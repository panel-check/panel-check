"""Diagnostico descartable: ver que quedo guardado en marca_oponente_* /
actas_marca_oponente para las 2 actas recien backfill  (4760629 ARGENTOS
FEST, 4757941), para confirmar si resolver_marca_oponente() encontro un
link directo o si el fundamento usa una redaccion ("Registros N°...",
plural, sin la palabra ACTA) que las regex actuales no capturan."""
import os

import psycopg2
import psycopg2.extras

dsn = os.environ["DATABASE_URL"]
conn = psycopg2.connect(dsn)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute(
        """
        SELECT acta, titular, oponente_nombre, fundamento_oposicion,
               actas_marca_oponente, marca_oponente_denominacion,
               marca_oponente_numero_registro
        FROM marcas
        WHERE acta IN ('4760629', '4757941')
        """
    )
    for fila in cur.fetchall():
        print(f"::notice::--- acta {fila['acta']} ({fila['titular']}) ---")
        print(f"::notice::  oponente_nombre: {fila['oponente_nombre']}")
        print(f"::notice::  fundamento_oposicion: {fila['fundamento_oposicion']}")
        print(f"::notice::  actas_marca_oponente: {fila['actas_marca_oponente']}")
        print(f"::notice::  marca_oponente_denominacion: {fila['marca_oponente_denominacion']}")
        print(f"::notice::  marca_oponente_numero_registro: {fila['marca_oponente_numero_registro']}")
conn.close()
