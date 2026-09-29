"""
Uso puntual, una sola vez: corrige marcas que quedaron con
`tuvo_oposicion = true` por el bug de detectar_oposicion() detectado el
2026-09-29 (ver validar_leads.detectar_oposicion) — el chequeo original no
miraba la FECHA de la fila de "VISTA"/"OPO" en Grilla Digital, así que una
vista vieja de una presentación ANTERIOR (ya resuelta, antes de que la marca
se volviera a publicar) quedaba marcada como oposición pendiente. Caso real
que lo confirmó: acta 4700141, "Vista de Marcas" del 16/07/2026, pero la
publicación vigente ("Hoja Publicacion") es del 23/09/2026 — esa vista ya
estaba solucionada.

Este script vuelve a consultar Grilla Digital para cada marca que hoy está
marcada `tuvo_oposicion = true`, y re-evalúa con la lógica corregida
(detectar_oposicion ahora filtra por fecha_publicacion). Si el resultado
cambia, actualiza tuvo_oposicion/detalle_oposicion. No toca las que ya daban
`false` (esas no podían tener falsos positivos con el bug viejo).

Uso:
    DATABASE_URL=... python3 corregir_oposiciones_viejas.py
    DATABASE_URL=... python3 corregir_oposiciones_viejas.py --limit 20
"""

import argparse
import os
import sys
import time
import traceback

import psycopg2
import psycopg2.extras

from validar_leads import buscar_archivos_grilla, crear_sesion, detectar_oposicion


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT acta, titular, fecha_publicacion, detalle_oposicion
                FROM marcas
                WHERE tuvo_oposicion = true
                ORDER BY fecha_publicacion
                """
            )
            candidatas = cur.fetchall()

        if args.limit:
            candidatas = candidatas[: args.limit]
        print(f"Marcas marcadas con oposición/vista a re-chequear: {len(candidatas)}")

        s = crear_sesion()
        corregidas = 0
        confirmadas = 0
        sin_poder_revisar = 0

        for i, fila in enumerate(candidatas, 1):
            acta = fila["acta"]
            archivos = buscar_archivos_grilla(s, acta)
            if not archivos:
                sin_poder_revisar += 1
                print(f"  [{i}/{len(candidatas)}] acta {acta}: bloqueado/sin respuesta, se puede reintentar después")
                time.sleep(args.delay)
                continue

            fecha_pub = fila["fecha_publicacion"].isoformat() if fila["fecha_publicacion"] else None
            tuvo_oposicion, detalle = detectar_oposicion(archivos, fecha_pub)

            if tuvo_oposicion:
                confirmadas += 1
                print(f"  [{i}/{len(candidatas)}] acta {acta} ({fila['titular']}): sigue siendo oposición real — {detalle}")
            else:
                corregidas += 1
                print(f"  [{i}/{len(candidatas)}] acta {acta} ({fila['titular']}): era un falso positivo "
                      f"(vista/oposición anterior a la publicación vigente) — corregido")

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE marcas
                    SET tuvo_oposicion = %s, detalle_oposicion = %s
                    WHERE acta = %s
                    """,
                    (tuvo_oposicion, detalle or None, acta),
                )
            conn.commit()
            time.sleep(args.delay)

        resumen = (
            f"Re-chequeadas: {len(candidatas)}. Siguen con oposición real: {confirmadas}. "
            f"Corregidas (falso positivo): {corregidas}. No se pudieron revisar: {sin_poder_revisar}."
        )
        print(f"::notice::{resumen}")
        print(f"\n{resumen}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        linea = f"{type(e).__name__}: {e}".replace("\n", " ")
        print(f"::error::corregir_oposiciones_viejas falló: {linea}")
        traceback.print_exc()
        sys.exit(1)
