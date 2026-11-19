"""Campanita 🔔 del encabezado: notificaciones del INPI a Pame (ver notificaciones_inpi.py).

  GET  /api/notificaciones-inpi                 lista (las más nuevas primero) + cuáles son nuevas para mí
  GET  /api/notificaciones-inpi/resumen         cuántas nuevas tengo (el número de la campanita)
  POST /api/notificaciones-inpi/visto           abrí la campanita: lo que había queda leído
  GET  /api/notificaciones-inpi/{id}/pdf        el PDF guardado (o el link del INPI si todavía no se bajó)
  POST /api/notificaciones-inpi/actualizar      lanza ya el proceso notificaciones_inpi.yml en GitHub

Lee solo la base: nunca consulta al INPI (eso lo hace scripts/sincronizar_notificaciones_inpi.py).
"""

import os
import re
import unicodedata

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, Response

import notificaciones_inpi as ni
import programador

WORKFLOW = "notificaciones_inpi.yml"


def _json(f: dict) -> dict:
    d = ni.describir(dict(f))
    for k in ("fecha_carga", "creado_en"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def crear_router(verificar_login, conexion) -> APIRouter:
    router = APIRouter()

    def _visto_hasta(cur, usuario):
        cur.execute("SELECT visto_hasta FROM notificaciones_inpi_visto WHERE usuario = %s", (usuario,))
        r = cur.fetchone()
        return r["visto_hasta"] if r else None

    @router.get("/api/notificaciones-inpi")
    def listar(limite: int = Query(60, ge=1, le=500), tipo: str = "", acta: str = "",
               usuario: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                visto = _visto_hasta(cur, usuario)
                where, params = [], []
                if tipo:
                    where.append("n.tipo_codigo = %s")
                    params.append(tipo)
                if acta:
                    where.append("n.acta = %s")
                    params.append(acta)
                sql = ni.SELECT_DETALLE + (" WHERE " + " AND ".join(where) if where else "")
                cur.execute(sql + " ORDER BY n.fecha_carga DESC NULLS LAST, n.id_notificacion DESC LIMIT %s",
                            params + [limite])
                items = []
                for f in cur.fetchall():
                    d = _json(f)
                    d["nueva"] = (not f["carga_inicial"]) and (visto is None or f["creado_en"] > visto)
                    items.append(d)
                cur.execute("""SELECT creado_en, metricas FROM registro_corridas
                               WHERE proceso = %s ORDER BY creado_en DESC LIMIT 1""", (WORKFLOW,))
                u = cur.fetchone()
                cur.execute("SELECT count(*) AS n FROM notificaciones_inpi")
                total = cur.fetchone()["n"]
        return {"items": items, "total": total,
                "ultima_actualizacion": u["creado_en"].isoformat() if u else None,
                "ultima_con_error": bool(u and (u["metricas"] or {}).get("error"))}

    @router.get("/api/notificaciones-inpi/resumen")
    def resumen(usuario: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                visto = _visto_hasta(cur, usuario)
                cur.execute(
                    """SELECT count(*) AS nuevas,
                              count(*) FILTER (WHERE tipo_codigo = '1') AS titulos
                       FROM notificaciones_inpi
                       WHERE NOT carga_inicial AND creado_en > COALESCE(%s, '-infinity'::timestamptz)""",
                    (visto,),
                )
                r = cur.fetchone()
        return {"nuevas": r["nuevas"], "titulos": r["titulos"]}

    @router.post("/api/notificaciones-inpi/visto")
    def marcar_visto(usuario: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO notificaciones_inpi_visto (usuario, visto_hasta) VALUES (%s, now())
                       ON CONFLICT (usuario) DO UPDATE SET visto_hasta = now()""",
                    (usuario,),
                )
            conn.commit()
        return {"ok": True}

    @router.get("/api/notificaciones-inpi/{id_notificacion}/pdf")
    def pdf(id_notificacion: int, descargar: bool = False, usuario: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""SELECT acta, tipo_codigo, tipo_nombre, pdf, adjunto_url
                               FROM notificaciones_inpi WHERE id_notificacion = %s""", (id_notificacion,))
                f = cur.fetchone()
        if not f:
            raise HTTPException(404, "No existe esa notificación")
        if f["pdf"] is None:
            if f["adjunto_url"]:
                return RedirectResponse(f["adjunto_url"])
            raise HTTPException(404, "El INPI no mandó un PDF para esta notificación")
        nombre_tipo = ni.tipo_de(f["tipo_codigo"] or "", f["tipo_nombre"] or "")[0]
        base = unicodedata.normalize("NFKD", f"{nombre_tipo}-acta-{f['acta']}").encode("ascii", "ignore").decode()
        archivo = re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-").lower() + ".pdf"
        modo = "attachment" if descargar else "inline"
        return Response(bytes(f["pdf"]), media_type="application/pdf",
                        headers={"Content-Disposition": f'{modo}; filename="{archivo}"',
                                 "Cache-Control": "private, max-age=3600"})

    @router.post("/api/notificaciones-inpi/actualizar")
    def actualizar(usuario: str = Depends(verificar_login)):
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise HTTPException(503, "El panel no tiene GITHUB_TOKEN para lanzar el proceso")
        ok, detalle = programador.GitHub(token).disparar(WORKFLOW, {})
        if not ok:
            raise HTTPException(502, detalle)
        return {"ok": True, "detalle": "Pedido enviado: en 1 o 2 minutos se actualiza la campanita."}

    return router
