"""
API de la pestaña «Análisis de marca» de la ficha del titular.

Se arma con una fábrica (crear_router) para no importar app.py desde acá (app.py
es quien importa este módulo): app.py le pasa el login y la conexión.

Rutas (todas con sesión):
  GET   /api/analisis-marca/{acta}              datos de la marca (nombre, acta, titular con CUIT),
                                                oposiciones y el texto guardado
  PUT   /api/analisis-marca/{acta}              guardar el texto del análisis
  POST  /api/analisis-marca/{acta}/oposiciones  consultar INPI (un pedido) y guardar las oposiciones
  POST  /api/analisis-marca/{acta}/pdf          el PDF con el membrete de Smarties (con el texto que
                                                se mande, o el guardado)
  GET   /api/analisis-marca/{acta}/logo         el logo de la marca (guardado de INPI), si tiene
  POST  /api/analisis-marca-lote/estado         de varias marcas: cuáles ya tienen análisis escrito
  POST  /api/analisis-marca-lote/pdf            UN PDF con varias marcas (cada una con su análisis guardado)
  POST  /api/analisis-marca/{acta}/mejorar-texto  propuesta de redacción mejorada con IA (no guarda
                                                nada: la persona la acepta o la descarta)
"""

from typing import List, Optional
from urllib.parse import quote

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import analisis_marca as am
import ia_texto
import inpi_lead


class TextoAnalisis(BaseModel):
    texto: str = ""


class PedidoPdf(BaseModel):
    texto: Optional[str] = None


class PedidoLote(BaseModel):
    actas: List[str] = []


