"""
Carga un CSV ya parseado y completado (salida de completar_mixtas.py) a Postgres
(Railway), sin duplicar actas. Es "upsert": si el acta ya existe, actualiza.

Requiere la variable de entorno DATABASE_URL (la da Railway).

Uso:
    python3 cargar_db.py --in 11121_completo.csv --boletin 11121 --fecha 2025-11-12
"""

import argparse
import csv
import os
import sys

import psycopg2
from psycopg2.extras import execute_values


def parse_fecha(fecha_str: str):
    """Convierte 'DD/MM/AAAA HH:MM:SS.mmm' a un timestamp ISO, o None si no matchea."""
    import re
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})\s+([\d:.]+)", fecha_str or "")
    if not m:
        return None
    d, mo, y, hms = m.groups()
    return f"{y}-{mo}-{d} {hms}"


def parse_bool(valor: str):
    """'True'/'False'/'' -> True/False/None. csv.DictWriter escribe los booleanos
    de Python como los strings 'True'/'False'/'None'."""
    if valor is None:
        return None
    v = valor.strip()
    if v == "True":
        return True
    if v == "False":
        return False
    return None  # "" o "None" -> no verificado


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--boletin", required=True, help="número de boletín")
    ap.add_argument("--fecha", help="fecha de publicación del boletín, YYYY-MM-DD")
    ap.add_argument("--pdf-url", default=None)
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")

    with open(args.in_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"{args.in_path} está vacío, nada para cargar")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            with open(os.path.join(os.path.dirname(__file__), "..", "db", "schema.sql")) as f:
                cur.execute(f.read())

            cur.execute(
                """
                INSERT INTO boletines (numero, fecha, pdf_url, total_marcas, estado)
                VALUES (%s, %s, %s, %s, 'procesado')
                ON CONFLICT (numero) DO UPDATE SET
                    total_marcas = EXCLUDED.total_marcas,
                    estado = 'procesado',
                    procesado_en = now()
                """,
                (args.boletin, args.fecha, args.pdf_url, len(rows)),
            )

            valores = [
                (
                    r["acta"],
                    args.boletin,
                    int(r["clase"]) if r.get("clase") else None,
                    r.get("tipo"),
                    r.get("denominacion") or None,
                    r.get("denominacion_inpi") or None,
                    parse_fecha(r.get("fecha")),
                    r.get("titular"),
                    r.get("pais"),
                    r.get("cuit") or None,
                    r.get("matricula_agente") or None,
                    r.get("link"),
                    r.get("caracter") or None,
                    parse_bool(r.get("es_lead")),
                    r.get("email") or None,
                    r.get("email_apoderado") or None,
                    int(r["lead_score"]) if r.get("lead_score") else 0,
                    r.get("motivo_sin_email") or None,
                )
                for r in rows
            ]
            execute_values(
                cur,
                """
                INSERT INTO marcas (
                    acta, boletin, clase, tipo, denominacion, denominacion_inpi,
                    fecha_presentacion, titular, pais, cuit, matricula_agente, link,
                    caracter, es_lead, email, email_apoderado, lead_score, motivo_sin_email
                ) VALUES %s
                ON CONFLICT (acta) DO UPDATE SET
                    denominacion_inpi = EXCLUDED.denominacion_inpi,
                    -- No pisar un CUIT que ya teníamos con NULL si esta corrida
                    -- no lo pudo encontrar (webservice de INPI es flaky, o esta
                    -- fila no es M/F y nunca intenta buscarlo).
                    cuit = COALESCE(EXCLUDED.cuit, marcas.cuit),
                    matricula_agente = EXCLUDED.matricula_agente,
                    caracter = EXCLUDED.caracter,
                    es_lead = EXCLUDED.es_lead,
                    email = EXCLUDED.email,
                    email_apoderado = EXCLUDED.email_apoderado,
                    lead_score = EXCLUDED.lead_score,
                    motivo_sin_email = EXCLUDED.motivo_sin_email,
                    actualizado_en = now()
                """,
                valores,
            )
        conn.commit()
        print(f"Cargadas/actualizadas {len(rows)} marcas del boletín {args.boletin}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
