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
  GET    /api/mails/cupo-hoy                mails mandados hoy por cuenta de Resend vs. el límite diario (100)
  GET    /api/mails/envios?actas=a,b       historial de mails mandados a esas actas (con aperturas y clics)
  GET    /api/mails/envios/{id}/adjunto     el PDF del presupuesto que se mandó en ese envío (se arma de nuevo con sus datos)
  GET    /api/mails/{clave}/presupuesto     datos del presupuesto (montos, vigencia, transferencia) y qué falta cargar
  POST   /api/mails/{clave}/presupuesto     guardar los datos del presupuesto (administrador)
  POST   /api/mails/{clave}/presupuesto/pdf PDF de vista previa (con los datos guardados + los que se manden sin guardar)
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
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

import mails_core as mc
import presupuestos as pres


class ConfigMail(BaseModel):
    cuenta: Optional[str] = None
    remitente: Optional[str] = None
    responder_a: Optional[str] = None
    destinatarios: Optional[str] = None
    asunto: Optional[str] = None
    cuerpo: Optional[str] = None
    encabezado: Optional[str] = None
    acta: Optional[str] = None
    # Solo plantillas con PDF adjunto (presupuestos): ajustes de este envío / vista previa.
    presupuesto: Optional[dict] = None


class EnvioTest(ConfigMail):
    para: str = ""


class EnvioLead(BaseModel):
    acta: str
    para: str
    confirmar_repetido: bool = False
    presupuesto: Optional[dict] = None   # ajustes de este envío: honorarios, tasas, alcance, vigencia_dias


