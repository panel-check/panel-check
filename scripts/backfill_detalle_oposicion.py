"""
Backfill puntual (corre una vez, no forma parte del cron): para las actas
que YA tienen tuvo_oposicion = true pero todavía no tienen el detalle rico
(oponente_nombre/fundamento_oposicion — columnas agregadas el 29/09/2026,
ver descargar_formulario_oposicion en validar_leads.py), vuelve a consultar
Grilla Digital y baja+parsea el Formulario real de la oposición.

Uso:
    DATABASE_URL=... python3 backfill_detalle_oposicion.py
    DATABASE_URL=... python3 backfill_detalle_oposicion.py --limit 5
"""

import argparse
import os
import sys
import time

import psycopg2
import psycopg2.extras

from validar_leads import (
    buscar_archivos_grilla,
    buscar_fila_oposicion,
    crear_sesion,
    descargar_formulario_oposicion,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT acta, titular, fecha_publicacion, detalle_oposicion
                FROM marcas
                WHERE tuvo_oposicion = true
                  AND oponente_nombre IS NULL
                  AND fundamento_oposicion IS NULL
                ORDER BY fecha_publicacion
                """
            )
            pendientes = cur.fetchall()

        if args.limit:
            pendientes = pendientes[: args.limit]

        print(f"::notice::Oposiciones ya detectadas sin detalle rico todavía: {len(pendientes)}")

        s = crear_sesion()
        completadas = 0
        for i, fila in enumerate(pendientes, 1):
            acta = fila["acta"]
            archivos = buscar_archivos_grilla(s, acta)
            if not archivos:
                print(f"::notice::  [{i}/{len(pendientes)}] acta {acta}: bloqueado por el WAF, reintento en la próxima corrida")
                conn.commit()  # commit "vacío" — ver revisar_estado.py
                time.sleep(args.delay)
                continue

            fecha_pub = fila["fecha_publicacion"].isoformat() if fila["fecha_publicacion"] else None
            fila_opo = buscar_fila_oposicion(archivos, fecha_pub)
            if not fila_opo or "OPO" not in (fila_opo.get("Referencia") or "").upper():
                # o ya no se encuentra (raro), o es una VISTA de oficio de
                # INPI (no tiene Formulario de tercero) — no hay nada para
                # completar, seguimos.
                print(f"::notice::  [{i}/{len(pendientes)}] acta {acta}: no es oposición de tercero con Formulario propio, se saltea")
                conn.commit()
                time.sleep(args.delay)
                continue

            detalle_rico = descargar_formulario_oposicion(s, archivos, fila_opo, acta_propia=acta)
            if detalle_rico:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE marcas
                        SET oponente_nombre = %s, oponente_tipo_doc = %s,
                            oponente_numero_doc = %s, oponente_cuit = %s,
                            fundamento_oposicion = %s,
                            actas_marca_oponente = %s, marca_oponente_denominacion = %s,
                            marca_oponente_numero_registro = %s
                        WHERE acta = %s
                        """,
                        (
                            detalle_rico.get("oponente_nombre"),
                            detalle_rico.get("oponente_tipo_doc"),
                            detalle_rico.get("oponente_numero_doc"),
                            detalle_rico.get("oponente_cuit"),
                            detalle_rico.get("fundamento_oposicion"),
                            detalle_rico.get("actas_marca_oponente"),
                            detalle_rico.get("marca_oponente_denominacion"),
                            detalle_rico.get("marca_oponente_numero_registro"),
                            acta,
                        ),
                    )
                conn.commit()
                completadas += 1
                # No se imprime el titular ni el oponente: datos personales
                # de terceros que no deben quedar en los logs (repo
                # público). Se siguen guardando en la base (UPDATE de
                # arriba) sin cambios.
                print(f"::notice::  [{i}/{len(pendientes)}] acta {acta}: completado")
            else:
                conn.commit()
                print(f"::notice::  [{i}/{len(pendientes)}] acta {acta}: no se pudo bajar/parsear el Formulario")

            time.sleep(args.delay)

        print(f"::notice::Revisadas: {len(pendientes)}. Completadas con detalle rico: {completadas}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
