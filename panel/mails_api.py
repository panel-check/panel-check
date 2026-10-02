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
  GET    /api/mails/bajas                   lista de bajas (no se les escribe más)
  POST   /api/mails/bajas                   agregar una baja
  DELETE /api/mails/bajas/{email}           quitar una baja
"""

import os
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
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


class BajaNueva(BaseModel):
    email: str
    motivo: Optional[str] = None


def crear_router(verificar_login, verificar_admin, conexion) -> APIRouter:
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
