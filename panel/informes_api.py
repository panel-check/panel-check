"""
API de la pestaña «Informes» de la ficha del cliente (ver informes_viabilidad.py).

Rutas (todas con sesión):
  GET    /api/clientes/{id}/informes           informes guardados del cliente (el interno, con un resumen)
  POST   /api/clientes/{id}/informes/cliente   pegar el JSON del CLIENTE: se valida y se guarda;
                                               devuelve el id para bajar el PDF
  POST   /api/clientes/{id}/informes/interno   pegar el JSON INTERNO de la IA: se guarda tal cual
  GET    /api/informes/{iid}                   el JSON guardado
  GET    /api/informes/{iid}/pdf               el PDF (solo los del cliente)
  GET    /api/informes/{iid}/json              descargar el JSON
  DELETE /api/informes/{iid}                   borrar
"""

import json
from urllib.parse import quote

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import informes_viabilidad as iv


class JsonPegado(BaseModel):
    texto: str


def crear_router(verificar_login, conexion) -> APIRouter:
    router = APIRouter()

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def _cliente(cur, cliente_id):
        cur.execute("SELECT id, nombre FROM clientes WHERE id = %s", (cliente_id,))
        c = cur.fetchone()
        if not c:
            raise HTTPException(status_code=404, detail="No existe ese cliente")
        return c

    def _informe(cur, iid):
        cur.execute("SELECT * FROM informes_viabilidad WHERE id = %s", (iid,))
        f = cur.fetchone()
        if not f:
            raise HTTPException(status_code=404, detail="No existe ese informe")
        return f

    def _guardar(cliente_id, tipo, marca, datos, usuario) -> int:
        with conexion() as conn:
            with rcur(conn) as cur:
                _cliente(cur, cliente_id)
                cur.execute("INSERT INTO informes_viabilidad (cliente_id, tipo, marca, datos, generado_por) "
                            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                            (cliente_id, tipo, marca, json.dumps(datos, ensure_ascii=False), usuario))
                iid = cur.fetchone()["id"]
            conn.commit()
        return iid

    @router.get("/api/clientes/{cliente_id}/informes")
    def listar(cliente_id: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                _cliente(cur, cliente_id)
                return {"informes": listar_informes(cur, cliente_id)}

    @router.post("/api/clientes/{cliente_id}/informes/cliente")
    def guardar_cliente(cliente_id: int, body: JsonPegado, usuario: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                nombre = _cliente(cur, cliente_id)["nombre"]
        try:
            d = iv.normalizar_cliente(iv.leer_json(body.texto), nombre)
            iv.generar_pdf(d)  # que no falle recién al bajarlo
        except iv.InformeInvalido as e:
            raise HTTPException(status_code=400, detail=str(e))
        iid = _guardar(cliente_id, "cliente", d["encabezado"]["marca_pretendida"], d, usuario)
        return {"id": iid, "archivo": iv.nombre_archivo(d)}

    @router.post("/api/clientes/{cliente_id}/informes/interno")
    def guardar_interno(cliente_id: int, body: JsonPegado, usuario: str = Depends(verificar_login)):
        try:
            d = iv.normalizar_interno(iv.leer_json(body.texto))
        except iv.InformeInvalido as e:
            raise HTTPException(status_code=400, detail=str(e))
        iid = _guardar(cliente_id, "interno", iv.marca_interno(d), d, usuario)
        return {"id": iid}

    @router.get("/api/informes/{iid}")
    def ver(iid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                f = _informe(cur, iid)
        return {"id": f["id"], "tipo": f["tipo"], "marca": f["marca"], "datos": f["datos"]}

    @router.get("/api/informes/{iid}/pdf")
    def pdf(iid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                f = _informe(cur, iid)
        if f["tipo"] != "cliente":
            raise HTTPException(status_code=400, detail="El informe interno no tiene PDF")
        d = f["datos"]
        return Response(content=iv.generar_pdf(d), media_type="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(iv.nombre_archivo(d))}"})

    @router.get("/api/informes/{iid}/json")
    def bajar_json(iid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                f = _informe(cur, iid)
        base = iv.nombre_archivo({"encabezado": {"marca_pretendida": f["marca"] or ""}}, "json")
        nombre = base.replace("Informe_Preliminar_", "Informe_IA_interno_" if f["tipo"] == "interno" else "Informe_cliente_")
        return Response(content=json.dumps(f["datos"], ensure_ascii=False, indent=2).encode("utf-8"),
                        media_type="application/json",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"})

    @router.delete("/api/informes/{iid}")
    def borrar(iid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM informes_viabilidad WHERE id = %s", (iid,))
            conn.commit()
        return {"ok": True}

    return router


def listar_informes(cur, cliente_id: int) -> list:
    """Lista para la ficha (sin el JSON completo; el interno trae su resumen)."""
    cur.execute("SELECT id, tipo, marca, datos, generado_por, generado_en FROM informes_viabilidad "
                "WHERE cliente_id = %s ORDER BY generado_en DESC LIMIT 50", (cliente_id,))
    out = []
    for f in cur.fetchall():
        d = f.pop("datos") or {}
        if f["tipo"] == "interno":
            f["resumen"] = iv.resumen_interno(d)
        else:
            f["fecha_informe"] = (d.get("encabezado") or {}).get("fecha_informe", "")
        out.append(f)
    return out
