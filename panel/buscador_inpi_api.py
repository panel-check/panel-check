"""
Rutas del Buscador INPI (pantalla /buscador-inpi, ver buscador_inpi.py). Todas con login.

  POST /api/inpi/buscar                      busca en INPI en vivo. Cuerpo: {modo, ...}
                                             modo «rapido» (q), «puntual» (acta / resolucion), «avanzada»,
                                             «renovaciones» (presentacion_desde / hasta) o «cuit» (cuit).
                                             Cada fila trae `panel`: si el acta ya está en el panel
                                             (cliente, lead o marca sin ser lead) y a dónde abrirla.
  GET  /api/inpi/acta/{acta}                 el acta completa, en vivo: datos, oposiciones, vistas,
                                             publicaciones, logo y Grilla Digital (+ `panel`).
  GET  /api/inpi/acta/{acta}/archivo?id=...  un archivo de la Grilla Digital (PDF), para verlo en el panel.
"""

import re
from typing import Optional
from urllib.parse import quote

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel

import buscador_inpi as bi

MODOS = ("rapido", "puntual", "avanzada", "renovaciones", "cuit")


class Busqueda(BaseModel):
    modo: str = "rapido"
    q: Optional[str] = None
    acta: Optional[str] = None
    resolucion: Optional[str] = None
    tipo_resolucion: Optional[str] = None
    clase: Optional[str] = None
    denominacion: Optional[str] = None
    denominacion_empieza: bool = False
    titular: Optional[str] = None
    titular_empieza: bool = False
    ingreso_desde: Optional[str] = None
    ingreso_hasta: Optional[str] = None
    resolucion_desde: Optional[str] = None
    resolucion_hasta: Optional[str] = None
    vigentes: bool = False
    presentacion_desde: Optional[str] = None
    presentacion_hasta: Optional[str] = None
    cuit: Optional[str] = None
    limit: int = 50
    offset: int = 0


def _clave_titular(cuit, titular) -> str:
    """Igual que claveTitular de comun.js: el CUIT o, si falta, el nombre en mayúsculas."""
    c = (cuit or "").strip()
    return c or " ".join((titular or "").strip().upper().split())


def en_panel(cur, actas: list) -> dict:
    """{acta: {tipo, etiqueta, url}} de las actas que ya están en el panel.
    tipo: «cliente» (cartera), «lead» (es lead) o «marca» (está en Leads pero no es lead)."""
    actas = sorted({a for a in actas if a})
    if not actas:
        return {}
    out = {}
    cur.execute("SELECT acta, es_lead, contactado, titular, cuit FROM marcas WHERE acta = ANY(%s)", (actas,))
    for r in cur.fetchall():
        clave = _clave_titular(r["cuit"], r["titular"])
        url = f"/titular/{quote(clave, safe='')}?acta={quote(r['acta'])}" if clave else f"/?q={quote(r['acta'])}&es_lead=todos&contactado=todos"
        if r["es_lead"]:
            etiqueta = "Lead · contactado" if r["contactado"] else "Lead"
            out[r["acta"]] = {"tipo": "lead", "etiqueta": etiqueta, "url": url}
        else:
            out[r["acta"]] = {"tipo": "marca", "etiqueta": "En el panel (no es lead)", "url": url}
    cur.execute("SELECT cm.acta, cm.cliente_id, c.nombre FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id "
                "WHERE cm.acta = ANY(%s)", (actas,))
    for r in cur.fetchall():
        out[r["acta"]] = {"tipo": "cliente", "etiqueta": f"Cliente: {r['nombre']}", "url": f"/clientes?cliente={r['cliente_id']}"}
    return out


def crear_router(verificar_login, conexion) -> APIRouter:
    router = APIRouter()

    def _con_panel(filas: list) -> list:
        try:
            with conexion() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    marcadas = en_panel(cur, [f.get("acta") for f in filas])
        except Exception as e:   # la base no frena la búsqueda en INPI
            print(f"[buscador-inpi] no se pudo cruzar con el panel: {e}")
            marcadas = {}
        for f in filas:
            f["panel"] = marcadas.get(f.get("acta"))
        return filas

    def _buscar(b: Busqueda) -> dict:
        if b.modo == "rapido":
            return bi.buscar_rapido(b.q or "", b.limit)
        if b.modo == "puntual":
            return bi.buscar_puntual(b.acta or "", b.resolucion or "")
        if b.modo == "avanzada":
            return bi.buscar_avanzada(b.model_dump())
        if b.modo == "renovaciones":
            return bi.buscar_renovaciones(b.presentacion_desde, b.presentacion_hasta, b.limit, b.offset)
        return bi.buscar_cuit(b.cuit or "")

    @router.post("/api/inpi/buscar")
    async def buscar(b: Busqueda, _: str = Depends(verificar_login)):
        if b.modo not in MODOS:
            raise HTTPException(status_code=400, detail="Tipo de búsqueda inválido.")
        try:
            res = await run_in_threadpool(_buscar, b)
        except bi.BuscadorError as e:
            raise HTTPException(status_code=400, detail=str(e))
        res["filas"] = await run_in_threadpool(_con_panel, res["filas"])
        return res

    @router.get("/api/inpi/acta/{acta}")
    async def acta(acta: str, _: str = Depends(verificar_login)):
        if not re.fullmatch(r"\d{4,9}", acta or ""):
            raise HTTPException(status_code=400, detail="Número de acta inválido.")
        try:
            d = await run_in_threadpool(bi.detalle_acta, acta)
        except bi.BuscadorError as e:
            raise HTTPException(status_code=400, detail=str(e))
        d["panel"] = (await run_in_threadpool(_con_panel, [{"acta": acta}]))[0]["panel"]
        return d

    @router.get("/api/inpi/acta/{acta}/archivo")
    async def archivo(acta: str, id: str, _: str = Depends(verificar_login)):
        if not re.fullmatch(r"\d{4,9}", acta or "") or not id or len(id) > 500:
            raise HTTPException(status_code=400, detail="Datos del archivo inválidos.")
        try:
            contenido, tipo, nombre = await run_in_threadpool(bi.archivo_grilla, acta, id)
        except bi.BuscadorError as e:
            raise HTTPException(status_code=400, detail=str(e))
        seguro = re.sub(r"[^\w.\-]", "_", nombre)[:120] or "archivo.pdf"
        # Solo PDF e imágenes se muestran en el navegador; cualquier otra cosa se descarga.
        en_linea = tipo in ("application/pdf", "image/png", "image/jpeg", "image/gif", "image/tiff")
        return Response(content=contenido, media_type=tipo if en_linea else "application/octet-stream",
                        headers={"Content-Disposition": f'{"inline" if en_linea else "attachment"}; filename="{seguro}"',
                                 "Cache-Control": "private, max-age=600",
                                 "X-Content-Type-Options": "nosniff"})

    return router
