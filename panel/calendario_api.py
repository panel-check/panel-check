"""
API de la sección Calendario (sincronizada con Google Calendar).

Se arma con una fábrica (crear_router) para no importar app.py desde acá: app.py
le pasa el login y la conexión. La lógica está en calendario_core.py.

Rutas:
  GET    /api/calendario/estado               ¿está configurado? última sincronización, errores
  POST   /api/calendario/sincronizar          sincronizar ahora (con ?completa=true, relee todo)
  GET    /api/calendario/eventos?desde&hasta  eventos de un rango de fechas (AAAA-MM-DD)
  GET    /api/calendario/por-actas?actas=a,b  eventos que nombran esas actas (ficha del titular)
  GET    /api/calendario/actas?actas=a,b      de quién es cada acta (vista previa del formulario)
  POST   /api/calendario/eventos              crear (en Google y en el panel)
  PUT    /api/calendario/eventos/{id}         editar
  DELETE /api/calendario/eventos/{id}         borrar (también en Google)
"""

import datetime as _dt
import re
from typing import List, Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import calendario_core as cal

# Un mes con semanas completas a los costados cabe de sobra en 70 días; el tope evita
# que un pedido a mano traiga años de eventos de una vez.
MAX_DIAS_RANGO = 120


class EventoEntrada(BaseModel):
    titulo: str
    fecha: str                       # AAAA-MM-DD
    hora: Optional[str] = None       # HH:MM; vacío = todo el día
    duracion_min: Optional[int] = 60
    todo_el_dia: Optional[bool] = False
    fecha_fin: Optional[str] = None  # solo para eventos de varios días (todo el día)
    descripcion: Optional[str] = None
    lugar: Optional[str] = None
    actas: Optional[List[str]] = None


def _fecha(valor: str, nombre: str) -> _dt.date:
    try:
        return _dt.date.fromisoformat(valor)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{nombre} tiene que ser una fecha AAAA-MM-DD")


def _lista_actas(texto: str) -> list:
    return [a for a in re.split(r"[,\s]+", texto or "") if re.fullmatch(r"\d{4,9}", a)][:200]


def crear_router(verificar_login, conexion) -> APIRouter:
    router = APIRouter()

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def _configurado_o_409():
        if not cal.configurado():
            raise HTTPException(
                status_code=409,
                detail="El calendario todavía no está conectado con Google (faltan GOOGLE_SERVICE_ACCOUNT_JSON y GOOGLE_CALENDAR_ID en Railway).",
            )

    def _como_http(e: cal.CalendarioError):
        # Los errores de Google (permisos, calendario no compartido) son de configuración, no del usuario.
        codigo = 502 if isinstance(e, cal.GoogleError) else 400
        return HTTPException(status_code=codigo, detail=str(e))

    @router.get("/api/calendario/estado")
    def estado(_: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                try:
                    return cal.estado(cur)
                except Exception:
                    conn.rollback()
                    # La tabla puede no existir todavía si el panel recién arrancó: se informa igual.
                    return {"configurado": cal.configurado(), "calendario": cal.calendar_id(),
                            "cuenta_servicio": cal.cuenta_servicio(), "ultimo_ok_en": None,
                            "ultimo_error": None, "sondeo_segundos": cal.SONDEO_SEGUNDOS}

    @router.post("/api/calendario/sincronizar")
    def sincronizar(completa: bool = False, _: str = Depends(verificar_login)):
        _configurado_o_409()
        try:
            return cal.sincronizar(conexion, completa=completa)
        except cal.CalendarioError as e:
            raise _como_http(e)

    @router.get("/api/calendario/eventos")
    def eventos(desde: str, hasta: str, _: str = Depends(verificar_login)):
        d0, d1 = _fecha(desde, "desde"), _fecha(hasta, "hasta")
        if d1 < d0:
            raise HTTPException(status_code=400, detail="«hasta» es anterior a «desde»")
        if (d1 - d0).days > MAX_DIAS_RANGO:
            raise HTTPException(status_code=400, detail=f"El rango es demasiado largo (máximo {MAX_DIAS_RANGO} días)")
        with conexion() as conn:
            with rcur(conn) as cur:
                return {"eventos": cal.listar_eventos(cur, d0, d1)}

    @router.get("/api/calendario/por-actas")
    def por_actas(actas: str = Query(""), _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                return {"eventos": cal.eventos_de_actas(cur, _lista_actas(actas))}

    @router.get("/api/calendario/actas")
    def actas_info(actas: str = Query(""), _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                info = cal.resolver_actas(cur, _lista_actas(actas))
        return {"actas": list(info.values())}

    def _evento_por_id(evento_id: int) -> dict:
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT * FROM calendario_eventos WHERE id = %s", (evento_id,))
                fila = cur.fetchone()
                if not fila:
                    raise HTTPException(status_code=404, detail="Ese evento no existe")
                return cal._con_vinculos(cur, [cal.evento_a_json(fila)])[0]

    @router.post("/api/calendario/eventos")
    def crear(datos: EventoEntrada, usuario: str = Depends(verificar_login)):
        _configurado_o_409()
        try:
            nuevo = cal.crear_evento(conexion, datos.dict(), usuario)
        except cal.CalendarioError as e:
            raise _como_http(e)
        return {"evento": _evento_por_id(nuevo)}

    @router.put("/api/calendario/eventos/{evento_id}")
    def editar(evento_id: int, datos: EventoEntrada, usuario: str = Depends(verificar_login)):
        _configurado_o_409()
        try:
            cal.editar_evento(conexion, evento_id, datos.dict(), usuario)
        except cal.CalendarioError as e:
            raise _como_http(e)
        return {"evento": _evento_por_id(evento_id)}

    @router.delete("/api/calendario/eventos/{evento_id}")
    def borrar(evento_id: int, _: str = Depends(verificar_login)):
        _configurado_o_409()
        try:
            cal.borrar_evento(conexion, evento_id)
        except cal.CalendarioError as e:
            raise _como_http(e)
        return {"ok": True}

    return router
