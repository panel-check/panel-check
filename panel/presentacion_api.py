"""
Presentación de oposición para la llamada con el cliente: la API que usa la página privada de la web
(wordpress/presentacion-oposicion.html). El armado está en presentacion_oposicion.py.

  OPTIONS /api/publico/presentacion/...       verificación previa (preflight) del navegador
  POST    /api/publico/presentacion/consulta  {"acta": "4793580", "actualizar": false}
                                              con la cabecera X-Presentacion-Clave

Es una ruta «pública» (la llama la web, desde otro dominio, sin la sesión del panel), así que se cuida así:
  - Clave: la variable de Railway PRESENTACION_CLAVE. Sin la variable cargada la ruta no anda (503). La clave
    va en una cabecera (nunca en la dirección, que queda en el historial) y se compara en tiempo constante.
  - Origen: solo desde los sitios de VERIFICADOR_ORIGENES (smartiesconsultora.com.ar con y sin www), como el
    verificador de marcas (ver auth.origen_valido y verificador_core.RUTAS_CRUZADAS).
  - Límites por IP: 10 claves mal en 15 minutos bloquean la IP un rato; 60 consultas por hora.
  - Nunca devuelve el fundamento de las oposiciones, el CUIT ni el mail del titular.
"""

import hmac
import json
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

import presentacion_oposicion as po
import verificador_core as vc

CABECERA_CLAVE = "X-Presentacion-Clave"
FALLOS_MAX, FALLOS_VENTANA = 10, 15 * 60
CONSULTAS_MAX, CONSULTAS_VENTANA = 60, 3600
MAX_CUERPO = 2000

_usos = {"fallo": defaultdict(deque), "consulta": defaultdict(deque)}
_usos_lock = threading.Lock()


def _contar(bolsa: str, ip: str, maximo: int, ventana: int, sumar: bool = True) -> bool:
    """True si la IP todavía no llegó al máximo en la ventana (y, con sumar, cuenta este uso)."""
    ahora = time.time()
    with _usos_lock:
        q = _usos[bolsa][ip]
        while q and ahora - q[0] > ventana:
            q.popleft()
        if len(q) >= maximo:
            return False
        if sumar:
            q.append(ahora)
        return True


def clave_configurada() -> str:
    return (os.environ.get("PRESENTACION_CLAVE") or "").strip()


def clave_correcta(enviada: str) -> bool:
    esperada = clave_configurada()
    return bool(esperada) and hmac.compare_digest((enviada or "").strip().encode(), esperada.encode())


def _cors(request: Request) -> dict:
    h = {"Vary": "Origin"}
    origen = vc.origen_de(request.headers.get("origin") or "")
    if origen and origen in vc.origenes_permitidos():
        h["Access-Control-Allow-Origin"] = origen
        h["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        h["Access-Control-Allow-Headers"] = f"Content-Type, {CABECERA_CLAVE}"
        h["Access-Control-Max-Age"] = "600"
    return h


def _json(request: Request, cuerpo, status: int = 200) -> JSONResponse:
    h = _cors(request)
    h["Cache-Control"] = "no-store"
    h["X-Robots-Tag"] = "noindex"
    return JSONResponse(status_code=status, content=cuerpo, headers=h)


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (
        request.client.host if request.client else "?")


def crear_router(conexion) -> APIRouter:
    router = APIRouter()

    import psycopg2.extras

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    @router.options("/api/publico/presentacion/{resto:path}", include_in_schema=False)
    def preflight(resto: str, request: Request):
        return Response(status_code=204, headers=_cors(request))

    @router.post("/api/publico/presentacion/consulta", include_in_schema=False)
    async def consulta(request: Request):
        ip = _ip(request)
        if not clave_configurada():
            return _json(request, {"detail": "Falta cargar PRESENTACION_CLAVE en Railway."}, 503)
        if not _contar("fallo", ip, FALLOS_MAX, FALLOS_VENTANA, sumar=False):
            return _json(request, {"detail": "Demasiados intentos con la clave equivocada. Probá en un rato."}, 429)
        if not clave_correcta(request.headers.get(CABECERA_CLAVE) or ""):
            _contar("fallo", ip, FALLOS_MAX, FALLOS_VENTANA)
            return _json(request, {"detail": "Clave incorrecta.", "clave": False}, 401)
        crudo = await request.body()
        if len(crudo) > MAX_CUERPO:
            return _json(request, {"detail": "Pedido demasiado grande."}, 413)
        try:
            body = json.loads(crudo or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return _json(request, {"detail": "Pedido inválido."}, 400)
        if not _contar("consulta", ip, CONSULTAS_MAX, CONSULTAS_VENTANA):
            return _json(request, {"detail": "Llegaste al límite de consultas por hora. Probá en un rato."}, 429)
        try:
            acta = po.validar_acta(body.get("acta"))
        except po.ActaInvalida as e:
            return _json(request, {"detail": str(e)}, 400)
        try:
            status, cuerpo = await run_in_threadpool(po.armar, acta, conexion, rcur, bool(body.get("actualizar")))
        except Exception as e:  # noqa: BLE001 — la página muestra el error y deja reintentar
            print(f"[presentacion] error armando el acta {acta}: {type(e).__name__}: {e}")
            return _json(request, {"detail": "No se pudo armar la presentación. Probá de nuevo."}, 500)
        return _json(request, cuerpo, status)

    return router
