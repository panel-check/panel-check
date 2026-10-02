"""
Rutas de la pestaña "Mails" (ver mails_core.py). Todas requieren login; guardar
cambios y tocar la lista de bajas, además, administrador.

  GET    /api/mails                         catálogo + configuración + cuentas
  POST   /api/mails/{clave}                 guardar cuenta / remitente / reply-to / destinatarios / plantilla
  POST   /api/mails/{clave}/restablecer     volver a los valores por defecto
  POST   /api/mails/{clave}/vista-previa    HTML de ejemplo (con la plantilla sin guardar, si se manda;
                                            con "acta", usa los datos reales de esa marca)
  GET    /api/mails/marcas-test?q=          buscar una marca real para probar las plantillas de prospectos
  POST   /api/mails/{clave}/test            mandar una PRUEBA a los mails que se escriban (nunca al de la marca)
  POST   /api/mails/{clave}/enviar          mandar la plantilla (tal como está guardada) a un lead, a mano
  GET    /api/mails/enviados?internos=      mails de la cuenta de prospectos de Resend (y de la interna, con
                                            internos=true), cruzados con el registro del panel y con su tipo
  GET    /api/mails/envios?actas=a,b        historial de mails mandados a esas actas (con aperturas y clics)
  POST   /api/publico/resend-webhook        avisos de Resend (entregado, abierto, clic, rebote, spam). Sin
                                            sesión: se valida la firma con RESEND_WEBHOOK_SECRET.
  GET    /api/mails/bajas                   lista de bajas (no se les escribe más)
  POST   /api/mails/bajas                   agregar una baja
  DELETE /api/mails/bajas/{email}           quitar una baja
"""

import os
from typing import Optional

import psycopg2.extras
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import mails_core as mc


class ConfigMail(BaseModel):
    cuenta: Optional[str] = None
    remitente: Optional[str] = None
    responder_a: Optional[str] = None
    destinatarios: Optional[str] = None
    asunto: Optional[str] = None
    cuerpo: Optional[str] = None
    encabezado: Optional[str] = None
    acta: Optional[str] = None


class EnvioTest(ConfigMail):
    para: str = ""


class EnvioLead(BaseModel):
    acta: str
    para: str
    confirmar_repetido: bool = False


class BajaNueva(BaseModel):
    email: str
    motivo: Optional[str] = None


