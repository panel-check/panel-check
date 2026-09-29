"""Backfill puntual (corre una vez, no forma parte del cron): re-parsea
marca_oponente_* a partir del fundamento_oposicion YA guardado (sin volver
a bajar el PDF) para las actas que quedaron sin nada reconocible con las
regex viejas -- agregadas el 29/09/2026 (RE_ACTA_REFERENCIA,
RE_MARCA_OPONENTE_REGISTROS) tras encontrar 2 redacciones reales que no
matcheaban ("actas de referencia X" y "Registros N° X, Y y Z").

Uso:
    DATABASE_URL=... python3 backfill_reparsear_marca_oponente.py
"""

import os
import sys

import psycopg2
import psycopg2.extras

from validar_leads import crear_sesion, parsear_marca_oponente, resolver_marca_oponente


def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT acta, titular, fundamento_oposicion
                FROM marcas
                WHERE fundamento_oposicion IS NOT NULL
                  AND actas_marca_oponente IS NULL
                  AND marca_oponente_denominacion IS NULL
                """
            )
            pendientes = cur.fetchall()

        print(f"::notice::Fundamentos guardados sin marca oponente reconocida: {len(pendientes)}")

        s = crear_sesion()
        resueltas = 0
        for i, fila in enumerate(pendientes, 1):
            acta = fila["acta"]
            detalle = parsear_marca_oponente(fila["fundamento_oposicion"], acta_propia=acta)
            if not detalle:
                print(f"::notice::  [{i}/{len(pendientes)}] acta {acta}: sigue sin match reconocible")
                conn.commit()  # commit "vacío" — ver revisar_estado.py
                continue
            detalle = resolver_marca_oponente(s, detalle)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE marcas
                    SET actas_marca_oponente = %s, marca_oponente_denominacion = %s,
                        marca_oponente_numero_registro = %s
                    WHERE acta = %s
                    """,
                    (
                        detalle.get("actas_marca_oponente"),
                        detalle.get("marca_oponente_denominacion"),
                        detalle.get("marca_oponente_numero_registro"),
                        acta,
                    ),
                )
            conn.commit()
            resueltas += 1
            # No se imprime el titular (dato personal de terceros); el
            # detalle de la marca oponente sí se imprime porque es
            # información de una marca (denominación/número de registro),
            # no datos personales.
            print(f"::notice::  [{i}/{len(pendientes)}] acta {acta}: {detalle}")

        print(f"::notice::Revisadas: {len(pendientes)}. Con marca oponente reconocida ahora: {resueltas}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
