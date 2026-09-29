"""
Reintenta contra INPI las marcas que quedaron "sin verificar"
(es_lead IS NULL): el pipeline principal (validar_leads.py, paso 5) no pudo
confirmar si tienen agente/apoderado, casi siempre porque el WAF de INPI
bloqueó esa consulta puntual o hubo un timeout. Hasta ahora la única forma
de resolverlas era el botón "Reintentar" del panel, una acta a la vez — este
script las reintenta solas, en lote, con su propio cron (ver
CRONS_DEFINIDOS en panel/app.py, aparece en /crons como "Reintento de
marcas sin verificar").

Por qué es un paso separado del pipeline: el pipeline ya reintenta cada
acta 3 veces (ver _get_con_reintentos) en el momento de procesar el
boletín, así que si sigue fallando ahí es porque el bloqueo dura más que
esos segundos — conviene esperar y probar de nuevo más tarde, no en el
momento, para no alargar la corrida del boletín.

Uso:
    DATABASE_URL=... python3 reintentar_sin_verificar.py
    DATABASE_URL=... python3 reintentar_sin_verificar.py --limit 50 --delay 2
"""

import argparse
import os
import sys
import time
import traceback

import psycopg2
import psycopg2.extras

from validar_leads import calcular_lead_score, crear_sesion, revisar_acta


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="tope de actas a reintentar en esta corrida")
    ap.add_argument("--delay", type=float, default=2.0, help="segundos entre acta y acta")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT acta, matricula_agente FROM marcas
                WHERE es_lead IS NULL
                ORDER BY fecha_presentacion
                """
            )
            pendientes = cur.fetchall()

        if args.limit:
            pendientes = pendientes[: args.limit]
        print(f"Marcas sin verificar a reintentar: {len(pendientes)}")

        s = crear_sesion()
        resueltas = 0
        siguen_sin_verificar = 0

        for i, fila in enumerate(pendientes, 1):
            acta = fila["acta"]
            info = revisar_acta(s, acta)

            if info["es_lead"] is None:
                # Sigue bloqueada/con error — se reintenta en la próxima corrida.
                siguen_sin_verificar += 1
                print(f"  [{i}/{len(pendientes)}] acta {acta}: sigue sin poder verificarse "
                      f"({info.get('motivo_sin_email') or 'sin detalle'})")
                time.sleep(args.delay)
                continue

            # calcular_lead_score en validar_leads.py toma la fila completa
            # (dict con matricula_agente/es_lead/email); armamos ese dict acá.
            fila_para_score = {
                "matricula_agente": fila["matricula_agente"],
                "es_lead": info["es_lead"],
                "email": info["email"],
            }
            nuevo_score = calcular_lead_score(fila_para_score)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE marcas
                    SET caracter = %s, es_lead = %s, email = %s, email_apoderado = %s,
                        motivo_sin_email = %s, lead_score = %s,
                        cuit = COALESCE(%s, cuit),
                        fecha_publicacion = COALESCE(%s, fecha_publicacion),
                        estado_tramite = COALESCE(%s, estado_tramite),
                        fecha_concesion = COALESCE(%s, fecha_concesion),
                        numero_disposicion = COALESCE(%s, numero_disposicion),
                        fecha_vencimiento_marca = COALESCE(%s, fecha_vencimiento_marca),
                        actualizado_en = now()
                    WHERE acta = %s
                    """,
                    (
                        info["caracter"], info["es_lead"], info["email"] or None,
                        info["email_apoderado"] or None, info["motivo_sin_email"] or None,
                        nuevo_score, info.get("cuit"), info.get("fecha_publicacion"),
                        info.get("estado_tramite"), info.get("fecha_concesion"),
                        info.get("numero_disposicion"), info.get("fecha_vencimiento_marca"),
                        acta,
                    ),
                )
            conn.commit()
            resueltas += 1
            print(f"  [{i}/{len(pendientes)}] acta {acta}: resuelta — es_lead={info['es_lead']} "
                  f"email={'sí' if info['email'] else 'no'}")

            time.sleep(args.delay)

        resumen = (
            f"Reintentadas: {len(pendientes)}. Resueltas: {resueltas}. "
            f"Siguen sin verificar: {siguen_sin_verificar}."
        )
        # ::notice:: además de stdout, mismo truco que en los otros scripts
        # (backfill_fecha_publicacion.py, revisar_estado.py) para poder leer
        # el resultado por la API de GitHub sin blob storage.
        print(f"::notice::{resumen}")
        print(f"\n{resumen}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        linea = f"{type(e).__name__}: {e}".replace("\n", " ")
        print(f"::error::reintentar_sin_verificar falló: {linea}")
        traceback.print_exc()
        sys.exit(1)
