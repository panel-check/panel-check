"""
Backfill puntual (corre una vez, no forma parte del cron): para las actas
que ya tienen marca_oponente_denominacion/numero_registro (el fundamento no
citó la ACTA directamente) pero todavía no tienen actas_marca_oponente,
reintenta resolver_marca_oponente() ahora que existe (agregado el
29/09/2026, ver validar_leads.py) — sin volver a bajar el Formulario, solo
la búsqueda por denominación en GrillaMarcasAvanzada.

Uso:
    DATABASE_URL=... python3 backfill_resolver_marca_oponente.py
"""

import os
import sys

import psycopg2
import psycopg2.extras

from validar_leads import crear_sesion, resolver_marca_oponente


def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT acta, titular, marca_oponente_denominacion, marca_oponente_numero_registro
                FROM marcas
                WHERE marca_oponente_denominacion IS NOT NULL
                  AND actas_marca_oponente IS NULL
                """
            )
            pendientes = cur.fetchall()

        print(f"::notice::Marcas oponentes solo con denominacion (sin ACTA resuelta): {len(pendientes)}")

        s = crear_sesion()
        resueltas = 0
        for i, fila in enumerate(pendientes, 1):
            detalle = {
                "marca_oponente_denominacion": fila["marca_oponente_denominacion"],
                "marca_oponente_numero_registro": fila["marca_oponente_numero_registro"],
            }
            nuevo = resolver_marca_oponente(s, detalle)
            if nuevo.get("actas_marca_oponente"):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE marcas
                        SET actas_marca_oponente = %s,
                            marca_oponente_denominacion = NULL,
                            marca_oponente_numero_registro = NULL
                        WHERE acta = %s
                        """,
                        (nuevo["actas_marca_oponente"], fila["acta"]),
                    )
                conn.commit()
                resueltas += 1
                print(f"::notice::  [{i}/{len(pendientes)}] acta {fila['acta']} ({fila['titular']}): resuelto -> acta {nuevo['actas_marca_oponente']}")
            else:
                conn.commit()  # commit "vacío" — ver revisar_estado.py
                print(f"::notice::  [{i}/{len(pendientes)}] acta {fila['acta']} ({fila['titular']}): sigue sin poder resolverse (denominacion '{fila['marca_oponente_denominacion']}'), se deja el boton Buscar")

        print(f"::notice::Revisadas: {len(pendientes)}. Resueltas a ACTA directa: {resueltas}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