class DatosPresupuesto(BaseModel):
    presupuesto: Optional[dict] = None


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
                "adjunto": cat.get("adjunto"),
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

    # ── Presupuestos (mails con PDF adjunto) ────────────────────────────
    def _tipo_presupuesto(clave):
        """Tipo de presupuesto (p. ej. «registro_marca») si esa plantilla lleva PDF adjunto; si no, 404."""
        tipo = (mc.CATALOGO.get(clave) or {}).get("adjunto")
        if not tipo:
            raise HTTPException(status_code=404, detail="Esta plantilla no lleva un presupuesto adjunto")
        return tipo

    def _datos_presupuesto(tipo, ajustes=None, completos=None, exigir_completo=False):
        """Snapshot con el que se arma el PDF: lo guardado en Mails, más `ajustes`
        (solo lo ajustable por envío) o `completos` (cualquier campo, para la vista
        previa de la pestaña Mails). Con exigir_completo, falla si falta cargar algo."""
        with conexion() as conn, rcur(conn) as cur:
            cfg = pres.leer_config(cur, tipo)
        try:
            if completos is not None:
                cfg = {**cfg, **pres.validar(completos)}
            else:
                cfg = pres.con_ajustes(cfg, ajustes)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if exigir_completo:
            faltan = pres.faltantes(cfg)
            if faltan:
                raise HTTPException(status_code=400, detail="Antes de mandar un presupuesto hay que cargar, en Mails → "
                                    "«Datos del presupuesto»: " + ", ".join(faltan) + ".")
        return pres.armar_datos(cfg)

    def _respuesta_pdf(datos, descargar=False):
        contenido = pres.generar_pdf(datos)
        nombre = pres.nombre_archivo(datos)
        return Response(content=contenido, media_type="application/pdf",
                        headers={"Content-Disposition": f"{'attachment' if descargar else 'inline'}; filename*=UTF-8''{quote(nombre)}",
                                 "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/api/mails/{clave}/presupuesto")
    def ver_presupuesto(clave: str, _: str = Depends(verificar_login)):
        tipo = _tipo_presupuesto(clave)
        with conexion() as conn, rcur(conn) as cur:
            cfg = pres.leer_config(cur, tipo)
        return {"tipo": tipo, "config": cfg, "faltan": pres.faltantes(cfg), "etiquetas": pres.ETIQUETAS,
                "ajustables": list(pres.AJUSTABLES_POR_ENVIO)}

    @router.post("/api/mails/{clave}/presupuesto")
    def guardar_presupuesto(clave: str, body: DatosPresupuesto, _: str = Depends(verificar_admin)):
        tipo = _tipo_presupuesto(clave)
        try:
            with conexion() as conn, rcur(conn) as cur:
                cfg = pres.guardar_config(cur, tipo, body.presupuesto or {})
                conn.commit()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "config": cfg, "faltan": pres.faltantes(cfg)}

    @router.post("/api/mails/{clave}/presupuesto/pdf")
    def pdf_presupuesto(clave: str, body: DatosPresupuesto, _: str = Depends(verificar_login)):
        """Vista previa del PDF. Acepta cualquier campo del presupuesto (aunque no esté
        guardado) para probar desde la pestaña Mails; lo que falte de transferencia sale como «—»."""
        tipo = _tipo_presupuesto(clave)
        return _respuesta_pdf(_datos_presupuesto(tipo, completos=body.presupuesto or {}))

    # ── Links de los mails: anotar el clic y redirigir ──────────────────
    @router.get("/r/{token}", include_in_schema=False)
    def redirigir_link(token: str, request: Request):
        """Público (lo abre el lead desde el mail). Si el token no existe, va a la
        web de Smarties en vez de dar error."""
        destino = None
        if 6 <= len(token) <= 40:
            try:
                with conexion() as conn, rcur(conn) as cur:
                    destino = mc.registrar_clic(cur, token, request.headers.get("user-agent", ""))
                    conn.commit()
            except Exception as e:  # noqa: BLE001 — si falla la base, igual se redirige
                print(f"[mails] no se pudo registrar el clic {token}: {e}")
                with conexion() as conn, rcur(conn) as cur:
                    cur.execute("SELECT url FROM mails_links WHERE token = %s", (token,))
                    f = cur.fetchone()
                    destino = f["url"] if f else None
        r = RedirectResponse(destino or "https://www.smartiesconsultora.com.ar", status_code=302)
        r.headers["Cache-Control"] = "no-store"
        r.headers["X-Robots-Tag"] = "noindex"
        return r

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

    @router.get("/api/mails/cupo-hoy")
    def cupo_hoy(_: str = Depends(verificar_login)):
        """Mails mandados hoy (día UTC) por cada cuenta de Resend, contra el límite diario del plan free."""
        out = {}
        for cuenta in ("prospectos", "interna"):
            try:
                out[cuenta] = mc.contar_enviados_hoy(cuenta)
            except ValueError as e:
                out[cuenta] = {"error": str(e)}
        return out

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
                "SELECT id, clave_mail, acta, para, asunto, resend_id, enviado_por, enviado_en, detalle FROM mails_envios "
                "WHERE acta = ANY(%s) ORDER BY enviado_en DESC LIMIT 200", (lista,))
            filas = cur.fetchall()
            eventos = mc.resumen_eventos(cur, [f["resend_id"] for f in filas])
        for f in filas:
            f["seguimiento"] = eventos.get(f.pop("resend_id"))
            f["enviado_en"] = f["enviado_en"].isoformat()
            f["plantilla"] = mc.CATALOGO.get(f["clave_mail"], {}).get("nombre", f["clave_mail"])
            det = f.pop("detalle", None)
            # Mails con presupuesto: qué se cotizó y un link para volver a ver el PDF.
            f["presupuesto"] = {"resumen": pres.resumen(det)} if isinstance(det, dict) and det.get("honorarios") else None
        return filas

    @router.get("/api/mails/envios/{envio_id}/adjunto")
    def adjunto_de_envio(envio_id: int, _: str = Depends(verificar_login)):
        """El PDF que se mandó en ese envío, armado de nuevo con los datos guardados (es el mismo)."""
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("SELECT detalle FROM mails_envios WHERE id = %s", (envio_id,))
            f = cur.fetchone()
        det = f and f.get("detalle")
        if not isinstance(det, dict) or not det.get("honorarios"):
            raise HTTPException(status_code=404, detail="Ese envío no tiene un presupuesto adjunto")
        return _respuesta_pdf(det)

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
        # Presupuesto: se arma el PDF con los datos guardados + los ajustes de este envío.
        adjuntos, detalle = None, None
        if cat.get("adjunto"):
            detalle = _datos_presupuesto(cat["adjunto"], ajustes=body.presupuesto, exigir_completo=True)
            datos = {**datos, **pres.variables_mail(detalle)}
            adjuntos = [{"filename": pres.nombre_archivo(detalle), "content": pres.generar_pdf(detalle)}]
        simple = mc.es_simple(clave)
        asunto, cuerpo_html, texto = mc.render_prospecto(cfg["asunto"], cfg["cuerpo"], datos, cfg.get("encabezado"), simple=simple)
        envio_token = mc.nuevo_token()
        if not simple:   # el estilo simple no reescribe links: un mail con links «de rastreo» tiende a ir a Promociones
            with conexion() as conn, rcur(conn) as cur:
                cuerpo_html, texto = mc.rastrear_links(cur, cuerpo_html, texto, envio_token, _panel_url())
                conn.commit()
        try:
            id_resend = mc.enviar(cfg["cuenta"], cfg["remitente"], cfg["responder_a"], [destino], asunto, cuerpo_html, texto, adjuntos)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("INSERT INTO mails_envios (clave_mail, acta, para, asunto, resend_id, enviado_por, token, detalle) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (clave, body.acta, destino, asunto, id_resend, usuario, envio_token,
                         psycopg2.extras.Json(detalle) if detalle else None))
            conn.commit()
            if registrar_en_crm:
                try:
                    if detalle:
                        texto_crm = (f"Presupuesto enviado desde el panel: «{cat['nombre']}» a {destino} (acta {body.acta}): "
                                     f"{pres.resumen(detalle)}. Asunto: {asunto}")
                    else:
                        texto_crm = (f"Mail enviado desde el panel: «{cat['nombre']}» a {destino} "
                                     f"(acta {body.acta}). Asunto: {asunto}")
                    registrar_en_crm(cur, body.acta, usuario, texto_crm)
                    conn.commit()
                except Exception as e:  # noqa: BLE001 — el mail ya salió; el CRM no debe hacerlo fallar
                    conn.rollback()
                    print(f"[mails] no se pudo registrar el envío en el CRM: {e}")
        return {"ok": True, "id": id_resend, "para": destino, "asunto": asunto,
                "adjunto": adjuntos[0]["filename"] if adjuntos else None}

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
        datos = _datos_marca(body.acta) if mc.CATALOGO[clave].get("editable") else None
        tipo_pres = mc.CATALOGO[clave].get("adjunto")
        if tipo_pres:
            # Montos y vigencia reales (los guardados + lo que haya escrito en pantalla) en vez de los de ejemplo.
            det = _datos_presupuesto(tipo_pres, completos=body.presupuesto or {})
            datos = {**(datos or mc.EJEMPLO_PROSPECTO), **pres.variables_mail(det)}
        return mc.vista_previa(clave, cfg, _panel_url(), datos)

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
        adjuntos = None
        if mc.CATALOGO[clave].get("adjunto"):
            # La prueba lleva también el PDF, con lo que hay en pantalla (lo que falte de transferencia sale como «—»).
            det = _datos_presupuesto(mc.CATALOGO[clave]["adjunto"], completos=body.presupuesto or {})
            datos = {**datos, **pres.variables_mail(det)}
            adjuntos = [{"filename": pres.nombre_archivo(det), "content": pres.generar_pdf(det)}]
        simple = mc.es_simple(clave)
        asunto, cuerpo_html, texto = mc.render_prospecto(v["asunto"], v["cuerpo"], datos, v.get("encabezado"), simple=simple)
        if not simple:
            with conexion() as conn, rcur(conn) as cur:
                cuerpo_html, texto = mc.rastrear_links(cur, cuerpo_html, texto, mc.nuevo_token(), _panel_url())
                conn.commit()
        try:
            id_resend = mc.enviar_test(v["cuenta"], v["remitente"], v["responder_a"], para, asunto, cuerpo_html, texto, adjuntos)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "id": id_resend, "para": para, "asunto": "[TEST] " + asunto}

    return router