def crear_router(verificar_login, conexion) -> APIRouter:
    router = APIRouter()

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def _acta(acta: str) -> str:
        acta = (acta or "").strip()
        if not am.RE_ACTA.match(acta):
            raise HTTPException(status_code=400, detail="Número de acta inválido")
        return acta

    def _marca(cur, acta: str) -> dict:
        m = am.leer_marca(cur, acta)
        if not m:
            raise HTTPException(status_code=404, detail="No existe una marca con ese número de acta")
        return m

    def _vista(m: dict, guardado: dict) -> dict:
        op = am.armar_oposiciones(m, guardado["oposiciones"])
        e = am.datos_expediente(m, guardado["expediente"])
        return {
            "acta": str(m["acta"]),
            "marca": e["denominacion"],
            "tipo_marca": e["tipo_marca"],
            "limitacion": e["limitacion"],
            "publicaciones": e["publicaciones"],
            "clase": am.clase_texto(m),
            "tiene_logo": guardado["tiene_logo"],
            # Si todavía no se miró el expediente en busca del logo, la pantalla lo consulta sola.
            "logo_consultado": guardado["logo_consultado"],
            "titular": am.titular_con_cuit(m),
            "oposiciones": {**op, "resumen": am.resumen_oposiciones(op),
                            "lineas": [am.linea_oposicion(i) for i in op["items"]]},
            "texto": guardado["texto"],
            "actualizado_por": guardado["actualizado_por"],
            "actualizado_en": guardado["actualizado_en"].isoformat() if guardado["actualizado_en"] else None,
            # La primera vez que se abre una marca todavía no hay consulta a INPI guardada.
            "consultada_en_inpi": op["origen"] == "inpi" and guardado["logo_consultado"] and guardado["expediente"] is not None,
            # El botón «Mejorar texto» solo funciona si el servidor tiene la clave de la IA.
            "ia_disponible": ia_texto.configurada(),
        }

    @router.get("/api/analisis-marca/{acta}")
    def ver_analisis(acta: str, _: str = Depends(verificar_login)):
        acta = _acta(acta)
        with conexion() as conn, rcur(conn) as cur:
            m = _marca(cur, acta)
            guardado = am.leer_analisis(cur, acta)
        return _vista(m, guardado)

    @router.put("/api/analisis-marca/{acta}")
    def guardar_analisis(acta: str, body: TextoAnalisis, usuario: str = Depends(verificar_login)):
        acta = _acta(acta)
        try:
            texto = am.validar_texto(body.texto)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        with conexion() as conn, rcur(conn) as cur:
            _marca(cur, acta)
            am.guardar_texto(cur, acta, texto, usuario)
            conn.commit()
            guardado = am.leer_analisis(cur, acta)
        return {"ok": True, "actualizado_por": guardado["actualizado_por"],
                "actualizado_en": guardado["actualizado_en"].isoformat() if guardado["actualizado_en"] else None}

    @router.post("/api/analisis-marca/{acta}/oposiciones")
    def consultar_oposiciones(acta: str, _: str = Depends(verificar_login)):
        """Un pedido a INPI (el expediente). Si responde, guarda las oposiciones; si no
        (bloqueo, caída), no toca lo guardado y avisa el motivo: la pestaña sigue
        mostrando lo que el sistema ya sabía."""
        acta = _acta(acta)
        with conexion() as conn, rcur(conn) as cur:
            m = _marca(cur, acta)
        res = inpi_lead.consultar_oposiciones(acta)
        aviso = None
        if res["estado_consulta"] == "ok":
            with conexion() as conn, rcur(conn) as cur:
                am.guardar_oposiciones(cur, acta, res["items"])
                am.guardar_logo(cur, acta, res.get("logo"))
                am.guardar_expediente(cur, acta, res.get("expediente"))
                conn.commit()
        else:
            aviso = res.get("error") or "No se pudo consultar INPI"
        with conexion() as conn, rcur(conn) as cur:
            guardado = am.leer_analisis(cur, acta)
        out = _vista(m, guardado)
        out["aviso"] = aviso
        return out

    @router.post("/api/analisis-marca/{acta}/pdf")
    def pdf_analisis(acta: str, body: PedidoPdf, _: str = Depends(verificar_login)):
        acta = _acta(acta)
        with conexion() as conn, rcur(conn) as cur:
            m = _marca(cur, acta)
            guardado = am.leer_analisis(cur, acta)
            logo = am.leer_logo(cur, acta)
        try:
            texto = am.validar_texto(body.texto) if body.texto is not None else guardado["texto"]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        d = am.datos_para_pdf(m, texto, am.armar_oposiciones(m, guardado["oposiciones"]), logo, guardado["expediente"])
        return Response(
            content=am.generar_pdf(d), media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(am.nombre_archivo(d))}",
                     "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/api/analisis-marca/{acta}/logo")
    def ver_logo(acta: str, _: str = Depends(verificar_login)):
        """El logo de la marca guardado desde el expediente de INPI (404 si no tiene)."""
        acta = _acta(acta)
        with conexion() as conn, rcur(conn) as cur:
            logo = am.leer_logo(cur, acta)
        if not logo:
            raise HTTPException(status_code=404, detail="Esta marca no tiene logo guardado")
        return Response(content=logo[0], media_type=logo[1],
                        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})

    def _actas_lote(actas: list) -> list:
        """Actas válidas, sin repetir y en el orden pedido."""
        vistas, out = set(), []
        for a in actas:
            a = _acta(a)
            if a not in vistas:
                vistas.add(a)
                out.append(a)
        if not out:
            raise HTTPException(status_code=400, detail="Elegí al menos una marca")
        if len(out) > am.MAX_MARCAS_PDF:
            raise HTTPException(status_code=400, detail=f"Son demasiadas marcas para un PDF (máximo {am.MAX_MARCAS_PDF})")
        return out

    @router.post("/api/analisis-marca-lote/estado")
    def estado_lote(body: PedidoLote, _: str = Depends(verificar_login)):
        """De varias marcas, cuáles ya tienen un análisis escrito (para tildarlas solas)."""
        actas = _actas_lote(body.actas)
        with conexion() as conn, rcur(conn) as cur:
            return {"marcas": am.leer_resumen_lote(cur, actas)}

    @router.post("/api/analisis-marca-lote/pdf")
    def pdf_lote(body: PedidoLote, _: str = Depends(verificar_login)):
        """Un solo PDF con varias marcas: cada una con sus datos, su logo y su análisis
        guardado (la pantalla guarda el texto en curso antes de pedirlo)."""
        actas = _actas_lote(body.actas)
        marcas = []
        with conexion() as conn, rcur(conn) as cur:
            for acta in actas:
                m = _marca(cur, acta)
                guardado = am.leer_analisis(cur, acta)
                marcas.append(am.datos_para_pdf(m, guardado["texto"], am.armar_oposiciones(m, guardado["oposiciones"]),
                                                am.leer_logo(cur, acta), guardado["expediente"]))
        return Response(
            content=am.generar_pdf(marcas), media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(am.nombre_archivo(marcas))}",
                     "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.post("/api/analisis-marca/{acta}/mejorar-texto")
    def mejorar_texto(acta: str, body: TextoAnalisis, _: str = Depends(verificar_login)):
        """Manda SOLO el texto del análisis a la IA y devuelve la propuesta mejorada.
        No guarda nada."""
        acta = _acta(acta)
        try:
            texto = am.validar_texto(body.texto)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not ia_texto.configurada():
            # 424 (y no 502/503) para que ningún proxy reemplace el mensaje por su página de error.
            raise HTTPException(status_code=424, detail="La IA no está configurada en el servidor (falta IA_API_KEY).")
        if len(texto.strip()) < ia_texto.LARGO_MIN:
            raise HTTPException(status_code=400, detail="Escribí un poco más de texto para poder mejorarlo.")
        with conexion() as conn, rcur(conn) as cur:
            _marca(cur, acta)
        try:
            return ia_texto.mejorar_texto(texto)
        except ia_texto.ErrorIA as e:
            raise HTTPException(status_code=424, detail=str(e))

    return router
