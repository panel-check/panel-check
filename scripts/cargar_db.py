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

from registro import registrar
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

            # Para el reporte: cuántos leads de este boletín ya los teníamos
            # antes por el escaneo directo de actas (contacto adelantado).
            actas_boletin = [r["acta"] for r in rows]
            cur.execute(
                "SELECT COUNT(*) FROM marcas WHERE acta = ANY(%s) AND fuente = 'escaneo_directo'",
                (actas_boletin,),
            )
            ya_por_escaneo = cur.fetchone()[0]

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
                    r.get("fecha_publicacion") or None,
                )
                for r in rows
            ]
            execute_values(
                cur,
                """
                INSERT INTO marcas (
                    acta, boletin, clase, tipo, denominacion, denominacion_inpi,
                    fecha_presentacion, titular, pais, cuit, matricula_agente, link,
                    caracter, es_lead, email, email_apoderado, lead_score, motivo_sin_email,
                    fecha_publicacion
                ) VALUES %s
                ON CONFLICT (acta) DO UPDATE SET
                    -- Agregado el 30/09/2026: boletin/clase/tipo/denominacion/
                    -- titular/pais/link/fecha_presentacion antes NO estaban en
                    -- este UPDATE (quedaban "de una sola escritura"). Eso rompía
                    -- al escaneo directo de actas (escanear_actas_nuevas.py):
                    -- carga el acta con boletin=NULL apenas se detecta (semanas
                    -- antes de publicarse), y cuando el boletín real la alcanzaba
                    -- se perdía la referencia para siempre. Con COALESCE(EXCLUDED, marcas)
                    -- el boletín "real" (que siempre trae estos datos) los
                    -- completa/corrige sin perder nada si por algún motivo
                    -- viniera vacío en esta corrida puntual.
                    boletin = COALESCE(EXCLUDED.boletin, marcas.boletin),
                    clase = COALESCE(EXCLUDED.clase, marcas.clase),
                    tipo = COALESCE(EXCLUDED.tipo, marcas.tipo),
                    denominacion = COALESCE(EXCLUDED.denominacion, marcas.denominacion),
                    titular = COALESCE(EXCLUDED.titular, marcas.titular),
                    pais = COALESCE(EXCLUDED.pais, marcas.pais),
                    link = COALESCE(EXCLUDED.link, marcas.link),
                    fecha_presentacion = COALESCE(EXCLUDED.fecha_presentacion, marcas.fecha_presentacion),
                    -- COALESCE: si el webservice falló en esta corrida, no borrar
                    -- un nombre ya recuperado (por escaneo o backfill_denominacion.py).
                    denominacion_inpi = COALESCE(EXCLUDED.denominacion_inpi, marcas.denominacion_inpi),
                    -- No pisar un CUIT que ya teníamos con NULL si esta corrida
                    -- no lo pudo encontrar (webservice de INPI es flaky, o esta
                    -- fila no es M/F y nunca intenta buscarlo).
                    cuit = COALESCE(EXCLUDED.cuit, marcas.cuit),
                    matricula_agente = EXCLUDED.matricula_agente,
                    -- Un lead que ya pasó a "apoderado/gestor" tras una oposición
                    -- (revisar_oposiciones.py) no vuelve a ser lead si se reimporta el boletín.
                    caracter = CASE WHEN marcas.representacion_posterior_oposicion IS TRUE
                                    THEN marcas.caracter ELSE EXCLUDED.caracter END,
                    es_lead = CASE WHEN marcas.representacion_posterior_oposicion IS TRUE
                                   THEN marcas.es_lead ELSE EXCLUDED.es_lead END,
                    email = EXCLUDED.email,
                    email_apoderado = EXCLUDED.email_apoderado,
                    lead_score = CASE WHEN marcas.representacion_posterior_oposicion IS TRUE
                                      THEN marcas.lead_score ELSE EXCLUDED.lead_score END,
                    motivo_sin_email = EXCLUDED.motivo_sin_email,
                    -- fecha_publicacion tampoco se pisa con NULL: no cambia con
                    -- el tiempo, así que si ya la teníamos no hace falta perderla.
                    fecha_publicacion = COALESCE(EXCLUDED.fecha_publicacion, marcas.fecha_publicacion),
                    actualizado_en = now()
                    -- "fuente" NO se toca acá a propósito: si el acta ya existía
                    -- por el escaneo directo, queremos que siga diciendo
                    -- 'escaneo_directo' (para poder medir cuánto adelantamos el
                    -- contacto) aunque el boletín la vuelva a cargar.
                    -- tuvo_oposicion / detalle_oposicion / revisado_oposicion_en NO
                    -- se tocan acá a propósito: los administra únicamente
                    -- scripts/revisar_oposiciones.py. Si el pipeline reprocesa un
                    -- boletín viejo no debe pisar un resultado ya detectado.
                    -- Mismo criterio para estado_tramite / fecha_concesion /
                    -- numero_disposicion / fecha_vencimiento_marca: los administra
                    -- únicamente scripts/revisar_estado.py.
                """,
                valores,
            )
        conn.commit()
        print(f"Cargadas/actualizadas {len(rows)} marcas del boletín {args.boletin}.")
        leads = sum(1 for r in rows if parse_bool(r.get("es_lead")) is True)
        registrar("pipeline.yml", {
            "boletines": 1, "marcas_cargadas": len(rows), "leads": leads,
            "leads_con_email": sum(1 for r in rows if parse_bool(r.get("es_lead")) is True and r.get("email")),
            "sin_verificar": sum(1 for r in rows if parse_bool(r.get("es_lead")) is None),
            "ya_detectados_por_escaneo": ya_por_escaneo,
            "boletin": args.boletin,
        }, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
