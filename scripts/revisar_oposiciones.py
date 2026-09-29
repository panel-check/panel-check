"""
Paso 6 (nuevo, corre por separado del pipeline principal) — Reviso, para los
leads reales (sin agente/apoderado) cuya publicación en el boletín ya tiene
al menos N días, si en su Grilla Digital apareció una oposición de tercero o
una vista de oficio de INPI durante la ventana de 30 días para oponerse.

Por qué es un paso separado: el pipeline principal (parse_boletin.py ->
completar_mixtas.py -> validar_leads.py -> cargar_db.py) corre apenas se
publica un boletín nuevo, cuando todavía no pasó tiempo suficiente para que
haya oposiciones. Este script corre solo (con su propio cron en GitHub
Actions) y mira boletines de semanas anteriores, no el más reciente.

Cómo se detecta: reutiliza la misma llamada a Grilla Digital que ya hacía
validar_leads.py para buscar el email (Home/GrillaDigital + GrillaDigitales),
sin pedir nada nuevo a INPI. Ahí:
  - la fila con Indice == "Hoja Publicacion" da la fecha real de publicación
    de ESE expediente (se guarda en fecha_publicacion la primera vez que se
    ve, en validar_leads.py — este script solo la LEE para decidir a quién
    revisar, no la vuelve a buscar).
  - si aparece una fila nueva con "OPO"/"VISTA"/"OPOSICION" en Indice o
    Referencia (ver TERMINOS_OPOSICION en validar_leads.py), es una
    oposición de tercero o una vista — ejemplo real confirmado a mano:
    Indice="Recibo de Ingreso", Referencia="Opo. de Marcas".

Marca cada acta revisada con revisado_oposicion_en = now(), así una acta se
chequea una sola vez (no todos los días para siempre). Si algún día INPI
tarda en resolver la oposición y hay que revisar de nuevo más adelante, por
ahora se puede resetear esa columna a mano.

Uso:
    DATABASE_URL=... python3 revisar_oposiciones.py
    DATABASE_URL=... python3 revisar_oposiciones.py --dias 33 --delay 1.5

Alcance: solo es_lead = true (particulares/empresas sin agente ni apoderado).
Las que ya tienen agente quedan afuera a propósito — ese trámite lo maneja
su propio apoderado.
"""

import argparse
import os
import sys
import time
from datetime import date, timedelta

import psycopg2
import psycopg2.extras

from validar_leads import (
    buscar_archivos_grilla,
    crear_sesion,
    detectar_oposicion,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dias", type=int, default=33,
        help="revisar leads cuya fecha_publicacion tenga al menos esta antigüedad (default 33, "
             "cubre la ventana de oposición de 30 días con margen)",
    )
    ap.add_argument("--delay", type=float, default=1.5, help="segundos entre acta y acta (freno de mano)")
    ap.add_argument("--limit", type=int, default=None, help="tope de actas a revisar, útil para pruebas")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")

    corte = date.today() - timedelta(days=args.dias)

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT acta, titular, fecha_publicacion
                FROM marcas
                WHERE es_lead = true
                  AND fecha_publicacion IS NOT NULL
                  AND fecha_publicacion <= %s
                  AND revisado_oposicion_en IS NULL
                ORDER BY fecha_publicacion
                """,
                (corte,),
            )
            pendientes = cur.fetchall()

        if args.limit:
            pendientes = pendientes[: args.limit]

        print(f"Leads con publicación <= {corte.isoformat()} (>= {args.dias} días) sin revisar todavía: {len(pendientes)}")

        s = crear_sesion()
        con_oposicion = 0
        for i, fila in enumerate(pendientes, 1):
            acta = fila["acta"]
            archivos = buscar_archivos_grilla(s, acta)
            if not archivos:
                # bloqueo del WAF u otro fallo de red: no marcamos como revisado,
                # para que la próxima corrida lo vuelva a intentar.
                print(f"  [{i}/{len(pendientes)}] acta {acta}: no se pudo consultar Grilla Digital, reintento en la próxima corrida")
                time.sleep(args.delay)
                continue

            tuvo_oposicion, detalle = detectar_oposicion(
                archivos, fila["fecha_publicacion"].isoformat()
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE marcas
                    SET tuvo_oposicion = %s, detalle_oposicion = %s,
                        revisado_oposicion_en = now()
                    WHERE acta = %s
                    """,
                    (tuvo_oposicion, detalle or None, acta),
                )
            conn.commit()

            if tuvo_oposicion:
                con_oposicion += 1
                print(f"  [{i}/{len(pendientes)}] acta {acta} ({fila['titular']}): CON OPOSICIÓN/VISTA — {detalle}")
            else:
                print(f"  [{i}/{len(pendientes)}] acta {acta} ({fila['titular']}): sin oposición")

            time.sleep(args.delay)

        print(f"\nRevisadas: {len(pendientes)}. Con oposición/vista detectada: {con_oposicion}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
