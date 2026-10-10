"""
Rutas del verificador de marca de la web (ver verificador_core.py).

Públicas (sin login — las llama el formulario del home de smartiesconsultora.com.ar,
que está en otro dominio, por eso llevan CORS solo para ese sitio):
  POST /api/publico/verificador/consulta             verifica una marca y guarda la consulta con los datos de contacto
  GET  /api/publico/verificador/{codigo}             recupera un resultado guardado (vale 90 días)
  POST /api/publico/verificador/consulta-adicional   una pregunta extra sobre una marca ya verificada
  OPTIONS /api/publico/verificador/...               la verificación previa (preflight) del navegador

Del panel (con login, sección «Consultas web» del menú Más):
  GET    /api/consultas-web              lista (filtros: estado, resultado, texto)
  GET    /api/consultas-web/{id}         una consulta completa (marcas encontradas y preguntas adicionales)
  POST   /api/consultas-web/{id}/estado  marcar revisada / nueva
  DELETE /api/consultas-web/{id}         borrar la consulta

Protecciones de la parte pública: límite de consultas por IP, campo trampa, captcha de
Cloudflare Turnstile (si el panel lo tiene activado), caché de 24 hs por marca y un tope
global de búsquedas a INPI por minuto. Los pedidos de otros dominios se rechazan salvo los
de VERIFICADOR_ORIGENES (ver auth.origen_valido).
"""

import json
import os
import threading
import time
from collections import defaultdict, deque
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

import auth
import formularios_api
import mails_core
import verificador_core as vc

MAX_CUERPO = 16 * 1024        # bytes: un pedido legítimo pesa unos cientos
CONSULTAS_POR_HORA = 10       # por IP
ADICIONALES_POR_HORA = 5      # por IP

_usos = defaultdict(lambda: defaultdict(deque))
_usos_lock = threading.Lock()


def _permitido(bolsa: str, ip: str, maximo: int, ventana: int = 3600) -> bool:
    """True si esa IP todavía puede hacer otro pedido de ese tipo; si sí, lo anota."""
    ahora = time.time()
    with _usos_lock:
        q = _usos[bolsa][ip]
        while q and ahora - q[0] > ventana:
            q.popleft()
        if len(q) >= maximo:
            return False
        q.append(ahora)
        return True


