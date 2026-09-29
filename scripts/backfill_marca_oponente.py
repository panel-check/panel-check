"""
Backfill puntual (corre una vez): para las oposiciones que YA tienen
fundamento_oposicion guardado (de antes del 29/09/2026, cuando se agregó
parsear_marca_oponente), re-parsea ese texto YA GUARDADO en la base para
sacar actas_marca_oponente / marca_oponente_denominacion / numero_registro
— no hace falta volver a bajar el PDF de INPI, el texto ya lo tenemos.

Uso:
    DATABASE_URL=... python3 backfill_marca_oponente.py
"""
import os
import sys

import psycopg2
import psycopg2.extras

from validar_leads import parsear_marca_oponente


def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT acta, fundamento_oposicion
                FROM marcas
                WHERE fundamento_oposicion IS NOT NULL
                  AND actas_marca_oponente IS NULL
                  AND marca_oponente_denominacion IS NULL
                """
            )
            filas = cur.fetchall()

        print(f"::notice::Fundamentos ya guardados sin marca_oponente parseada: {len(filas)}")
        completadas = 0
        for f in filas:
            detalle = parsear_marca_oponente(f["fundamento_oposicion"], f["acta"])
            if not detalle:
                print(f"::notice::  acta {f['acta']}: no se encontró ninguna marca/acta citada en el fundamento")
                continue
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
                        f["acta"],
                    ),
                )
            conn.commit()
            completadas += 1
            print(f"::notice::  acta {f['acta']}: {detalle}")

        print(f"::notice::Completadas: {completadas}/{len(filas)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
