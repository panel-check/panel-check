"""Vigilancia de expediente: API pública de la página del titular (sin login, por token).

  OPTIONS /api/publico/seguimiento/...              verificación previa (preflight) del navegador
  GET     /api/publico/seguimiento/{token}          estado del seguimiento y sus marcas
  POST    /api/publico/seguimiento/{token}/activar  el titular acepta el seguimiento gratuito
  POST    /api/publico/seguimiento/{token}/baja     deja de recibir avisos
  GET     /api/seguimiento/links-prueba             (con login) links de la página de algunos titulares de prueba

La llama la página wordpress/seguimiento-expediente.html (smartiesconsultora.com.ar/seguimiento/?t=TOKEN).
Lee solo lo que guardó el barrido diario: nunca consulta al INPI, así la página carga al instante.
"""

import threading
import time
from collections import defaultdict, deque

import psycopg2.extras
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

import seguimiento_expediente as se
import verificador_core as vc

MAX_POR_HORA = 120   # pedidos por IP y por hora (la página hace 1 o 2 por visita)

_usos = defaultdict(deque)
_lock = threading.Lock()


def _permitido(ip: str) -> bool:
    ahora = time.time()
    with _lock:
        q = _usos[ip]
        while q and ahora - q[0] > 3600:
            q.popleft()
        if len(q) >= MAX_POR_HORA:
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


def _ip(request: Request) -> str:
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "")


PAGINA = "https://smartiesconsultora.com.ar/seguimiento/?t="


def crear_router(verificar_login, conexion) -> APIRouter:
    router = APIRouter()

    @router.get("/api/seguimiento/links-prueba", dependencies=[Depends(verificar_login)])
    def links_prueba():
        """Para probar la página antes de lanzar: el link de 10 titulares de prueba (nunca reciben mails)."""
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT t.token, count(s.acta) FROM seguimiento_titulares t
                               JOIN seguimiento_actas s ON s.titular_id = t.id
                               WHERE t.prueba GROUP BY t.token ORDER BY count(s.acta) DESC, t.token LIMIT 10""")
                return [{"link": PAGINA + tok, "actas": n} for tok, n in cur.fetchall()]

    @router.options("/api/publico/seguimiento/{resto:path}", include_in_schema=False)
    def preflight(resto: str, request: Request):
        return Response(status_code=204, headers=_cors(request))

    def _responder(request: Request, token: str, accion=None):
        if not _permitido(_ip(request)):
            return _json(request, {"error": "Demasiados pedidos. Probá de nuevo en un rato."}, 429)
        if not se.token_valido(token):
            return _json(request, {"error": "Link inválido"}, 404)
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if accion:
                    accion(cur, token)
                    conn.commit()
                datos = se.datos_pagina(cur, token)
        if datos is None:
            return _json(request, {"error": "Link inválido o vencido"}, 404)
        return _json(request, datos)

    @router.get("/api/publico/seguimiento/{token}", include_in_schema=False)
    def ver(token: str, request: Request):
        return _responder(request, token)

    @router.post("/api/publico/seguimiento/{token}/activar", include_in_schema=False)
    def activar(token: str, request: Request):
        return _responder(request, token, se.activar)

    @router.post("/api/publico/seguimiento/{token}/baja", include_in_schema=False)
    def baja(token: str, request: Request):
        return _responder(request, token, se.dar_baja)

    return router
