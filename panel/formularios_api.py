"""
Rutas de los formularios para clientes (ver formularios_core.py).

Públicas (sin login — son las que abre el cliente):
  GET  /formulario/persona-fisica | /formulario/persona-juridica   la página
  GET  /api/publico/formulario/{slug}                              definición de campos
  POST /api/publico/formulario/{slug}                              envío (multipart)

Del panel (con login):
  GET    /api/formularios                    links + respuestas recibidas
  GET    /api/formularios/{id}               una respuesta completa
  POST   /api/formularios/{id}/estado        marcar revisada / nueva
  DELETE /api/formularios/{id}               borrar respuesta y archivos (no el cliente)
  GET    /api/formularios/archivos/{id}      descargar / ver un archivo
"""

import os
import threading
import datetime as _dt
import json
import time
from typing import Optional
from urllib.parse import quote
from collections import defaultdict, deque
import psycopg2.extras
import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

import formularios_core as fc
import mails_core
import poderes

STATIC = os.path.join(os.path.dirname(__file__), "static")
MAX_ENVIO = 32 * 1024 * 1024  # tope del pedido entero (3 archivos de 10 MB + texto)
ENVIOS_POR_HORA = 10          # por IP, contra spam / robots

# Tipos que el navegador puede mostrar sin riesgo (nunca HTML/SVG).
MIME_VER_EN_LINEA = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif"}


class CambioEstado(BaseModel):
    estado: str


class DatosPoder(BaseModel):
    tipo_persona: str
    otorgante: str
    lugar: str
    fecha: str
    aclaracion: Optional[str] = ""
    cargo: Optional[str] = ""


class Apoderado(BaseModel):
    texto: str


def leer_apoderado(cur) -> str:
    cur.execute("SELECT valor FROM vigilancia_config WHERE clave = 'poder_apoderado'")
    f = cur.fetchone()
    v = (f["valor"] if isinstance(f, dict) else f[0]) if f else None
    return v or poderes.APODERADO_DEFAULT


def _ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    return (xff.split(",")[0].strip() if xff else (request.client.host if request.client else "")) or "?"


_envios = defaultdict(deque)
_envios_lock = threading.Lock()


def _limite_envios(ip: str):
    ahora = time.time()
    with _envios_lock:
        q = _envios[ip]
        while q and ahora - q[0] > 3600:
            q.popleft()
        if len(q) >= ENVIOS_POR_HORA:
            raise HTTPException(status_code=429, detail="Recibimos muchos envíos desde tu conexión. Probá de nuevo en un rato.")
        q.append(ahora)


def respuestas_de(cur, donde: str, val) -> list:
    """Respuestas con sus archivos (sin el contenido) y las respuestas en orden
    legible. También la usa la ficha del cliente (cartera_api)."""
    cur.execute(f"SELECT * FROM formularios_respuestas WHERE {donde} ORDER BY recibido_en DESC", val)
    filas = cur.fetchall()
    if not filas:
        return []
    cur.execute("SELECT id, respuesta_id, campo, nombre, mime, tamano FROM formularios_archivos "
                "WHERE respuesta_id = ANY(%s) ORDER BY id", ([f["id"] for f in filas],))
    por_resp = {}
    for a in cur.fetchall():
        por_resp.setdefault(a["respuesta_id"], []).append(a)
    etiquetas = {s: {c["id"]: c["etiqueta"] for c in f["campos"]} for s, f in fc.FORMULARIOS.items()}
    for f in filas:
        f["archivos"] = por_resp.get(f["id"], [])
        for a in f["archivos"]:
            a["etiqueta"] = etiquetas.get(f["formulario"], {}).get(a["campo"], a["campo"])
        f["respuestas"] = [{"etiqueta": k, "valor": v} for k, v in
                           fc.respuestas_legibles(f["formulario"], f["datos"] or {})]
        f["tipo_legible"] = fc.TIPO_LEGIBLE.get(f["tipo_persona"], f["tipo_persona"])
    return filas


