"""
Backfill de `fecha_publicacion` para leads ya cargados en Postgres antes de
que existiera esa columna (ver revisar_oposiciones.py / validar_leads.py).

Solo tiene sentido para marcas con `es_lead = true`: son las únicas para las
que `validar_leads.py` alguna vez consultó Grilla Digital (a las que tienen
agente/apoderado nunca se les mira Grilla Digital, así que no hay nada para
recuperar ahí).

De paso, si en la misma consulta aparece ya una oposición/vista cargada, se
guarda (`tuvo_oposicion = true`) — es un dato real, ya pasó. Pero si NO
aparece ninguna, deliberadamente NO se marca `tuvo_oposicion = false`: el
plazo de 30 días para oponerse puede no haber vencido todavía, así que decidir
"no tuvo oposición" en este momento sería prematuro. Eso queda en manos de
`revisar_oposiciones.py`, que sí sabe esperar los 33 días antes de decidir.

Uso:
    DATABASE_URL=... python3 backfill_fecha_publicacion.py [--limit N] [--delay 1.5]
"""

import argparse
import os
import sys
import time

import psycopg2
import psycopg2.extras

from validar_leads import buscar_archivos_grilla, crear_sesion, detectar_oposicion, fecha_publicacion_de_archivos


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="máximo de actas a revisar en esta corrida")
    ap.add_argument("--delay", type=float, default=1.5, help="segundos de espera entre actas")
    args = ap.parse_args()

    database_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not database_url:
        print("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT acta FROM marcas
            WHERE es_lead = true AND fecha_publicacion IS NULL
            ORDER BY fecha_presentacion
            """
        )
        pendientes = cur.fetchall()

    if args.limit:
        pendientes = pendientes[: args.limit]
    print(f"Leads sin fecha_publicacion a revisar: {len(pendientes)}")

    s = crear_sesion()
    actualizadas = 0
    sin_publicar_aun = 0
    con_oposicion = 0

    for i, fila in enumerate(pendientes, 1):
        acta = fila["acta"]
        archivos = buscar_archivos_grilla(s, acta)
        if not archivos:
            print(f"  [{i}/{len(pendientes)}] acta {acta}: bloqueado/sin respuesta, se reintenta la próxima corrida")
            time.sleep(args.delay)
            continue

        fecha_publicacion = fecha_publicacion_de_archivos(archivos)
        if not fecha_publicacion:
            sin_publicar_aun += 1
            print(f"  [{i}/{len(pendientes)}] acta {acta}: todavía no tiene 'Hoja Publicacion' en Grilla Digital")
            time.sleep(args.delay)
            continue

        tuvo_oposicion, detalle = detectar_oposicion(archivos)

        with conn.cursor() as cur:
            if tuvo_oposicion:
                cur.execute(
                    """
                    UPDATE marcas
                    SET fecha_publicacion = %s, tuvo_oposicion = true,
                        detalle_oposicion = %s, revisado_oposicion_en = now()
                    WHERE acta = %s
                    """,
                    (fecha_publicacion, detalle, acta),
                )
                con_oposicion += 1
            else:
                # no marcamos tuvo_oposicion = false acá: puede que el plazo
                # de 30 días todavía no haya vencido (ver docstring arriba)
                cur.execute(
                    "UPDATE marcas SET fecha_publicacion = %s WHERE acta = %s",
                    (fecha_publicacion, acta),
                )
        conn.commit()
        actualizadas += 1
        print(f"  [{i}/{len(pendientes)}] acta {acta}: fecha_publicacion={fecha_publicacion}"
              f"{' (¡con oposición/vista!)' if tuvo_oposicion else ''}")
        time.sleep(args.delay)

    conn.close()
    print(
        f"\nListo. Actualizadas: {actualizadas}. Todavía sin 'Hoja Publicacion': {sin_publicar_aun}. "
        f"Con oposición ya detectada: {con_oposicion}."
    )


if __name__ == "__main__":
    main()
