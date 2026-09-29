"""
Diagnóstico puntual: lista sesiones activas y locks sobre la tabla `marcas`
para entender por qué algunas consultas (ej. /api/clases, /api/marcas) se
quedan colgadas. Uso único, se puede borrar después.
"""
import os
import sys

import psycopg2
import psycopg2.extras


def main():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL")

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT pid, state, wait_event_type, wait_event,
                   now() - xact_start AS duracion_transaccion,
                   now() - query_start AS duracion_query,
                   left(query, 150) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
            ORDER BY xact_start ASC NULLS LAST
            """
        )
        print("::notice::--- pg_stat_activity ---")
        for r in cur.fetchall():
            print(f"::notice::pid={r['pid']} state={r['state']} wait={r['wait_event_type']}/{r['wait_event']} "
                  f"tx={r['duracion_transaccion']} query_dur={r['duracion_query']} sql={r['query']!r}")

        cur.execute(
            """
            SELECT l.pid, l.mode, l.granted, l.relation::regclass AS tabla, a.query_start,
                   left(a.query, 150) AS query
            FROM pg_locks l
            JOIN pg_stat_activity a ON a.pid = l.pid
            WHERE l.relation = 'marcas'::regclass
            ORDER BY l.granted, l.pid
            """
        )
        print("::notice::--- pg_locks sobre marcas ---")
        for r in cur.fetchall():
            print(f"::notice::pid={r['pid']} mode={r['mode']} granted={r['granted']} tabla={r['tabla']} "
                  f"query_start={r['query_start']} sql={r['query']!r}")

    conn.close()


if __name__ == "__main__":
    main()