# ── Captcha (Cloudflare Turnstile) ─────────────────────────────────────────
# Se activa solo si Railway tiene TURNSTILE_SITE_KEY y TURNSTILE_SECRET_KEY.
# Sin esas variables el formulario sigue protegido por el campo trampa y el
# límite de envíos por conexión, pero sin captcha.
def _turnstile_activo() -> bool:
    return bool(os.environ.get("TURNSTILE_SITE_KEY") and os.environ.get("TURNSTILE_SECRET_KEY"))


def _captcha_ok(token: str, ip: str) -> bool:
    if not _turnstile_activo():
        return True
    if not token or len(token) > 2048:
        return False
    try:
        r = requests.post("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                          data={"secret": os.environ["TURNSTILE_SECRET_KEY"], "response": token, "remoteip": ip},
                          timeout=10)
        return bool(r.json().get("success"))
    except Exception as e:  # noqa: BLE001 — si Cloudflare no responde, no se le traba el envío al cliente
        print(f"[formularios] Turnstile no respondió ({e}); se acepta el envío (sigue el límite por conexión)")
        return True


def crear_router(verificar_login, conexion) -> APIRouter:
    router = APIRouter()

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def _aviso_en_segundo_plano(respuesta_id: int):
        # Cuenta, remitente y destinatarios: los de la pestaña Mails del panel.
        cfg_mail = mails_core.preparar("formularios")
        api_key = cfg_mail["api_key"]
        if not api_key:
            return  # lo manda el respaldo automático (scripts/notificar_formularios.py)
        try:
            with conexion() as conn:
                fc.avisar_respuesta(conn, respuesta_id, api_key, cfg_mail["remitente"],
                                    cfg_mail["destinatarios"], (os.environ.get("PANEL_URL") or fc.DEFAULT_PANEL).rstrip("/"))
        except Exception as e:  # noqa: BLE001 — queda pendiente para el respaldo
            print(f"[formularios] aviso de la respuesta {respuesta_id} no enviado: {e}")

    # ── Públicas ──────────────────────────────────────────────────────
    @router.get("/formulario/{slug}", include_in_schema=False)
    def pagina_formulario(slug: str):
        if slug not in fc.FORMULARIOS:
            raise HTTPException(status_code=404, detail="Formulario inexistente")
        r = FileResponse(os.path.join(STATIC, "formulario.html"))
        r.headers["Cache-Control"] = "no-cache"
        r.headers["X-Robots-Tag"] = "noindex"
        return r

    @router.get("/api/publico/formulario/{slug}")
    def definicion(slug: str):
        f = fc.FORMULARIOS.get(slug)
        if not f:
            raise HTTPException(status_code=404, detail="Formulario inexistente")
        return {"slug": slug, **{k: f[k] for k in ("titulo", "descripcion", "otro")},
                "campos": [{k: v for k, v in c.items() if k != "cliente"} for c in f["campos"]],
                "max_archivo": fc.MAX_ARCHIVO,
                "turnstile_site_key": os.environ.get("TURNSTILE_SITE_KEY") if _turnstile_activo() else None}

    @router.post("/api/publico/formulario/{slug}")
    async def enviar(slug: str, request: Request, background: BackgroundTasks):
        if slug not in fc.FORMULARIOS:
            raise HTTPException(status_code=404, detail="Formulario inexistente")
        try:
            largo = int(request.headers.get("content-length") or 0)
        except ValueError:
            largo = 0
        if largo > MAX_ENVIO:
            raise HTTPException(status_code=413, detail="Los archivos pesan demasiado (máximo 10 MB cada uno).")
        ip = _ip(request)
        _limite_envios(ip)
        form = await request.form(max_files=10, max_fields=60)
        if (form.get("sitio_web") or "").strip():  # campo trampa invisible: solo lo llenan los robots
            return {"ok": True}
        if not _captcha_ok(str(form.get("cf-turnstile-response") or ""), ip):
            return JSONResponse(status_code=400, content={"detail": "No pudimos verificar que no seas un robot. "
                                                          "Esperá a que aparezca el tilde de verificación y volvé a enviar."})
        valores, archivos = {}, {}
        for clave, valor in form.multi_items():
            if hasattr(valor, "read"):
                contenido = await valor.read(fc.MAX_ARCHIVO + 1)
                if contenido:
                    archivos[clave] = (valor.filename or "archivo", valor.content_type or "", contenido)
            else:
                valores[clave] = str(valor)
        try:
            datos, validos = fc.validar(slug, valores, archivos)
        except fc.ErrorFormulario as e:
            return JSONResponse(status_code=400, content={"detail": str(e)})
        with conexion() as conn:
            with rcur(conn) as cur:
                res = fc.guardar_respuesta(cur, slug, datos, validos, ip)
            conn.commit()
        background.add_task(_aviso_en_segundo_plano, res["respuesta_id"])
        return {"ok": True}

    # ── Del panel ─────────────────────────────────────────────────────
    @router.get("/api/formularios")
    def listar(estado: str = "", _: str = Depends(verificar_login)):
        cond, val = [], []
        if estado in ("nueva", "revisada"):
            cond.append("r.estado = %s")
            val.append(estado)
        donde = ("WHERE " + " AND ".join(cond)) if cond else ""
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute(
                    f"""
                    SELECT r.id, r.formulario, r.tipo_persona, r.estado, r.recibido_en, r.cliente_id, r.cliente_nuevo,
                           r.aviso_enviado_en, r.aviso_error, r.revisado_por, r.revisado_en,
                           r.datos->>'nombre' AS nombre, r.datos->>'cuit' AS cuit, r.datos->>'email' AS email,
                           r.datos->>'marca_nombre' AS marca, c.nombre AS cliente_nombre,
                           (SELECT COUNT(*) FROM formularios_archivos a WHERE a.respuesta_id = r.id) AS archivos
                    FROM formularios_respuestas r LEFT JOIN clientes c ON c.id = r.cliente_id
                    {donde} ORDER BY r.recibido_en DESC LIMIT 500
                    """,
                    val,
                )
                filas = cur.fetchall()
                cur.execute("SELECT COUNT(*) AS n FROM formularios_respuestas WHERE estado = 'nueva'")
                nuevas = cur.fetchone()["n"]
        return {"respuestas": filas, "nuevas": nuevas,
                "formularios": [{"slug": s, "titulo": f["titulo"], "tipo": fc.TIPO_LEGIBLE[f["tipo_persona"]],
                                 "ruta": f"/formulario/{s}"} for s, f in fc.FORMULARIOS.items()]}

    @router.get("/api/formularios/{rid}")
    def ver(rid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                filas = respuestas_de(cur, "id = %s", (rid,))
        if not filas:
            raise HTTPException(status_code=404, detail="No existe esa respuesta")
        return filas[0]

    @router.post("/api/formularios/{rid}/estado")
    def cambiar_estado(rid: int, body: CambioEstado, usuario: str = Depends(verificar_login)):
        if body.estado not in ("nueva", "revisada"):
            raise HTTPException(status_code=400, detail="Estado inválido")
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE formularios_respuestas SET estado = %s, revisado_por = %s, revisado_en = now() WHERE id = %s",
                    (body.estado, usuario if body.estado == "revisada" else None, rid),
                )
                if not cur.rowcount:
                    raise HTTPException(status_code=404, detail="No existe esa respuesta")
            conn.commit()
        return {"ok": True}

    @router.delete("/api/formularios/{rid}")
    def borrar(rid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM formularios_respuestas WHERE id = %s", (rid,))
                if not cur.rowcount:
                    raise HTTPException(status_code=404, detail="No existe esa respuesta")
            conn.commit()
        return {"ok": True}

    @router.get("/api/formularios/archivos/{aid}")
    def archivo(aid: int, ver: bool = False, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT nombre, mime, contenido FROM formularios_archivos WHERE id = %s", (aid,))
                a = cur.fetchone()
        if not a:
            raise HTTPException(status_code=404, detail="No existe ese archivo")
        en_linea = ver and a["mime"] in MIME_VER_EN_LINEA
        nombre = a["nombre"].replace('"', "")
        disp = f"{'inline' if en_linea else 'attachment'}; filename*=UTF-8''{quote(nombre)}"
        return Response(content=bytes(a["contenido"]), media_type=a["mime"],
                        headers={"Content-Disposition": disp, "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"})

    # ── Poder para firmar ─────────────────────────────────────────────
    def _archivo_poder(d: dict, apoderado: str, formato: str) -> Response:
        if formato == "docx":
            contenido = poderes.generar_docx(d, apoderado)
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            formato, contenido, mime = "pdf", poderes.generar_pdf(d, apoderado), "application/pdf"
        nombre = poderes.nombre_archivo(d, formato)
        return Response(content=contenido, media_type=mime,
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"})

    @router.get("/api/clientes/{cliente_id}/poder")
    def propuesta_poder(cliente_id: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT * FROM clientes WHERE id = %s", (cliente_id,))
                c = cur.fetchone()
                if not c:
                    raise HTTPException(status_code=404, detail="No existe ese cliente")
                apoderado = leer_apoderado(cur)
        return {"propuesta": poderes.propuesta(c, _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-3))).date()),
                "apoderado": apoderado}

    @router.post("/api/clientes/{cliente_id}/poder")
    def generar_poder(cliente_id: int, body: DatosPoder, formato: str = "pdf", usuario: str = Depends(verificar_login)):
        d = {k: (getattr(body, k) or "").strip() for k in DatosPoder.model_fields}
        if d["tipo_persona"] not in fc.TIPO_LEGIBLE:
            raise HTTPException(status_code=400, detail="Tipo de persona inválido")
        if not d["otorgante"] or not d["lugar"]:
            raise HTTPException(status_code=400, detail="Faltan los datos del otorgante o el lugar")
        if len(d["otorgante"]) > 1500 or any(len(d[k]) > 200 for k in ("lugar", "aclaracion", "cargo")):
            raise HTTPException(status_code=400, detail="Algún dato es demasiado largo")
        try:
            _dt.date.fromisoformat(d["fecha"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Fecha inválida")
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT id FROM clientes WHERE id = %s", (cliente_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="No existe ese cliente")
                apoderado = leer_apoderado(cur)
                cur.execute("INSERT INTO poderes_generados (cliente_id, datos, apoderado, generado_por) VALUES (%s, %s, %s, %s)",
                            (cliente_id, json.dumps(d, ensure_ascii=False), apoderado, usuario))
            conn.commit()
        return _archivo_poder(d, apoderado, formato)

    @router.get("/api/poderes/{pid}")
    def bajar_poder(pid: int, formato: str = "pdf", _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT datos, apoderado FROM poderes_generados WHERE id = %s", (pid,))
                f = cur.fetchone()
        if not f:
            raise HTTPException(status_code=404, detail="No existe ese poder")
        return _archivo_poder(f["datos"], f["apoderado"], formato)

    @router.delete("/api/poderes/{pid}")
    def borrar_poder(pid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM poderes_generados WHERE id = %s", (pid,))
            conn.commit()
        return {"ok": True}

    @router.get("/api/poderes-apoderado")
    def ver_apoderado(_: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                return {"texto": leer_apoderado(cur), "default": poderes.APODERADO_DEFAULT}

    @router.put("/api/poderes-apoderado")
    def guardar_apoderado(body: Apoderado, _: str = Depends(verificar_login)):
        t = " ".join((body.texto or "").split())
        if len(t) > 600:
            raise HTTPException(status_code=400, detail="El texto es demasiado largo")
        with conexion() as conn:
            with conn.cursor() as cur:
                if t:
                    cur.execute("INSERT INTO vigilancia_config (clave, valor) VALUES ('poder_apoderado', %s) "
                                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor", (t,))
                else:  # vacío = volver al texto original
                    cur.execute("DELETE FROM vigilancia_config WHERE clave = 'poder_apoderado'")
            conn.commit()
        return {"ok": True}

    return router

