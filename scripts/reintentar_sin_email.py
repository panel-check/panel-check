"""
Reintenta contra INPI el email de los leads reales que quedaron sin
conseguirlo (es_lead = true, email vacío) -- distinto de
reintentar_sin_verificar.py, que es para marcas donde ni siquiera se pudo
confirmar si son lead (es_lead IS NULL). Acá ya se sabe que es un lead
real, solo falta el email: casi siempre porque el WAF de INPI bloqueó
puntualmente la Grilla Digital o la descarga del PDF del Formulario en el
momento de la consulta (ver motivo_sin_email de cada fila).

Hasta ahora la única forma de resolverlas en lote era el botón
"Reintentar" del panel, un acta a la vez -- este script las reintenta
solas, con su propio cron (ver CRONS_DEFINIDOS en panel/app.py, aparece en
/crons como "Reintento de emails faltantes").

Uso:
    DATABASE_URL=... python3 reintentar_sin_email.py
    DATABASE_URL=... python3 reintentar_sin_email.py --limit 50 --delay 2
"""

import argparse
import os
import sys
import time
import traceback

import psycopg2

from registro import registrar
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
                WHERE es_lead = true AND (email IS NULL OR email = '')
                ORDER BY fecha_presentacion
                """
            )
            pendientes = cur.fetchall()

        if args.limit:
            pendientes = pendientes[: args.limit]
        print(f"Leads sin email a reintentar: {len(pendientes)}")

        s = crear_sesion()
        resueltos = 0
        siguen_sin_email = 0

        for i, fila in enumerate(pendientes, 1):
            acta = fila["acta"]
            info = revisar_acta(s, acta)

            if info["es_lead"] is not True:
                # No se pudo volver a confirmar nada esta vez (bloqueo/error de
                # conexión) -- se reintenta en la próxima corrida, sin tocar la
                # fila (no queremos pisar es_lead=true con None por un error
                # transitorio de esta consulta puntual).
                siguen_sin_email += 1
                print(f"  [{i}/{len(pendientes)}] acta {acta}: no se pudo reconsultar "
                      f"({info.get('motivo_sin_email') or 'sin detalle'})")
                conn.commit()  # cerrar la transacción del SELECT aunque no haya cambios
                time.sleep(args.delay)
                continue

            if not info["email"]:
                siguen_sin_email += 1
                print(f"  [{i}/{len(pendientes)}] acta {acta}: sigue sin email "
                      f"({info.get('motivo_sin_email') or 'sin detalle'})")
                # Igual guardamos motivo_sin_email actualizado (por si cambió)
                # y demás datos que puedan haberse conseguido en esta pasada.
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE marcas
                        SET motivo_sin_email = %s,
                            cuit = COALESCE(%s, cuit),
                            denominacion = COALESCE(NULLIF(denominacion, ''), %s),
                            tipo = COALESCE(NULLIF(tipo, ''), %s),
                            fecha_presentacion = COALESCE(fecha_presentacion, %s),
                            fecha_publicacion = COALESCE(%s, fecha_publicacion),
                            estado_tramite = COALESCE(%s, estado_tramite),
                            fecha_concesion = COALESCE(%s, fecha_concesion),
                            numero_disposicion = COALESCE(%s, numero_disposicion),
                            fecha_vencimiento_marca = COALESCE(%s, fecha_vencimiento_marca),
                            actualizado_en = now()
                        WHERE acta = %s
                        """,
                        (
                            info["motivo_sin_email"] or None, info.get("cuit"), info.get("denominacion_formulario"), info.get("tipo_formulario"), info.get("fecha_presentacion_formulario"),
                            info.get("fecha_publicacion"), info.get("estado_tramite"),
                            info.get("fecha_concesion"), info.get("numero_disposicion"),
                            info.get("fecha_vencimiento_marca"), acta,
                        ),
                    )
                conn.commit()
                time.sleep(args.delay)
                continue

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
                    SET email = %s, email_apoderado = %s, motivo_sin_email = %s,
                        lead_score = %s,
                        cuit = COALESCE(%s, cuit),
                        denominacion = COALESCE(NULLIF(denominacion, ''), %s),
                        tipo = COALESCE(NULLIF(tipo, ''), %s),
                        fecha_presentacion = COALESCE(fecha_presentacion, %s),
                        fecha_publicacion = COALESCE(%s, fecha_publicacion),
                        estado_tramite = COALESCE(%s, estado_tramite),
                        fecha_concesion = COALESCE(%s, fecha_concesion),
                        numero_disposicion = COALESCE(%s, numero_disposicion),
                        fecha_vencimiento_marca = COALESCE(%s, fecha_vencimiento_marca),
                        actualizado_en = now()
                    WHERE acta = %s
                    """,
                    (
                        info["email"], info["email_apoderado"] or None, None,
                        nuevo_score, info.get("cuit"), info.get("denominacion_formulario"), info.get("tipo_formulario"), info.get("fecha_presentacion_formulario"), info.get("fecha_publicacion"),
                        info.get("estado_tramite"), info.get("fecha_concesion"),
                        info.get("numero_disposicion"), info.get("fecha_vencimiento_marca"),
                        acta,
                    ),
                )
            conn.commit()
            resueltos += 1
            # No se imprime el email (dato personal de terceros) -- solo si
            # se consiguió o no.
            print(f"  [{i}/{len(pendientes)}] acta {acta}: email conseguido")

            time.sleep(args.delay)

        resumen = (
            f"Reintentados: {len(pendientes)}. Con email conseguido: {resueltos}. "
            f"Siguen sin email: {siguen_sin_email}."
        )
        print(f"::notice::{resumen}")
        print(f"\n{resumen}")
        registrar("reintentar_sin_email.yml", {
            "reintentados": len(pendientes), "emails_conseguidos": resueltos,
            "siguen": siguen_sin_email,
        }, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        linea = f"{type(e).__name__}: {e}".replace("\n", " ")
        print(f"::error::reintentar_sin_email falló: {linea}")
        traceback.print_exc()
        sys.exit(1)
