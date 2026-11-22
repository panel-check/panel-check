"""
API de las plantillas para clientes (Mails → «Plantillas para clientes»), desde el 22/11/2026 (antes en Notion).

  GET    /api/plantillas          todas (más las listas de servicios y canales)
  POST   /api/plantillas          nueva
  PUT    /api/plantillas/{id}     editar
  DELETE /api/plantillas/{id}     borrar

El armado del mail (completar [nombre], [marca], [acta]… con los datos del trabajo o del cliente) se hace en el
navegador (static/plantillas.js): acá solo se guardan los textos. Ver plantillas_core.py.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

import plantillas_core as pc
import trabajos_core as tc


class PlantillaDatos(BaseModel):
    titulo: Optional[str] = None
    servicio: Optional[str] = None
    para_etapa: Optional[int] = None
    canal: Optional[str] = None
    asunto: Optional[str] = None
    cuando: Optional[str] = None
    cuerpo: Optional[str] = None
    orden: Optional[float] = None


def _limpio(v, largo):
    v = (v or "").strip()
    return v[:largo] or None


def crear_router(verificar_login, conexion):
    router = APIRouter()

    def rcur(conn):
        return conn.cursor(cursor_factory=RealDictCursor)

    def _validar(body: PlantillaDatos, nueva: bool):
        campos = {}
        enviados = body.model_fields_set
        if nueva or "titulo" in enviados:
            t = _limpio(body.titulo, 200)
            if not t:
                raise HTTPException(status_code=400, detail="Poné un título a la plantilla")
            campos["titulo"] = t
        if "servicio" in enviados:
            if body.servicio not in pc.SERVICIOS_PLANTILLA:
                raise HTTPException(status_code=400, detail="Servicio desconocido")
            campos["servicio"] = body.servicio
        if "canal" in enviados:
            if body.canal not in pc.CANALES:
                raise HTTPException(status_code=400, detail="Canal desconocido")
            campos["canal"] = body.canal
        if "para_etapa" in enviados:
            campos["para_etapa"] = body.para_etapa if body.para_etapa is None or 0 <= body.para_etapa <= 20 else None
        if "asunto" in enviados:
            campos["asunto"] = _limpio(body.asunto, 300)
        if "cuando" in enviados:
            campos["cuando"] = _limpio(body.cuando, 1000)
        if "cuerpo" in enviados:
            campos["cuerpo"] = (body.cuerpo or "")[:50000]
        if "orden" in enviados and body.orden is not None:
            campos["orden"] = float(body.orden)
        return campos

    @router.get("/api/plantillas")
    def listar(_: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("SELECT * FROM plantillas ORDER BY servicio, orden, id")
            filas = cur.fetchall()
        # etapas de cada servicio (0 = «Antes de la seña / presupuesto»), para elegir en qué etapa se manda
        etapas = {s["id"]: ["Antes de la seña (presupuesto)"] + s["etapas"] for s in tc.SERVICIOS}
        return {"plantillas": filas, "servicios": pc.SERVICIOS_PLANTILLA, "canales": pc.CANALES, "etapas": etapas}

    @router.post("/api/plantillas")
    def crear(body: PlantillaDatos, usuario: str = Depends(verificar_login)):
        campos = _validar(body, nueva=True)
        campos.setdefault("servicio", "general")
        campos.setdefault("canal", "mail")
        campos["modificado_por"] = usuario
        cols = ", ".join(campos)
        with conexion() as conn, rcur(conn) as cur:
            cur.execute(f"INSERT INTO plantillas ({cols}) VALUES ({', '.join(['%s'] * len(campos))}) RETURNING id",
                        list(campos.values()))
            pid = cur.fetchone()["id"]
            conn.commit()
        return {"id": pid}

    @router.put("/api/plantillas/{pid}")
    def editar(pid: int, body: PlantillaDatos, usuario: str = Depends(verificar_login)):
        campos = _validar(body, nueva=False)
        if not campos:
            raise HTTPException(status_code=400, detail="No hay cambios para guardar")
        sets = [f"{k} = %s" for k in campos] + ["modificado_en = now()", "modificado_por = %s"]
        with conexion() as conn, rcur(conn) as cur:
            cur.execute(f"UPDATE plantillas SET {', '.join(sets)} WHERE id = %s RETURNING id",
                        list(campos.values()) + [usuario, pid])
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="No existe esa plantilla")
            conn.commit()
        return {"ok": True}

    @router.delete("/api/plantillas/{pid}")
    def borrar(pid: int, _: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("DELETE FROM plantillas WHERE id = %s RETURNING id", (pid,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="No existe esa plantilla")
            conn.commit()
        return {"ok": True}

    return router
