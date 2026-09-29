"""
Uso único: termina una sesión puntual de Postgres por pid (pg_terminate_backend),
para destrabar la cola de locks sobre `marcas` cuando quedó una sesión zombie
esperando un ALTER TABLE que ya no tiene sentido (de un deploy viejo, reemplazado).

Uso:
    DATABASE_URL=... python3 terminar_backend.py <pid> [<pid> ...]
"""
import os
import sys

import psycopg2


def main():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL")
    pids = [int(p) for p in sys.argv[1:]]
    if not pids:
        sys.exit("Pasá al menos un pid")

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        for pid in pids:
            cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
            ok = cur.fetchone()[0]
            print(f"::notice::pg_terminate_backend({pid}) -> {ok}")
    conn.close()


if __name__ == "__main__":
    main()