def _cors(request: Request) -> dict:
    h = {"Vary": "Origin"}
    origen = vc.origen_de(request.headers.get("origin") or "")
    if origen and origen in vc.origenes_permitidos():
        h["Access-Control-Allow-Origin"] = origen
        h["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        h["Access-Control-Allow-Headers"] = "Content-Type"
        h["Access-Control-Max-Age"] = "600"
    return h


def _json(request: Request, cuerpo, status: int = 200) -> JSONResponse:
    h = _cors(request)
    h["Cache-Control"] = "no-store"
    h["X-Robots-Tag"] = "noindex"
    return JSONResponse(status_code=status, content=cuerpo, headers=h)


async def _leer_json(request: Request):
    """El cuerpo como dict, o None si es demasiado grande o no es un JSON válido."""
    try:
        if int(request.headers.get("content-length") or 0) > MAX_CUERPO:
            return None
    except ValueError:
        return None
    crudo = await request.body()
    if len(crudo) > MAX_CUERPO:
        return None
    try:
        data = json.loads(crudo or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _panel_url() -> str:
    return (os.environ.get("PANEL_URL") or mails_core.DEFAULT_PANEL).rstrip("/")


def _muestras_publicas(muestras) -> list:
    """Lo que se le muestra a la persona: solo nombre y clase, sin actas ni estados."""
    out = []
    for m in muestras or []:
        clase = vc._clase_entera(m.get("clase"))
        if clase is None:
            continue
        out.append({"denominacion": m.get("denominacion") or "", "clase": clase})
        if len(out) >= vc.MUESTRAS_PUBLICAS:
            break
    return out


def crear_router(verificar_login, conexion) -> APIRouter:
    router = APIRouter()

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── Búsqueda con caché ────────────────────────────────────────────
    def _filas_para(marca: str):
        """(filas de INPI, vino_del_caché). Levanta vc.ErrorInpi si INPI no respondió."""
        clave = vc.clave_cache(marca)
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT filas FROM verificaciones_marca_cache "
                    "WHERE clave = %s AND consultada_en > now() - make_interval(hours => %s)",
                    (clave, vc.CACHE_HORAS),
                )
                fila = cur.fetchone()
        if fila:
            return fila[0], True
        filas = vc.buscar_inpi(marca)
        if filas:  # una lista vacía no se guarda: si INPI contestó mal, no se repite el error 24 hs
            with conexion() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO verificaciones_marca_cache (clave, filas, consultada_en) VALUES (%s, %s, now()) "
                        "ON CONFLICT (clave) DO UPDATE SET filas = EXCLUDED.filas, consultada_en = now()",
                        (clave, psycopg2.extras.Json(filas)),
                    )
                conn.commit()
        return filas, False

    def _genericas() -> frozenset:
        """Palabras genéricas de la vigilancia (pestaña Ajustes de Clientes). Si no se pueden leer, no se usan."""
        try:
            with conexion() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT palabra FROM vigilancia_palabras_genericas")
                    return frozenset(str(f[0]).upper() for f in cur.fetchall())
        except Exception as e:  # noqa: BLE001
            print(f"[verificador] no se pudieron leer las palabras genéricas: {e}")
            return frozenset()

    # ── Avisos por mail al equipo ─────────────────────────────────────
    def _enviar_aviso(asunto: str, cuerpo_html: str, texto: str, responder_a: str) -> Optional[str]:
        """None si salió bien; si no, el motivo (queda guardado para verlo en el panel)."""
        cfg = mails_core.preparar("consultas_web")
        if not cfg["destinatarios"]:
            return "No hay destinatarios configurados (pestaña Mails)."
        try:
            mails_core.enviar(cfg["cuenta"], cfg["remitente"], responder_a or cfg["responder_a"], cfg["destinatarios"],
                              asunto, cuerpo_html, texto)
        except Exception as e:  # noqa: BLE001 — la consulta ya está guardada: el aviso fallido se ve en el panel
            return str(e)[:500]
        return None

    def _aviso_consulta(vid: int):
        try:
            with conexion() as conn:
                with rcur(conn) as cur:
                    cur.execute("SELECT * FROM verificaciones_marca WHERE id = %s", (vid,))
                    v = cur.fetchone()
            if not v:
                return
            cuerpo, texto = vc.armar_mail_aviso(v, _panel_url())
            error = _enviar_aviso(vc.asunto_aviso(v["marca"], v["veredicto"]), cuerpo, texto, v["email"])
            with conexion() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE verificaciones_marca SET aviso_enviado_en = CASE WHEN %s IS NULL THEN now() END, aviso_error = %s WHERE id = %s",
                        (error, error, vid),
                    )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[verificador] aviso de la consulta {vid} no enviado: {e}")

    def _aviso_adicional(cid: int):
        try:
            with conexion() as conn:
                with rcur(conn) as cur:
                    cur.execute(
                        "SELECT c.*, v.codigo FROM verificaciones_marca_consultas c "
                        "LEFT JOIN verificaciones_marca v ON v.id = c.verificacion_id WHERE c.id = %s",
                        (cid,),
                    )
                    c = cur.fetchone()
            if not c:
                return
            cuerpo, texto = vc.armar_mail_adicional(c, c["marca"], c["codigo"] or "", _panel_url())
            error = _enviar_aviso(vc.asunto_adicional(c["marca"]), cuerpo, texto, c["email"])
            with conexion() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE verificaciones_marca_consultas SET aviso_enviado_en = CASE WHEN %s IS NULL THEN now() END, aviso_error = %s WHERE id = %s",
                        (error, error, cid),
                    )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[verificador] aviso de la consulta adicional {cid} no enviado: {e}")

    # ── Públicas ──────────────────────────────────────────────────────
    @router.options("/api/publico/verificador/{resto:path}", include_in_schema=False)
    def preflight(resto: str, request: Request):
        return Response(status_code=204, headers=_cors(request))

    def _procesar_consulta(raw: dict, ip: str, origen: str):
        """(status, cuerpo, id de la fila para el aviso o None)."""
        if not _permitido("consulta", ip, CONSULTAS_POR_HORA):
            return 429, {"detail": "Recibimos muchas consultas desde tu conexión. Probá de nuevo en un rato."}, None
        if str(raw.get("hp") or "").strip():  # campo trampa invisible: solo lo llenan los robots
            return 200, {"public": {"verdict": "error", "exactCount": 0, "similarCount": 0, "samples": []}, "consultaId": None}, None
        lead = raw.get("lead") if isinstance(raw.get("lead"), dict) else {}
        try:
            d = vc.validar_lead(lead.get("marca") or raw.get("nombre"), lead.get("actividad"), lead.get("nombre"),
                                lead.get("email"), lead.get("telefono"), lead.get("web"))
        except ValueError as e:
            return 400, {"detail": str(e)}, None
        if not formularios_api._captcha_ok(str(raw.get("token") or ""), ip):
            return 400, {"detail": "No pudimos verificar que no seas un robot. Esperá a que aparezca el tilde de "
                                   "verificación y volvé a enviar."}, None

        # 1) se guarda la consulta ya (si INPI tarda o falla, el contacto no se pierde)
        codigo = vc.nuevo_codigo()
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO verificaciones_marca (codigo, marca, actividad, nombre, email, telefono, web, origen, ip) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (codigo, d["marca"], d["actividad"], d["nombre"], d["email"], d["telefono"], d["web"], origen[:200], ip),
                )
                vid = cur.fetchone()[0]
            conn.commit()

        # 2) se busca en INPI (o en el caché) y se calcula el veredicto
        error, desde_cache, filas = None, False, []
        try:
            filas, desde_cache = _filas_para(d["marca"])
        except vc.ErrorInpi as e:
            error = str(e)[:300]
        except Exception as e:  # noqa: BLE001 — pase lo que pase, la persona recibe una respuesta
            error = f"error inesperado ({type(e).__name__})"
            print(f"[verificador] consulta {vid}: {e}")
        if error:
            res = {"verdict": "error", "exactCount": 0, "similarCount": 0, "samples": [], "muestras": [], "posible_mas": False}
        else:
            res = vc.evaluar(d["marca"], filas, genericas=_genericas())

        # 3) se guarda el resultado
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE verificaciones_marca SET veredicto = %s, exactos = %s, similares = %s, muestras = %s, "
                    "posible_mas = %s, error = %s, desde_cache = %s, consultada_en = now() WHERE id = %s",
                    (res["verdict"], res["exactCount"], res["similarCount"], psycopg2.extras.Json(res["muestras"]),
                     res["posible_mas"], error, desde_cache, vid),
                )
            conn.commit()
        publico = {"verdict": res["verdict"], "exactCount": res["exactCount"], "similarCount": res["similarCount"],
                   "samples": res["samples"]}
        return 200, {"public": publico, "consultaId": codigo}, vid

    @router.post("/api/publico/verificador/consulta")
    async def consulta(request: Request, background: BackgroundTasks):
        raw = await _leer_json(request)
        if raw is None:
            return _json(request, {"detail": "Pedido inválido."}, 400)
        ip = auth.ip_cliente(request) or "?"
        origen = request.headers.get("origin") or request.headers.get("referer") or ""
        status, cuerpo, vid = await run_in_threadpool(_procesar_consulta, raw, ip, origen)
        if vid:
            background.add_task(_aviso_consulta, vid)
        return _json(request, cuerpo, status)

    @router.get("/api/publico/verificador/{codigo}")
    def ver(codigo: str, request: Request):
        if not vc.CODIGO_RE.match(codigo):
            return _json(request, {"detail": "No encontrada"}, 404)
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute(
                    "SELECT marca, nombre, veredicto, exactos, similares, muestras, creada_en FROM verificaciones_marca "
                    "WHERE codigo = %s AND veredicto <> 'pendiente' AND creada_en > now() - make_interval(days => %s)",
                    (codigo, vc.VALIDEZ_DIAS),
                )
                v = cur.fetchone()
        if not v:
            return _json(request, {"detail": "No encontrada"}, 404)
        return _json(request, {
            "public": {"verdict": v["veredicto"], "exactCount": v["exactos"], "similarCount": v["similares"],
                       "samples": _muestras_publicas(v["muestras"])},
            "marca": v["marca"], "nombre": v["nombre"], "createdAt": v["creada_en"].isoformat(), "informeUrl": None,
        })

    def _procesar_adicional(raw: dict, ip: str):
        if not _permitido("adicional", ip, ADICIONALES_POR_HORA):
            return 429, {"ok": False, "error": "Recibimos muchas consultas desde tu conexión. Probá de nuevo en un rato."}, None
        try:
            d = vc.validar_adicional(raw.get("pregunta"), raw.get("nombre"), raw.get("email"))
        except ValueError as e:
            return 400, {"ok": False, "error": str(e)}, None
        codigo = str(raw.get("consultaId") or "")
        vid, marca = None, vc._limpio(raw.get("marca"), 80)
        with conexion() as conn:
            with conn.cursor() as cur:
                if vc.CODIGO_RE.match(codigo):
                    cur.execute("SELECT id, marca FROM verificaciones_marca WHERE codigo = %s", (codigo,))
                    f = cur.fetchone()
                    if f:
                        vid, marca = f[0], f[1]
                cur.execute(
                    "INSERT INTO verificaciones_marca_consultas (verificacion_id, marca, nombre, email, pregunta, ip) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (vid, marca, d["nombre"], d["email"], d["pregunta"], ip),
                )
                cid = cur.fetchone()[0]
            conn.commit()
        return 200, {"ok": True}, cid

    @router.post("/api/publico/verificador/consulta-adicional")
    async def consulta_adicional(request: Request, background: BackgroundTasks):
        raw = await _leer_json(request)
        if raw is None:
            return _json(request, {"ok": False, "error": "Pedido inválido."}, 400)
        ip = auth.ip_cliente(request) or "?"
        status, cuerpo, cid = await run_in_threadpool(_procesar_adicional, raw, ip)
        if cid:
            background.add_task(_aviso_adicional, cid)
        return _json(request, cuerpo, status)

    # ── Del panel ─────────────────────────────────────────────────────
    @router.get("/api/consultas-web")
    def listar(estado: str = "", veredicto: str = "", q: str = "", _: str = Depends(verificar_login)):
        cond, val = [], []
        if estado in ("nueva", "revisada"):
            cond.append("v.estado = %s")
            val.append(estado)
        if veredicto in vc.VEREDICTOS or veredicto == "pendiente":
            cond.append("v.veredicto = %s")
            val.append(veredicto)
        q = (q or "").strip()[:80]
        if q:
            cond.append("(v.marca ILIKE %s OR v.nombre ILIKE %s OR v.email ILIKE %s)")
            val += [f"%{q}%"] * 3
        donde = ("WHERE " + " AND ".join(cond)) if cond else ""
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute(
                    f"""
                    SELECT v.id, v.codigo, v.creada_en, v.marca, v.actividad, v.nombre, v.email, v.telefono, v.web,
                           v.veredicto, v.exactos, v.similares, v.estado, v.desde_cache, v.error, v.aviso_enviado_en, v.aviso_error,
                           (SELECT COUNT(*) FROM verificaciones_marca_consultas c WHERE c.verificacion_id = v.id) AS adicionales
                    FROM verificaciones_marca v {donde}
                    ORDER BY v.creada_en DESC LIMIT 500
                    """,
                    val,
                )
                filas = cur.fetchall()
                cur.execute("SELECT COUNT(*) AS n FROM verificaciones_marca WHERE estado = 'nueva'")
                nuevas = cur.fetchone()["n"]
        return {"consultas": filas, "nuevas": nuevas, "veredictos": vc.VEREDICTO_LEGIBLE}

    @router.get("/api/consultas-web/{cid}")
    def ver_consulta(cid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT * FROM verificaciones_marca WHERE id = %s", (cid,))
                v = cur.fetchone()
                if not v:
                    raise HTTPException(status_code=404, detail="No existe esa consulta")
                cur.execute("SELECT id, creada_en, nombre, email, pregunta, aviso_error FROM verificaciones_marca_consultas "
                            "WHERE verificacion_id = %s ORDER BY id", (cid,))
                v["adicionales"] = cur.fetchall()
        v.pop("ip", None)
        v["veredicto_legible"] = vc.VEREDICTO_LEGIBLE.get(v["veredicto"], v["veredicto"])
        return v

    class CambioEstado(BaseModel):
        estado: str

    @router.post("/api/consultas-web/{cid}/estado")
    def cambiar_estado(cid: int, body: CambioEstado, usuario: str = Depends(verificar_login)):
        if body.estado not in ("nueva", "revisada"):
            raise HTTPException(status_code=400, detail="Estado inválido")
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE verificaciones_marca SET estado = %s, revisado_por = %s, revisado_en = CASE WHEN %s = 'revisada' THEN now() END WHERE id = %s",
                    (body.estado, usuario if body.estado == "revisada" else None, body.estado, cid),
                )
                if not cur.rowcount:
                    raise HTTPException(status_code=404, detail="No existe esa consulta")
            conn.commit()
        return {"ok": True}

    @router.delete("/api/consultas-web/{cid}")
    def borrar(cid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM verificaciones_marca WHERE id = %s", (cid,))
                if not cur.rowcount:
                    raise HTTPException(status_code=404, detail="No existe esa consulta")
            conn.commit()
        return {"ok": True}

    return router
