"""
Notificaciones del INPI → campanita 🔔 del panel (19/11/2026).

Consulta ConsultaNotificacionesAdjunto del webservice de Autogestión con el token de la delegación de Pame
(secret INPI_HASH_DELEGACION), guarda las notificaciones nuevas en `notificaciones_inpi`, baja y guarda el PDF
de cada una (el link del INPI trae una clave y puede vencer) y avisa por Telegram (tema «🔔 Notificaciones INPI»)
las importantes: títulos digitales, vistas y vistas a la oposición. Ver panel/notificaciones_inpi.py.

La primera vez (tabla vacía) carga el último año completo, marcado como «carga inicial»: se ve en la campanita
pero no cuenta como nuevo ni manda avisos. Después mira los últimos --dias días (por defecto 15: cubre una
corrida perdida o una notificación que el INPI carga con fecha atrasada; las repetidas se ignoran).

Los logs de GitHub son públicos: solo se imprimen cantidades, nunca actas ni nombres.
"""

import argparse
import datetime as _dt
import html
import os
import sys
import time

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import notificaciones_inpi as ni  # noqa: E402
from registro import registrar  # noqa: E402

PANEL = "https://panel.registrodemimarca.com.ar"


def avisar_telegram(cur, ids: list[int]) -> int:
    if not ids:
        return 0
    try:
        from telegram_avisos import enviar
    except Exception as e:   # sin el módulo no se corta la sincronización
        print(f"Telegram no disponible: {e}")
        return 0
    cur.execute(ni.SELECT_DETALLE + " WHERE n.id_notificacion = ANY(%s) ORDER BY n.fecha_carga", (ids,))
    enviados = 0
    for f in map(ni.describir, cur.fetchall()):
        if not f["importante"]:
            continue
        marca = html.escape(f["marca"] or "(sin nombre)")
        quien = html.escape(f["cliente"] or f["titular"] or "")
        link = f"{PANEL}/clientes?cliente={f['cliente_id']}" if f["cliente_id"] else PANEL
        clase = f" (clase {f['clase']})" if f["clase"] else ""
        lineas = [f"{f['icono']} <b>{html.escape(f['tipo'])}</b> — {marca}{clase}",
                  f"Acta {html.escape(f['acta'])}" + (f" · {quien}" if quien else "")]
        if not f["cliente_id"]:
            lineas.append("⚠️ El acta no está en Clientes.")
        lineas.append(f'<a href="{link}">Abrir en el panel</a> · PDF en la campanita 🔔')
        texto = "\n".join(lineas)
        if enviar(texto, tema="inpi", conn=cur.connection):
            enviados += 1
    return enviados


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dias", type=int, default=15)
    p.add_argument("--max-pdf", type=int, default=150)
    a = p.parse_args()

    hash_delegacion = (os.environ.get("INPI_HASH_DELEGACION") or "").strip()
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not hash_delegacion or not dsn:
        print("::error::Faltan INPI_HASH_DELEGACION o DATABASE_URL")
        sys.exit(1)

    t0 = time.time()
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    m = {}
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        ni.asegurar_tablas(cur)
        cur.execute("SELECT count(*) AS n FROM notificaciones_inpi")
        inicial = cur.fetchone()["n"] == 0
        desde = "" if inicial else (_dt.date.today() - _dt.timedelta(days=a.dias)).isoformat()

        try:
            filas = ni.consultar(hash_delegacion, desde=desde)
        except Exception as e:
            print(f"::error::No se pudo consultar el webservice del INPI: {type(e).__name__}")
            registrar("notificaciones_inpi.yml", {"error": 1, "segundos": round(time.time() - t0)}, conn=conn)
            sys.exit(1)

        with conn.cursor() as c2:
            nuevos = ni.guardar(c2, filas, carga_inicial=inicial)
        print(f"Notificaciones recibidas: {len(filas)} · nuevas: {len(nuevos)}"
              f"{' (carga inicial del último año)' if inicial else ''}")

        # PDFs pendientes (de esta corrida o de una anterior que no pudo bajarlos)
        cur.execute("""SELECT id_notificacion, adjunto_url FROM notificaciones_inpi
                       WHERE pdf IS NULL AND adjunto_url <> '' ORDER BY fecha_carga DESC LIMIT %s""", (a.max_pdf,))
        pendientes = cur.fetchall()
        bajados = fallidos = 0
        for f in pendientes:
            datos, error = ni.bajar_pdf(f["adjunto_url"])
            with conn.cursor() as c2:
                if datos:
                    c2.execute("UPDATE notificaciones_inpi SET pdf = %s, pdf_bajado_en = now(), pdf_error = NULL "
                               "WHERE id_notificacion = %s", (psycopg2.Binary(datos), f["id_notificacion"]))
                    bajados += 1
                else:
                    c2.execute("UPDATE notificaciones_inpi SET pdf_error = %s WHERE id_notificacion = %s",
                               (error, f["id_notificacion"]))
                    fallidos += 1
            time.sleep(2)
        print(f"PDF bajados: {bajados} · con error: {fallidos}")

        avisos = 0 if inicial else avisar_telegram(cur, nuevos)
        print(f"Avisos por Telegram: {avisos}")

        m = {"recibidas": len(filas), "nuevas": len(nuevos), "pdf_bajados": bajados, "pdf_error": fallidos,
             "avisos": avisos, "carga_inicial": inicial, "segundos": round(time.time() - t0)}
        registrar("notificaciones_inpi.yml", m, conn=conn)
    conn.close()


if __name__ == "__main__":
    main()
