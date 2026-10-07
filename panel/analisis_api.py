"""
API de la pestaña «Análisis de marca» de la ficha del titular.

Se arma con una fábrica (crear_router) para no importar app.py desde acá (app.py
es quien importa este módulo): app.py le pasa el login y la conexión.

Rutas (todas con sesión):
  GET   /api/analisis-marca/{acta}              datos de la marca (nombre, acta, titular con CUIT),
                                                oposiciones y el texto guardado
  PUT   /api/analisis-marca/{acta}              guardar el texto del análisis
  POST  /api/analisis-marca/{acta}/oposiciones  consultar INPI (el expediente + el de cada marca de oponente
                                                que citan los fundamentos) y guardar oposiciones y datos
  POST  /api/analisis-marca/{acta}/pdf          el PDF con el membrete de Smarties (con el texto que
                                                se mande, o el guardado)
  GET   /api/analisis-marca/{acta}/pdf          el mismo PDF con un link: siempre con el análisis guardado
                                                (lo abre el botón «Ver análisis de marca» del Calendario)
  GET   /api/analisis-marca/{acta}/logo         el logo de la marca (guardado de INPI), si tiene
  GET   /api/analisis-marca-opuesta/{acta}/logo  el logo de la marca de un oponente (guardado de INPI), si tiene
  PUT   /api/analisis-marca-lote/mismo-analisis recordar (por titular) «el mismo análisis para todas»
  POST  /api/analisis-marca-lote/estado         de varias marcas: cuáles ya tienen análisis escrito
  POST  /api/analisis-marca-lote/pdf            UN PDF con varias marcas (cada una con su análisis guardado, o
                                                con un mismo análisis para todas si se manda analisis_comun_de)
  POST  /api/analisis-marca/{acta}/mejorar-texto  propuesta de redacción mejorada con IA (no guarda
                                                nada: la persona la acepta o la descarta)
"""

import logging
import time
from typing import List, Optional
from urllib.parse import quote

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import analisis_marca as am
import ia_texto
import inpi_lead

log = logging.getLogger(__name__)
PAUSA_ENTRE_PEDIDOS = 1.0   # segundos entre un pedido a INPI y el siguiente


class TextoAnalisis(BaseModel):
    texto: str = ""


class PedidoPdf(BaseModel):
    texto: Optional[str] = None


class PedidoMismoAnalisis(BaseModel):
    acta: str
    valor: bool = False


