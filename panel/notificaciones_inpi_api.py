"""Campanita 🔔 del encabezado: notificaciones del INPI a Pame (ver notificaciones_inpi.py) y, desde el
19/11/2026 (pedido de Pame), los movimientos de las actas de clientes que guarda la revisión diaria de la cartera
(tabla cartera_novedades). Desde el 26/11/2026 también las menciones: «@usuario» en una nota de un trabajo de
Seguimientos (tabla trabajos_menciones) le aparece solo a ese usuario.

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
from urllib.parse import quote

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


def nombre_archivo(acta: str, marca) -> str:
    """«4644387-NOMBRE DE LA MARCA.pdf»: sin caracteres que no admite un nombre de archivo."""
    marca = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", (marca or "").strip())
    marca = re.sub(r"\s+", " ", marca).strip(" .")[:80]
    return f"{acta}-{marca}.pdf" if marca else f"{acta}.pdf"


def disposicion(acta: str, marca, descargar: bool = True) -> str:
    """Content-Disposition con el nombre en ASCII (navegadores viejos) y en UTF-8 (tildes, ñ)."""
    nombre = nombre_archivo(acta, marca)
    ascii_ = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().replace('"', "")
    modo = "attachment" if descargar else "inline"
    return f'{modo}; filename="{ascii_}"; filename*=UTF-8\'\'{quote(nombre)}'


SELECT_MENCIONES = """
    SELECT mn.id, mn.trabajo_id, mn.de, mn.creado_en, COALESCE(b.texto, cn.texto) AS texto, t.marca, t.servicio,
           COALESCE(t.cliente_id, mn.cliente_id) AS cliente_id, c.nombre AS cliente
    FROM trabajos_menciones mn
    LEFT JOIN trabajos_bitacora b ON b.id = mn.nota_id
    LEFT JOIN clientes_notas cn ON cn.id = mn.cliente_nota_id
    LEFT JOIN trabajos t ON t.id = mn.trabajo_id
    LEFT JOIN clientes c ON c.id = COALESCE(t.cliente_id, mn.cliente_id)
    WHERE mn.para = %s AND (b.id IS NOT NULL OR cn.id IS NOT NULL)"""


def _mencion(f: dict) -> dict:
    import trabajos_core as tc
    texto = (f["texto"] or "").strip()
    return {"origen": "men", "id": f["id"], "trabajo_id": f["trabajo_id"], "de": f["de"] or "alguien",
            "marca": f["marca"], "cliente": f["cliente"], "cliente_id": f["cliente_id"],
            "servicio": (tc.POR_ID.get(f["servicio"]) or {}).get("nombre", f["servicio"]) if f["servicio"] else "Nota del cliente",
            "texto": texto[:400] + ("…" if len(texto) > 400 else ""), "icono": "💬",
            "fecha": f["creado_en"].isoformat(), "_creado": f["creado_en"]}


def crear_router(verificar_login, conexion) -> APIRouter:
    router = APIRouter()

    def _visto_hasta(cur, usuario):
        cur.execute("SELECT visto_hasta FROM notificaciones_inpi_visto WHERE usuario = %s", (usuario,))
        r = cur.fetchone()
        return r["visto_hasta"] if r else None

    # Sin fila en notificaciones_inpi_visto (primera vez que el usuario abre la campanita) cuentan como nuevas
    # solo las de los últimos 2 días, para no arrancar con todo el historial de movimientos sin leer.
    DESDE_SIN_VISTO = "now() - interval '2 days'"

    @router.get("/api/notificaciones-inpi")
    def listar(limite: int = Query(80, ge=1, le=500), tipo: str = "", acta: str = "", origen: str = "",
               usuario: str = Depends(verificar_login)):
        """origen: '' (todo), 'inpi' (notificaciones del INPI) o 'mov' (movimientos de actas de clientes).
        tipo: código del INPI (1, 14, 15, 4239) — solo para origen inpi."""
        items = []
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                visto = _visto_hasta(cur, usuario)
                cur.execute("SELECT COALESCE(%s, " + DESDE_SIN_VISTO + ") AS corte", (visto,))
                corte = cur.fetchone()["corte"]
                if origen in ("", "inpi"):
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
                    for f in cur.fetchall():
                        d = _json(f)
                        d["origen"] = "inpi"
                        d["fecha"] = d.get("fecha_carga")
                        d["nueva"] = (not f["carga_inicial"]) and f["creado_en"] > corte
                        items.append(d)
                if origen in ("", "mov") and not tipo:
                    where, params = [], []
                    if acta:
                        where.append("nv.acta = %s")
                        params.append(acta)
                    sql = ni.SELECT_MOVIMIENTOS + (" AND " + " AND ".join(where) if where else "")
                    # Se trae de más porque la mayoría de los movimientos de trámite no se muestran.
                    cur.execute(sql + " ORDER BY nv.detectado_en DESC, nv.id DESC LIMIT %s", params + [limite * 8])
                    for d in ni.filtrar_movimientos(cur.fetchall())[:limite]:
                        d["origen"] = "mov"
                        d["nueva"] = d["detectado_en"] > corte
                        d["fecha"] = d.pop("detectado_en").isoformat()
                        items.append(d)
                if origen in ("", "men") and not tipo and not acta:
                    try:
                        cur.execute("SAVEPOINT men")
                        cur.execute(SELECT_MENCIONES + " ORDER BY mn.creado_en DESC LIMIT %s", (usuario, limite))
                        for f in cur.fetchall():
                            d = _mencion(f)
                            d["nueva"] = d.pop("_creado") > corte
                            items.append(d)
                        cur.execute("RELEASE SAVEPOINT men")
                    except Exception as e:   # sin la tabla todavía (primer arranque): la campanita sigue andando
                        cur.execute("ROLLBACK TO SAVEPOINT men")
                        print(f"[campanita] menciones: {e}")
                items.sort(key=lambda d: d.get("fecha") or "", reverse=True)
                items = items[:limite]
                cur.execute("""SELECT creado_en, metricas FROM registro_corridas
                               WHERE proceso = %s ORDER BY creado_en DESC LIMIT 1""", (WORKFLOW,))
                u = cur.fetchone()
                cur.execute("SELECT (SELECT count(*) FROM notificaciones_inpi) + (SELECT count(*) FROM cartera_novedades) AS n")  # solo para el mensaje de lista vacía
                total = cur.fetchone()["n"] + len([i for i in items if i.get("origen") == "men"])
        return {"items": items, "total": total,
                "ultima_actualizacion": u["creado_en"].isoformat() if u else None,
                "ultima_con_error": bool(u and (u["metricas"] or {}).get("error"))}

    @router.get("/api/notificaciones-inpi/resumen")
    def resumen(usuario: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                visto = _visto_hasta(cur, usuario)
                cur.execute(
                    "WITH c AS (SELECT COALESCE(%s, " + DESDE_SIN_VISTO + """) AS corte)
                       SELECT (SELECT count(*) FROM notificaciones_inpi, c
                               WHERE NOT carga_inicial AND creado_en > c.corte) AS inpi,
                              (SELECT count(*) FROM notificaciones_inpi, c
                               WHERE NOT carga_inicial AND creado_en > c.corte AND tipo_codigo = '1') AS titulos,
                              (SELECT corte FROM c) AS corte""",
                    (visto,),
                )
                r = dict(cur.fetchone())
                # Movimientos: se cuentan solo los que la campanita muestra (mismo filtro que la lista).
                cur.execute(ni.SELECT_MOVIMIENTOS + " AND nv.detectado_en > %s", (r["corte"],))
                r["movimientos"] = len(ni.filtrar_movimientos(cur.fetchall()))
                r["menciones"] = 0
                try:
                    cur.execute("SAVEPOINT men")
                    cur.execute("SELECT count(*) AS n FROM trabajos_menciones WHERE para = %s AND creado_en > %s",
                                (usuario, r["corte"]))
                    r["menciones"] = cur.fetchone()["n"]
                    cur.execute("RELEASE SAVEPOINT men")
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT men")
        return {"nuevas": r["inpi"] + r["movimientos"] + r["menciones"], "inpi": r["inpi"], "titulos": r["titulos"],
                "movimientos": r["movimientos"], "menciones": r["menciones"]}

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
    def pdf(id_notificacion: int, descargar: bool = True, usuario: str = Depends(verificar_login)):
        """El PDF guardado, como descarga con el nombre «<acta>-<marca>.pdf» (pedido del 19/11/2026)."""
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""SELECT n.acta, n.pdf, n.adjunto_url,
                                      COALESCE(NULLIF(cm.denominacion, ''), NULLIF(m.denominacion_inpi, ''),
                                               NULLIF(m.denominacion, '')) AS marca
                               FROM notificaciones_inpi n
                               LEFT JOIN cartera_marcas cm ON cm.acta = n.acta
                               LEFT JOIN marcas m ON m.acta = n.acta
                               WHERE n.id_notificacion = %s""", (id_notificacion,))
                f = cur.fetchone()
        if not f:
            raise HTTPException(404, "No existe esa notificación")
        if f["pdf"] is None:
            if f["adjunto_url"]:
                return RedirectResponse(f["adjunto_url"])
            raise HTTPException(404, "El INPI no mandó un PDF para esta notificación")
        return Response(bytes(f["pdf"]), media_type="application/pdf",
                        headers={"Content-Disposition": disposicion(f["acta"], f["marca"], descargar),
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
