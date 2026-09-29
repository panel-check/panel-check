"""Diagnóstico puntual: vuelca el fundamento_oposicion COMPLETO (sin
truncar) de las oposiciones ya detectadas, para diseñar el regex que
extrae la marca/número del oponente. Se borra después de usarlo."""
import os
import sys

import psycopg2
import psycopg2.extras

dsn = os.environ.get("DATABASE_URL")
if not dsn:
    sys.exit("Falta DATABASE_URL")

conn = psycopg2.connect(dsn)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute(
        """
        SELECT acta, oponente_nombre, fundamento_oposicion
        FROM marcas
        WHERE fundamento_oposicion IS NOT NULL
        ORDER BY acta
        """
    )
    for f in cur.fetchall():
        texto = f["fundamento_oposicion"] or ""
        print(f"::notice::=== acta {f['acta']} ({f['oponente_nombre']}) — {len(texto)} caracteres ===")
        paso = 1400
        for i in range(0, len(texto), paso):
            print(f"::notice::[{i}:{i+paso}] {texto[i:i+paso]}")
conn.close()