class PedidoLote(BaseModel):
    actas: List[str] = []
    # Si viene, el análisis es el mismo para todas las marcas: se usa el texto guardado de
    # esta acta y sale una sola vez en el PDF (en vez del texto propio de cada marca).
    analisis_comun_de: Optional[str] = None


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

    def _vista(cur, m: dict, guardado: dict) -> dict:
        op = am.armar_oposiciones(m, guardado["oposiciones"])
        # Datos de la marca de cada oponente (los que ya se leyeron de INPI).
        op, pendientes = am.adjuntar_opuestas(cur, str(m["acta"]), op)
        e = am.datos_expediente(m, guardado["expediente"])
        return {
            "acta": str(m["acta"]),
            "url_acta": am.url_expediente(m["acta"]),
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
            # La primera vez que se abre una marca todavía no hay consulta a INPI guardada
            # (ni la de las marcas de los oponentes que citan los fundamentos).
            "consultada_en_inpi": (op["origen"] == "inpi" and guardado["logo_consultado"]
                                   and guardado["expediente"] is not None and not pendientes),
            "opuestas_pendientes": len(pendientes),
            # El botón «Mejorar texto» solo funciona si el servidor tiene la clave de la IA.
            "ia_disponible": ia_texto.configurada(),
        }

    @router.get("/api/analisis-marca/{acta}")
    def ver_analisis(acta: str, _: str = Depends(verificar_login)):
        acta = _acta(acta)
        with conexion() as conn, rcur(conn) as cur:
            m = _marca(cur, acta)
            guardado = am.leer_analisis(cur, acta)
            return _vista(cur, m, guardado)

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

    def _consultar_opuestas(res: dict, s_inpi, acta: str) -> list:
        """Lee en INPI el expediente de cada marca de oponente que citan los fundamentos
        (un pedido por acta, con pausa) y lo guarda. Devuelve los avisos de lo que falló."""
        avisos = []
        for k, a in enumerate(am.actas_opuestas(res["items"], acta)):
            if k:
                time.sleep(PAUSA_ENTRE_PEDIDOS)
            try:
                r = inpi_lead.consultar_marca_opuesta(a, s_inpi)
            except Exception:  # noqa: BLE001 — una marca que falla no tiene que tirar las demás
                log.warning("Falló la consulta de la marca opuesta %s", a, exc_info=True)
                avisos.append(f"No se pudo leer la marca del oponente (acta {a})")
                continue
            if r["estado_consulta"] == "ok":
                with conexion() as conn, rcur(conn) as cur:
                    am.guardar_opuesta(cur, a, "ok", r["datos"], r["logo"])
                    conn.commit()
            elif r["estado_consulta"] == "no_existe":
                with conexion() as conn, rcur(conn) as cur:
                    am.guardar_opuesta(cur, a, "no_existe", {}, None)
                    conn.commit()
            else:
                avisos.append(f"No se pudo leer la marca del oponente (acta {a}): {r.get('error') or 'error'}")
                if r["estado_consulta"] == "bloqueado":
                    break  # si INPI bloquea, insistir con las demás solo empeora
        return avisos

    @router.post("/api/analisis-marca/{acta}/oposiciones")
    def consultar_oposiciones(acta: str, _: str = Depends(verificar_login)):
        """Pedidos a INPI: el expediente de la marca y el de cada marca de oponente que citan
        los fundamentos. Si responde, guarda; si no (bloqueo, caída), no toca lo guardado y
        avisa el motivo: la pestaña sigue mostrando lo que el sistema ya sabía."""
        acta = _acta(acta)
        with conexion() as conn, rcur(conn) as cur:
            m = _marca(cur, acta)
        s_inpi = inpi_lead._crear_sesion()
        res = inpi_lead.consultar_oposiciones(acta, s_inpi)
        aviso = None
        if res["estado_consulta"] == "ok":
            with conexion() as conn, rcur(conn) as cur:
                am.guardar_oposiciones(cur, acta, res["items"])
                am.guardar_logo(cur, acta, res.get("logo"))
                am.guardar_expediente(cur, acta, res.get("expediente"))
                conn.commit()
            avisos = _consultar_opuestas(res, s_inpi, acta)
            aviso = " · ".join(avisos) or None
        else:
            aviso = res.get("error") or "No se pudo consultar INPI"
        with conexion() as conn, rcur(conn) as cur:
            guardado = am.leer_analisis(cur, acta)
            out = _vista(cur, m, guardado)
        out["aviso"] = aviso
        return out

    def _respuesta_pdf(acta: str, texto_pedido: Optional[str]) -> Response:
        with conexion() as conn, rcur(conn) as cur:
            m = _marca(cur, acta)
            guardado = am.leer_analisis(cur, acta)
            logo = am.leer_logo(cur, acta)
            op, _pend = am.adjuntar_opuestas(cur, acta, am.armar_oposiciones(m, guardado["oposiciones"]), con_logo=True)
        try:
            texto = am.validar_texto(texto_pedido) if texto_pedido is not None else guardado["texto"]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        d = am.datos_para_pdf(m, texto, op, logo, guardado["expediente"])
        return Response(
            content=am.generar_pdf(d), media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(am.nombre_archivo(d))}",
                     "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.post("/api/analisis-marca/{acta}/pdf")
    def pdf_analisis(acta: str, body: PedidoPdf, _: str = Depends(verificar_login)):
        return _respuesta_pdf(_acta(acta), body.texto)

    @router.get("/api/analisis-marca/{acta}/pdf")
    def ver_pdf_analisis(acta: str, _: str = Depends(verificar_login)):
        """El mismo PDF pero con un link: se arma en el momento con el análisis GUARDADO, así que siempre
        está al día. Sirve para abrirlo desde otra pantalla (la tarjeta del evento del Calendario) o pasarlo
        al equipo (requiere sesión del panel)."""
        return _respuesta_pdf(_acta(acta), None)

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

    @router.get("/api/analisis-marca-opuesta/{acta}/logo")
    def ver_logo_opuesta(acta: str, _: str = Depends(verificar_login)):
        """El logo de la marca de un oponente guardado desde su expediente de INPI (404 si no tiene)."""
        acta = _acta(acta)
        with conexion() as conn, rcur(conn) as cur:
            logo = am.leer_logo_opuesta(cur, acta)
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
            m = am.leer_marca(cur, actas[0])
            return {"marcas": am.leer_resumen_lote(cur, actas),
                    # Opción recordada del titular: «el mismo análisis para todas las marcas».
                    "mismo_analisis": am.leer_mismo_analisis(cur, am.cuit_de(m))}

    @router.put("/api/analisis-marca-lote/mismo-analisis")
    def guardar_mismo_analisis(body: PedidoMismoAnalisis, usuario: str = Depends(verificar_login)):
        """Recuerda (para todo el equipo) si el titular de esa marca usa el mismo análisis en todas."""
        acta = _acta(body.acta)
        with conexion() as conn, rcur(conn) as cur:
            cuit = am.cuit_de(_marca(cur, acta))
            if not cuit:
                return {"ok": True, "guardado": False}   # sin CUIT no hay a quién asociarla
            am.guardar_mismo_analisis(cur, cuit, body.valor, usuario)
            conn.commit()
        return {"ok": True, "guardado": True}
    @router.post("/api/analisis-marca-lote/pdf")
    def pdf_lote(body: PedidoLote, _: str = Depends(verificar_login)):
        """Un solo PDF con varias marcas: cada una con sus datos, su logo y su análisis
        guardado (la pantalla guarda el texto en curso antes de pedirlo)."""
        actas = _actas_lote(body.actas)
        marcas = []
        with conexion() as conn, rcur(conn) as cur:
            texto_comun = None
            if body.analisis_comun_de:
                de = _acta(body.analisis_comun_de)
                _marca(cur, de)
                texto_comun = am.leer_analisis(cur, de)["texto"]
            for acta in actas:
                m = _marca(cur, acta)
                guardado = am.leer_analisis(cur, acta)
                op, _pend = am.adjuntar_opuestas(cur, acta, am.armar_oposiciones(m, guardado["oposiciones"]), con_logo=True)
                marcas.append(am.datos_para_pdf(m, guardado["texto"], op, am.leer_logo(cur, acta), guardado["expediente"]))
        if texto_comun is not None and len(marcas) == 1:
            marcas[0]["texto"] = texto_comun   # con una sola marca no hay nada que compartir
            texto_comun = None
        return Response(
            content=am.generar_pdf(marcas, texto_comun), media_type="application/pdf",
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
