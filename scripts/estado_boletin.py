"""
Consulta el estado de un boletín en la tabla `boletines` (columna `estado`,
ver db/schema.sql: 'pendiente' | 'procesado' | 'error', o inexistente si
nunca se cargó). Se usa desde pipeline.yml antes de gastar Apify/Actions
en reprocesar un boletín que ya está importado -- agregado el 29/09/2026
tras encontrar que el modo automático del pipeline reprocesó el boletín
11125, que ya estaba cargado (no había ninguno nuevo publicado todavía).

Imprime una sola palabra a stdout: "procesado", "pendiente", "error" o
"no_existe". No es para uso interactivo, es para leer con $(...) desde
bash en el workflow.

Uso:
    DATABASE_URL=... python3 estado_boletin.py 11125
"""

import os
import sys

import psycopg2


def main():
    if len(sys.argv) != 2:
        sys.exit("Uso: estado_boletin.py <numero>")
    numero = sys.argv[1]

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT estado FROM boletines WHERE numero = %s", (numero,))
            fila = cur.fetchone()
            print(fila[0] if fila else "no_existe")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
