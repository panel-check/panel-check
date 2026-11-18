"""
API de la pantalla Seguimientos (/seguimientos): trabajos puntuales de cada cliente.

  GET    /api/trabajos                     tablero (estado=activo|cerrado|todos, q=texto)
  GET    /api/trabajos/{id}                ficha: trabajo + cliente + expediente + bitácora
  POST   /api/trabajos                     nuevo trabajo
  PUT    /api/trabajos/{id}                cambiar datos, etapa, estado, plazos
  DELETE /api/trabajos/{id}                borrar
  POST   /api/trabajos/{id}/bitacora       agregar una nota a la bitácora
  DELETE /api/trabajos/{id}/bitacora/{bid} borrar una nota

La lógica (servicios, etapas, vencimientos, alertas, orden) está en trabajos_core.py.
"""

import re
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import trabajos_core as tc

RE_ACTA = re.compile(r"^\d{4,9}$")


class TrabajoNuevo(BaseModel):
    cliente_id: int
    marca: str
    servicio: str
    etapa: Optional[int] = 1
    contratacion: Optional[str] = None
    notificacion: Optional[str] = None
    acta: Optional[str] = None
    carpeta_drive: Optional[str] = None
    comprobante: Optional[str] = None
    notas: Optional[str] = None
    responsable: Optional[str] = None


class TrabajoCambios(BaseModel):
    cliente_id: Optional[int] = None
    marca: Optional[str] = None
    servicio: Optional[str] = None
    etapa: Optional[int] = None
    estado: Optional[str] = None
    contratacion: Optional[str] = None
    notificacion: Optional[str] = None
    acta: Optional[str] = None
    carpeta_drive: Optional[str] = None
    comprobante: Optional[str] = None
    notas: Optional[str] = None
    responsable: Optional[str] = None


class NotaBitacora(BaseModel):
    texto: str
    fecha: Optional[str] = None


def _texto(v, largo):
    v = (v or "").strip()
    return v[:largo] or None


def _url(v):
    v = _texto(v, 2000)
    if v and not re.match(r"^https?://", v, re.I):
        raise HTTPException(status_code=400, detail="El link tiene que empezar con http:// o https://")
    return v


def _fecha(v, campo):
    if v in (None, ""):
        return None
    f = tc.a_fecha(v)
    if not f:
        raise HTTPException(status_code=400, detail=f"Fecha inválida en {campo}")
    return f


def _acta(v):
    v = re.sub(r"\D", "", v or "")
    if not v:
        return None
    if not RE_ACTA.match(v):
        raise HTTPException(status_code=400, detail="Número de acta inválido")
    return v


def _campos(body, parcial):
    enviados = body.model_fields_set if parcial else set(type(body).model_fields)
    out = {}
    if "cliente_id" in enviados and body.cliente_id is not None:
        out["cliente_id"] = int(body.cliente_id)
    if "marca" in enviados and body.marca is not None:
        m = tc.marca_limpia(body.marca)
        if not m:
            raise HTTPException(status_code=400, detail="Falta el nombre de la marca")
        out["marca"] = m[:200]
    if "servicio" in enviados and body.servicio is not None:
        if not tc.servicio(body.servicio):
            raise HTTPException(status_code=400, detail="Servicio desconocido")
        out["servicio"] = body.servicio
    if "etapa" in enviados and body.etapa is not None:
        out["etapa"] = max(0, min(int(body.etapa), 20))
    if "estado" in enviados and getattr(body, "estado", None) is not None:
        if body.estado not in tc.ESTADOS:
            raise HTTPException(status_code=400, detail="Estado inválido")
        out["estado"] = body.estado
    for k in ("contratacion", "notificacion"):
        if k in enviados:
            out[k] = _fecha(getattr(body, k), k)
    if "acta" in enviados:
        out["acta"] = _acta(body.acta)
    for k in ("carpeta_drive", "comprobante"):
        if k in enviados:
            out[k] = _url(getattr(body, k))
    if "notas" in enviados:
        out["notas"] = _texto(body.notas, 4000)
    if "responsable" in enviados:
        out["responsable"] = _texto(body.responsable, 60)
    return out