def crear_router(verificar_login, verificar_admin, conexion, registrar_en_crm=None) -> APIRouter:
    """registrar_en_crm(cur, acta, usuario, texto): opcional, lo pasa app.py
    para dejar el envío como gestión «Mail» en el CRM del titular."""
    router = APIRouter()

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def _panel_url():
        return (os.environ.get("PANEL_URL") or mc.DEFAULT_PANEL).rstrip("/")

    def _guardadas(cur) -> dict:
        cur.execute("SELECT * FROM mails_config")
        return {f["clave"]: f for f in cur.fetchall()}

    @router.get("/api/mails")
    def listar(_: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            guardadas = _guardadas(cur)
        mails = []
        for clave, cat in mc.CATALOGO.items():
            cfg = mc.combinar(clave, guardadas.get(clave))
            cfg.pop("destinatarios_lista", None)
            if cfg.get("actualizado_en"):
                cfg["actualizado_en"] = cfg["actualizado_en"].isoformat()
            mails.append({
                "clave": clave, "grupo": cat["grupo"], "nombre": cat["nombre"], "descripcion": cat["descripcion"],
                "editable": bool(cat.get("editable")),
                "responder_a_automatico": cat.get("responder_a_automatico"),
                "config": cfg,
            })
        return {
            "cuentas": [{"id": k, "nombre": v["nombre"], "env": v["env"], "clave_cargada": bool(os.environ.get(v["env"]))}
                        for k, v in mc.CUENTAS.items()],
            "mails": mails,
            "variables": mc.VARIABLES_PROSPECTO,
            "pie_baja": mc.PIE_BAJA,
        }

    # ── Bajas (antes que /{clave}, para que no se confundan las rutas) ──
    @router.get("/api/mails/bajas")
    def listar_bajas(_: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("SELECT email, motivo, creada_en, creada_por FROM mails_bajas ORDER BY creada_en DESC")
            filas = cur.fetchall()
        for f in filas:
            f["creada_en"] = f["creada_en"].isoformat()
        return filas

    @router.post("/api/mails/bajas")
    def agregar_baja(body: BajaNueva, admin: str = Depends(verificar_admin)):
        email = body.email.strip().lower()
        if not mc._RE_EMAIL.match(email):
            raise HTTPException(status_code=400, detail="Mail inválido")
        with conexion() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mails_bajas (email, motivo, creada_por) VALUES (%s, %s, %s) "
                "ON CONFLICT (email) DO UPDATE SET motivo = EXCLUDED.motivo",
                (email, (body.motivo or "").strip()[:300] or None, admin),
            )
            conn.commit()
        return {"ok": True}

    @router.delete("/api/mails/bajas/{email}")
    def quitar_baja(email: str, _: str = Depends(verificar_admin)):
        with conexion() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM mails_bajas WHERE email = %s", (email.strip().lower(),))
            conn.commit()
        return {"ok": True}

    # ── Marca real para probar las plantillas de prospectos ─────────────
    @router.get("/api/mails/marcas-test")
    def buscar_marcas_test(q: str = "", _: str = Depends(verificar_login)):
        q = (q or "").strip()
        if len(q) < 2:
            return []
        with conexion() as conn, rcur(conn) as cur:
            cur.execute(
                """
                SELECT acta, clase, titular, email, COALESCE(NULLIF(denominacion_inpi, ''), denominacion) AS marca,
                       fecha_publicacion, tuvo_oposicion, detalle_oposicion
                  FROM marcas
                 WHERE acta = %(q)s OR denominacion ILIKE %(l)s OR denominacion_inpi ILIKE %(l)s OR titular ILIKE %(l)s
                 ORDER BY (acta = %(q)s) DESC, creado_en DESC
                 LIMIT 15
                """,
                {"q": q, "l": f"%{q}%"},
            )
            filas = cur.fetchall()
        for f in filas:
            if f.get("fecha_publicacion"):
                f["fecha_publicacion"] = f["fecha_publicacion"].isoformat()
        return filas

    def _datos_marca(acta):
        if not acta:
            return None
        with conexion() as conn, rcur(conn) as cur:
            d = mc.datos_de_marca(cur, acta)
        if not d:
            raise HTTPException(status_code=404, detail=f"No existe la marca con acta {acta}")
        return d

    # ── Avisos de Resend (webhook) ──────────────────────────────────────
    @router.post("/api/publico/resend-webhook", include_in_schema=False)
    async def resend_webhook(request: Request):
        secretos = [x for x in (os.environ.get("RESEND_WEBHOOK_SECRET") or "").split(",") if x.strip()]
        if not secretos:
            return JSONResponse({"detail": "Webhook sin configurar"}, status_code=503)
        cuerpo = await request.body()
        if len(cuerpo) > 200_000 or not mc.verificar_firma_webhook(cuerpo, request.headers, secretos):
            return JSONResponse({"detail": "Firma inválida"}, status_code=401)
        try:
            payload = json.loads(cuerpo)
        except ValueError:
            return JSONResponse({"detail": "JSON inválido"}, status_code=400)
        evento_id = request.headers.get("svix-id") or request.headers.get("webhook-id")
        with conexion() as conn, rcur(conn) as cur:
            tipo = mc.guardar_evento(cur, evento_id, payload)
            conn.commit()
        return {"ok": True, "tipo": tipo}

    # ── Enviados (pestaña por defecto de Mails) ─────────────────────────
    _SQL_CLAVE = ("COALESCE((SELECT cc.clave FROM crm_claves cc WHERE cc.acta = m.acta), NULLIF(trim(m.cuit), ''), "
                  "upper(regexp_replace(trim(m.titular), '\\s+', ' ', 'g')))")

    def _items_de_cuenta(cur, cuenta, lista):
        ids = [x.get("id") for x in lista if x.get("id")]
        cur.execute(
            f"""SELECT e.resend_id, e.clave_mail, e.acta, e.enviado_por, m.titular,
                       COALESCE(NULLIF(m.denominacion_inpi, ''), m.denominacion) AS marca, {_SQL_CLAVE} AS clave_titular
                  FROM mails_envios e LEFT JOIN marcas m ON m.acta = e.acta
                 WHERE e.resend_id = ANY(%s)""", (ids,))
        del_panel = {f["resend_id"]: f for f in cur.fetchall()}
        por_mail = {}
        if cuenta == "prospectos":
            # Mails que no salieron del panel: se busca el lead por el mail de destino.
            destinos = list({(x.get("to") or [""])[0].lower() for x in lista if x.get("id") not in del_panel})
            if destinos:
                cur.execute(
                    f"""SELECT DISTINCT ON (lower(m.email)) lower(m.email) AS email, m.acta, m.titular,
                               COALESCE(NULLIF(m.denominacion_inpi, ''), m.denominacion) AS marca, {_SQL_CLAVE} AS clave_titular
                          FROM marcas m WHERE lower(m.email) = ANY(%s)
                         ORDER BY lower(m.email), m.fecha_presentacion DESC NULLS LAST""", (destinos,))
                por_mail = {f["email"]: f for f in cur.fetchall()}
        eventos = mc.resumen_eventos(cur, ids)
        items = []
        for x in lista:
            para = ", ".join(x.get("to") or [])
            p = del_panel.get(x.get("id"))
            tipo = p["clave_mail"] if p else mc.tipo_por_asunto(x.get("subject"), cuenta)
            interno = tipo == "test" or mc.CATALOGO.get(tipo, {}).get("grupo") == "interno" or cuenta != "prospectos"
            lead = p or (None if interno else por_mail.get(((x.get("to") or [""])[0]).lower()))
            items.append({
                "id": x.get("id"), "cuenta": cuenta, "para": para, "asunto": x.get("subject"),
                "enviado_en": x.get("created_at"), "estado": x.get("last_event"), "desde_panel": bool(p),
                "tipo": tipo, "interno": interno,
                "plantilla": mc.CATALOGO.get(p["clave_mail"], {}).get("nombre") if p else None,
                "enviado_por": p["enviado_por"] if p else None,
                "acta": lead["acta"] if lead else None, "marca": lead["marca"] if lead else None,
                "titular": lead["titular"] if lead else None,
                "clave_titular": lead["clave_titular"] if lead else None,
                "seguimiento": eventos.get(x.get("id")),
            })
        return items

    @router.get("/api/mails/enviados")
    def listar_enviados(despues_prospectos: str = "", despues_interna: str = "", internos: bool = False,
                        solo: str = "", _: str = Depends(verificar_login)):
        """Mails de la cuenta de prospectos de Resend (y, con internos=true, también
        los de la cuenta interna), del más nuevo al más viejo. `solo` limita a una
        cuenta (para «Cargar más» cuando la otra ya no tiene más)."""
        avisos, items, siguiente = [], [], {}
        cuentas = ["prospectos"] + (["interna"] if internos else [])
        if solo in cuentas:
            cuentas = [solo]
        despues = {"prospectos": despues_prospectos, "interna": despues_interna}
        with conexion() as conn, rcur(conn) as cur:
            for cuenta in cuentas:
                try:
                    r = mc.listar_resend(cuenta, 50, despues[cuenta] or None)
                except ValueError as e:
                    avisos.append(f"Cuenta {mc.CUENTAS[cuenta]['nombre']}: {e}")
                    if cuenta == "prospectos" and not despues[cuenta]:
                        items.extend(_respaldo_panel(cur))
                    continue
                items.extend(_items_de_cuenta(cur, cuenta, r["data"]))
                if r["has_more"] and r["data"]:
                    siguiente[cuenta] = r["data"][-1]["id"]
        items.sort(key=lambda it: it.get("enviado_en") or "", reverse=True)
        return {"avisos": avisos, "items": items, "siguiente": siguiente, "tipos": mc.tipos_de_mail()}

    def _respaldo_panel(cur):
        """Si no se puede listar Resend: lo que se mandó desde el panel."""
        cur.execute(
            f"""SELECT e.resend_id AS id, e.para, e.asunto, e.enviado_en, e.clave_mail, e.acta, e.enviado_por,
                       m.titular, COALESCE(NULLIF(m.denominacion_inpi, ''), m.denominacion) AS marca, {_SQL_CLAVE} AS clave_titular
                  FROM mails_envios e LEFT JOIN marcas m ON m.acta = e.acta
                 ORDER BY e.enviado_en DESC LIMIT 200""")
        filas = cur.fetchall()
        eventos = mc.resumen_eventos(cur, [f["id"] for f in filas])
        return [{
            "id": f["id"], "cuenta": "prospectos", "para": f["para"], "asunto": f["asunto"],
            "enviado_en": f["enviado_en"].isoformat(), "estado": None, "desde_panel": True,
            "tipo": f["clave_mail"], "interno": False,
            "plantilla": mc.CATALOGO.get(f["clave_mail"], {}).get("nombre", f["clave_mail"]),
            "enviado_por": f["enviado_por"], "acta": f["acta"], "marca": f["marca"], "titular": f["titular"],
            "clave_titular": f["clave_titular"], "seguimiento": eventos.get(f["id"]),
        } for f in filas]

    # ── Envío manual a un lead ──────────────────────────────────────────
    @router.get("/api/mails/envios")
    def listar_envios(actas: str = "", _: str = Depends(verificar_login)):
        lista = [a.strip() for a in actas.split(",") if a.strip()][:500]
        if not lista:
            return []
        with conexion() as conn, rcur(conn) as cur:
            cur.execute(
                "SELECT id, clave_mail, acta, para, asunto, resend_id, enviado_por, enviado_en FROM mails_envios "
                "WHERE acta = ANY(%s) ORDER BY enviado_en DESC LIMIT 200", (lista,))
            filas = cur.fetchall()
            eventos = mc.resumen_eventos(cur, [f["resend_id"] for f in filas])
        for f in filas:
            f["seguimiento"] = eventos.get(f.pop("resend_id"))
            f["enviado_en"] = f["enviado_en"].isoformat()
            f["plantilla"] = mc.CATALOGO.get(f["clave_mail"], {}).get("nombre", f["clave_mail"])
        return filas

    @router.post("/api/mails/{clave}/enviar")
    def enviar_a_lead(clave: str, body: EnvioLead, usuario: str = Depends(verificar_login)):
        """Manda la plantilla de prospectos, tal como está GUARDADA, con los
        datos reales de la marca, al mail indicado. No manda nada si ese mail
        está en la lista de bajas; si ya se le mandó esa plantilla a esa acta,
        pide confirmación (409) antes de repetir."""
        cat = mc.CATALOGO.get(clave)
        if not cat or not cat.get("editable") or cat["grupo"] != "prospectos":
            raise HTTPException(status_code=400, detail="Solo se pueden mandar plantillas de prospectos")
        para = mc.lista_de_emails(body.para)
        if len(para) != 1 or not mc._RE_EMAIL.match(para[0]):
            raise HTTPException(status_code=400, detail="Escribí un único mail válido de destino")
        destino = para[0].lower()
        with conexion() as conn, rcur(conn) as cur:
            datos = mc.datos_de_marca(cur, body.acta)
            if not datos:
                raise HTTPException(status_code=404, detail=f"No existe la marca con acta {body.acta}")
            if mc.esta_de_baja(cur, destino):
                raise HTTPException(status_code=400, detail=f"{destino} está en la lista de bajas: no se le escribe más.")
            cur.execute("SELECT enviado_en, enviado_por FROM mails_envios WHERE clave_mail = %s AND acta = %s "
                        "ORDER BY enviado_en DESC LIMIT 1", (clave, body.acta))
            previo = cur.fetchone()
            cfg = mc.combinar(clave, _guardadas(cur).get(clave))
        if previo and not body.confirmar_repetido:
            cuando = previo["enviado_en"].astimezone().strftime("%d/%m/%Y %H:%M")
            raise HTTPException(status_code=409, detail=f"Esta plantilla ya se le mandó a esta marca el {cuando}"
                                                         f"{' (por ' + previo['enviado_por'] + ')' if previo.get('enviado_por') else ''}.")
        asunto, cuerpo_html, texto = mc.render_prospecto(cfg["asunto"], cfg["cuerpo"], datos, cfg.get("encabezado"))
        try:
            id_resend = mc.enviar(cfg["cuenta"], cfg["remitente"], cfg["responder_a"], [destino], asunto, cuerpo_html, texto)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("INSERT INTO mails_envios (clave_mail, acta, para, asunto, resend_id, enviado_por) "
                        "VALUES (%s, %s, %s, %s, %s, %s)", (clave, body.acta, destino, asunto, id_resend, usuario))
            conn.commit()
            if registrar_en_crm:
                try:
                    registrar_en_crm(cur, body.acta, usuario,
                                     f"Mail enviado desde el panel: «{cat['nombre']}» a {destino} "
                                     f"(acta {body.acta}). Asunto: {asunto}")
                    conn.commit()
                except Exception as e:  # noqa: BLE001 — el mail ya salió; el CRM no debe hacerlo fallar
                    conn.rollback()
                    print(f"[mails] no se pudo registrar el envío en el CRM: {e}")
        return {"ok": True, "id": id_resend, "para": destino, "asunto": asunto}

    # ── Configuración de cada mail ──────────────────────────────────────
    @router.post("/api/mails/{clave}")
    def guardar(clave: str, body: ConfigMail, admin: str = Depends(verificar_admin)):
        if clave not in mc.CATALOGO:
            raise HTTPException(status_code=404, detail="Tipo de mail inexistente")
        try:
            v = mc.validar_config(clave, body.model_dump() if hasattr(body, "model_dump") else body.dict())
            mc.validar_dominio(v["cuenta"], v["remitente"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        with conexion() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mails_config (clave, cuenta, remitente, responder_a, destinatarios, asunto, cuerpo, encabezado, actualizado_en, actualizado_por)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), %s)
                ON CONFLICT (clave) DO UPDATE SET cuenta = EXCLUDED.cuenta, remitente = EXCLUDED.remitente,
                    responder_a = EXCLUDED.responder_a, destinatarios = EXCLUDED.destinatarios,
                    asunto = EXCLUDED.asunto, cuerpo = EXCLUDED.cuerpo, encabezado = EXCLUDED.encabezado,
                    actualizado_en = now(), actualizado_por = EXCLUDED.actualizado_por
                """,
                (clave, v["cuenta"], v["remitente"], v["responder_a"], v["destinatarios"],
                 v.get("asunto"), v.get("cuerpo"), v.get("encabezado"), admin),
            )
            conn.commit()
        return {"ok": True}

    @router.post("/api/mails/{clave}/restablecer")
    def restablecer(clave: str, _: str = Depends(verificar_admin)):
        if clave not in mc.CATALOGO:
            raise HTTPException(status_code=404, detail="Tipo de mail inexistente")
        with conexion() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM mails_config WHERE clave = %s", (clave,))
            conn.commit()
        return {"ok": True}

    @router.post("/api/mails/{clave}/vista-previa")
    def vista_previa(clave: str, body: ConfigMail, _: str = Depends(verificar_login)):
        if clave not in mc.CATALOGO:
            raise HTTPException(status_code=404, detail="Tipo de mail inexistente")
        with conexion() as conn, rcur(conn) as cur:
            cfg = mc.combinar(clave, _guardadas(cur).get(clave))
        if mc.CATALOGO[clave].get("editable"):
            asunto = body.asunto if body.asunto is not None else cfg["asunto"]
            cuerpo = body.cuerpo if body.cuerpo is not None else cfg["cuerpo"]
            desconocidas = (mc.variables_usadas(asunto) | mc.variables_usadas(cuerpo)) - set(mc.VARIABLES_PROSPECTO)
            if desconocidas:
                raise HTTPException(status_code=400, detail="Variable inexistente: {{" + sorted(desconocidas)[0] + "}}")
            cfg["asunto"], cfg["cuerpo"] = asunto, cuerpo
            if body.encabezado is not None:
                cfg["encabezado"] = " ".join(body.encabezado.split())[:60] or mc.CATALOGO[clave].get("encabezado")
        return mc.vista_previa(clave, cfg, _panel_url(), _datos_marca(body.acta) if mc.CATALOGO[clave].get("editable") else None)

    @router.post("/api/mails/{clave}/test")
    def enviar_test(clave: str, body: EnvioTest, _: str = Depends(verificar_admin)):
        """Prueba de una plantilla de prospectos, tal como está en pantalla
        (sin guardar), con los datos reales de la marca elegida. Va SOLO a los
        mails escritos en "Enviar test a"; el de la marca no se usa nunca."""
        if clave not in mc.CATALOGO:
            raise HTTPException(status_code=404, detail="Tipo de mail inexistente")
        if not mc.CATALOGO[clave].get("editable"):
            raise HTTPException(status_code=400, detail="La prueba con datos reales es para las plantillas de prospectos")
        para = mc.lista_de_emails(body.para)
        if not para:
            raise HTTPException(status_code=400, detail="Escribí al menos un mail en «Enviar test a»")
        if len(para) > 5:
            raise HTTPException(status_code=400, detail="Como máximo 5 mails de prueba por vez")
        malos = [d for d in para if not mc._RE_EMAIL.match(d)]
        if malos:
            raise HTTPException(status_code=400, detail=f"Mail de prueba inválido: {malos[0]}")
        try:
            v = mc.validar_config(clave, body.model_dump() if hasattr(body, "model_dump") else body.dict())
            mc.validar_dominio(v["cuenta"], v["remitente"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        datos = _datos_marca(body.acta) or mc.EJEMPLO_PROSPECTO
        asunto, cuerpo_html, texto = mc.render_prospecto(v["asunto"], v["cuerpo"], datos, v.get("encabezado"))
        try:
            id_resend = mc.enviar_test(v["cuenta"], v["remitente"], v["responder_a"], para, asunto, cuerpo_html, texto)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "id": id_resend, "para": para, "asunto": "[TEST] " + asunto}

    return router
