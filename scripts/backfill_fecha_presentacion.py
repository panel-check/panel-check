"""
Corrige `fecha_presentacion` de las marcas PRE-BOLETÍN (boletin IS NULL) con la
fecha del "Recibo de Ingreso / Solicitud de marcas" de Grilla Digital, que es
la fecha real de presentación (la de DATOS GENERALES de la ficha puede diferir).

Uso:
    DATABASE_URL=... python3 backfill_fecha_presentacion.py [--limit N] [--delay 1.5] [--dry-run]
"""

import argparse
import os
import sys
import time

import psycopg2
import psycopg2.extras

from validar_leads import BASE, _get_con_reintentos, crear_sesion, datos_generales_de_pagina


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--dry-run", action="store_true", help="solo muestra los cambios, no guarda")
    args = ap.parse_args()

    database_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not database_url:
        print("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT acta, fecha_presentacion FROM marcas WHERE boletin IS NULL ORDER BY acta")
        pendientes = cur.fetchall()
    if args.limit:
        pendientes = pendientes[: args.limit]
    print(f"Marcas pre-boletín a revisar: {len(pendientes)}")

    s = crear_sesion()
    cambiadas = sin_dato = 0
    for fila in pendientes:
        acta = fila["acta"]
        nueva = None
        try:
            r = _get_con_reintentos(lambda: s.post(
                f"{BASE}/MarcasConsultas/Resultado",
                headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
                data={"acta": acta}, timeout=30,
            ))
            if "Web Page Blocked" not in r.text and "Attack ID" not in r.text:
                nueva = datos_generales_de_pagina(r.text)["fecha_presentacion"]
        except Exception as e:  # noqa: BLE001
            print(f"  {acta}: error {e}")
        if not nueva:
            sin_dato += 1
        elif str(fila["fecha_presentacion"]) != nueva:
            print(f"  {acta}: {fila['fecha_presentacion']} -> {nueva}")
            if not args.dry_run:
                with conn.cursor() as cur:
                    cur.execute("UPDATE marcas SET fecha_presentacion = %s WHERE acta = %s", (nueva, acta))
                conn.commit()
            cambiadas += 1
        time.sleep(args.delay)
    print(f"Corregidas: {cambiadas} · sin dato en la ficha: {sin_dato}")


if __name__ == "__main__":
    main()