# Acta efectiva: la cargada a mano o, si no hay, la marca de la cartera del mismo cliente con
# el mismo nombre (la más reciente). Así el expediente aparece solo para las marcas que ya
# están en Clientes → Marcas.
SQL_LISTA = """
    SELECT t.*, c.nombre AS cliente, c.email AS cliente_email, c.telefono AS cliente_telefono,
           COALESCE(t.acta, cm.acta) AS acta_efectiva, (t.acta IS NULL AND cm.acta IS NOT NULL) AS acta_auto,
           cm.estado_tramite, cm.ultimo_movimiento, cm.ultimo_movimiento_fecha
    FROM trabajos t
    JOIN clientes c ON c.id = t.cliente_id
    LEFT JOIN LATERAL (
        SELECT m.acta, m.estado_tramite, m.ultimo_movimiento, m.ultimo_movimiento_fecha
        FROM cartera_marcas m
        WHERE (t.acta IS NOT NULL AND m.acta = t.acta)
           OR (t.acta IS NULL AND m.cliente_id = t.cliente_id AND upper(m.denominacion) = upper(t.marca))
        ORDER BY (m.acta = t.acta) DESC, m.fecha_presentacion DESC NULLS LAST
        LIMIT 1
    ) cm ON true
"""


def crear_router(verificar_login, conexion) -> APIRouter:
    router = APIRouter()

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def _uno(cur, tid):
        cur.execute(SQL_LISTA + " WHERE t.id = %s", (tid,))
        t = cur.fetchone()
        if not t:
            raise HTTPException(status_code=404, detail="No existe ese trabajo")
        return tc.completar(dict(t))

    @router.get("/api/trabajos")
    def listar(estado: str = "activo", q: str = "", _: str = Depends(verificar_login)):
        cond, val = [], []
        if estado in tc.ESTADOS:
            cond.append("t.estado = %s"); val.append(estado)
        if q.strip():
            like = f"%{q.strip()}%"
            cond.append("(t.marca ILIKE %s OR c.nombre ILIKE %s OR COALESCE(t.acta, '') = %s OR COALESCE(t.notas, '') ILIKE %s)")
            val += [like, like, q.strip(), like]
        donde = (" WHERE " + " AND ".join(cond)) if cond else ""
        with conexion() as conn, rcur(conn) as cur:
            cur.execute(SQL_LISTA + donde, val)
            hoy = tc.hoy_ar()
            filas = tc.ordenar([tc.completar(dict(f), hoy) for f in cur.fetchall()])
            cur.execute("SELECT estado, COUNT(*) AS n FROM trabajos GROUP BY estado")
            cuentas = {f["estado"]: f["n"] for f in cur.fetchall()}
        return {"hoy": hoy, "servicios": tc.servicios_publicos(), "trabajos": filas,
                "cuentas": cuentas, "notion_procesos": tc.NOTION_PROCESOS}

    @router.get("/api/trabajos/{tid}")
    def ficha(tid: int, _: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            t = _uno(cur, tid)
            expediente = None
            if t.get("acta_efectiva"):
                cur.execute(
                    """SELECT acta, denominacion, tipo, clase, titular, fecha_presentacion, fecha_publicacion,
                              estado_tramite, fecha_concesion, numero_disposicion, fecha_vencimiento_marca,
                              tuvo_oposicion, detalle_oposicion, ultimo_movimiento, ultimo_movimiento_fecha,
                              consultado_en, agente
                       FROM cartera_marcas WHERE acta = %s""", (t["acta_efectiva"],))
                marca = cur.fetchone()
                cur.execute(
                    """SELECT tipo, texto, detectado_en FROM cartera_novedades
                       WHERE acta = %s ORDER BY detectado_en DESC LIMIT 40""", (t["acta_efectiva"],))
                expediente = {"acta": t["acta_efectiva"], "automatica": bool(t.get("acta_auto")),
                              "en_cartera": bool(marca), "marca": marca, "novedades": cur.fetchall()}
            cur.execute(
                "SELECT id, fecha, texto, usuario, creado_en FROM trabajos_bitacora "
                "WHERE trabajo_id = %s ORDER BY fecha DESC, id DESC", (tid,))
            bitacora = cur.fetchall()
        return {"trabajo": t, "expediente": expediente, "bitacora": bitacora,
                "servicios": tc.servicios_publicos(), "notion_procesos": tc.NOTION_PROCESOS}

    @router.post("/api/trabajos")
    def crear(body: TrabajoNuevo, usuario: str = Depends(verificar_login)):
        campos = _campos(body, parcial=False)
        if not campos.get("marca") or not campos.get("servicio"):
            raise HTTPException(status_code=400, detail="Faltan la marca o el servicio")
        campos.setdefault("contratacion", tc.hoy_ar())
        campos.pop("estado", None)
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("SELECT 1 FROM clientes WHERE id = %s", (campos["cliente_id"],))
            if not cur.fetchone():
                raise HTTPException(status_code=400, detail="No existe ese cliente")
            cols = list(campos)
            cur.execute(
                f"INSERT INTO trabajos ({', '.join(cols)}, alta_por, modificado_por) "
                f"VALUES ({', '.join(['%s'] * len(cols))}, %s, %s) RETURNING id",
                [campos[c] for c in cols] + [usuario, usuario])
            tid = cur.fetchone()["id"]
            conn.commit()
            return {"trabajo": _uno(cur, tid)}

    @router.put("/api/trabajos/{tid}")
    def cambiar(tid: int, body: TrabajoCambios, usuario: str = Depends(verificar_login)):
        campos = _campos(body, parcial=True)
        if not campos:
            raise HTTPException(status_code=400, detail="No hay cambios")
        sets = [f"{k} = %s" for k in campos] + ["modificado_en = now()", "modificado_por = %s"]
        if "estado" in campos:
            sets.append("cerrado_en = CASE WHEN %s = 'cerrado' THEN now() ELSE NULL END")
        val = list(campos.values()) + [usuario] + ([campos["estado"]] if "estado" in campos else [])
        with conexion() as conn, rcur(conn) as cur:
            if "cliente_id" in campos:
                cur.execute("SELECT 1 FROM clientes WHERE id = %s", (campos["cliente_id"],))
                if not cur.fetchone():
                    raise HTTPException(status_code=400, detail="No existe ese cliente")
            cur.execute(f"UPDATE trabajos SET {', '.join(sets)} WHERE id = %s", val + [tid])
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="No existe ese trabajo")
            conn.commit()
            return {"trabajo": _uno(cur, tid)}

    @router.delete("/api/trabajos/{tid}")
    def borrar(tid: int, _: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("DELETE FROM trabajos WHERE id = %s", (tid,))
            conn.commit()
            return {"ok": cur.rowcount > 0}

    @router.post("/api/trabajos/{tid}/bitacora")
    def agregar_nota(tid: int, body: NotaBitacora, usuario: str = Depends(verificar_login)):
        texto = _texto(body.texto, 4000)
        if not texto:
            raise HTTPException(status_code=400, detail="La nota está vacía")
        fecha = _fecha(body.fecha, "fecha") or tc.hoy_ar()
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("SELECT 1 FROM trabajos WHERE id = %s", (tid,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="No existe ese trabajo")
            cur.execute(
                "INSERT INTO trabajos_bitacora (trabajo_id, fecha, texto, usuario) VALUES (%s, %s, %s, %s) "
                "RETURNING id, fecha, texto, usuario, creado_en", (tid, fecha, texto, usuario))
            nota = cur.fetchone()
            cur.execute("UPDATE trabajos SET modificado_en = now(), modificado_por = %s WHERE id = %s", (usuario, tid))
            conn.commit()
            return {"nota": nota}

    @router.delete("/api/trabajos/{tid}/bitacora/{bid}")
    def borrar_nota(tid: int, bid: int, _: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("DELETE FROM trabajos_bitacora WHERE id = %s AND trabajo_id = %s", (bid, tid))
            conn.commit()
            return {"ok": cur.rowcount > 0}

    return router
