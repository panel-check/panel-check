"""Diagnóstico puntual: vuelca el detalle de oposición guardado para las
actas con tuvo_oposicion = true, para confirmar que el backfill funcionó.
Se borra después de usarlo."""
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
        SELECT acta, titular, oponente_nombre, oponente_tipo_doc, oponente_numero_doc,
               oponente_cuit, fundamento_oposicion
        FROM marcas
        WHERE tuvo_oposicion = true
        ORDER BY fecha_publicacion DESC NULLS LAST
        """
    )
    for f in cur.fetchall():
        fundamento = (f["fundamento_oposicion"] or "")[:200]
        print(f"::notice::acta {f['acta']} ({f['titular']}) oponente={f['oponente_nombre']!r} "
              f"doc={f['oponente_tipo_doc']} {f['oponente_numero_doc']} cuit={f['oponente_cuit']} "
              f"fundamento[:200]={fundamento!r}")
conn.close()
