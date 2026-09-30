"""
Corrige el titular de las marcas cargadas por escanear_actas_nuevas.py
(fuente='escaneo_directo') antes del fix del 30/09/2026.

El bug: la regex del Formulario (TITULARIDAD.*?NOMBRE:(.+) con re.S) se
comía todo el resto del PDF, así que el titular quedaba como
"SERRATTI GERONIMO DOMICILIO LEGAL: ... 4124382 3 de 3", o directamente
como el CUIT ("27288993780 DOMICILIO LEGAL: ...") cuando NOMBRE venía vacío.

Para cada fila afectada:
  1. Vuelve a pedir /MarcasConsultas/Resultado y toma el NOMBRE del titular
     de ahí (dato estructurado, ver titular_de_pagina).
  2. Si INPI no responde / WAF, limpia el texto guardado cortando en el
     primer label (limpiar_titular).
  3. Si ninguna de las dos da un nombre, deja "(titular a confirmar manualmente)".

Solo toca filas con boletin IS NULL (si el boletín ya la alcanzó,
cargar_db.py ya puso el titular correcto del boletín).

Uso:
    DATABASE_URL=... python3 corregir_titular_escaneo.py            # aplica
    DATABASE_URL=... python3 corregir_titular_escaneo.py --dry-run  # solo muestra
"""

import argparse
import os
import sys
import time

import psycopg2

from validar_leads import BASE, _get_con_reintentos, crear_sesion, limpiar_titular, titular_de_pagina


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT acta, titular FROM marcas
            WHERE fuente = 'escaneo_directo' AND boletin IS NULL
            ORDER BY acta
            """
        )
        filas = cur.fetchall()
    print(f"{len(filas)} filas de escaneo directo sin boletín todavía")

    s = crear_sesion()
    corregidas = desde_inpi = desde_limpieza = a_confirmar = sin_cambio = 0
    for acta, titular_actual in filas:
        nuevo = None
        try:
            r = _get_con_reintentos(
                lambda: s.post(
                    f"{BASE}/MarcasConsultas/Resultado",
                    headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
                    data={"acta": acta},
                    timeout=30,
                )
            )
            if "Web Page Blocked" not in r.text and "Attack ID" not in r.text:
                nuevo = titular_de_pagina(r.text)
        except Exception as e:
            print(f"  acta {acta}: error consultando INPI ({e})")

        if nuevo:
            desde_inpi += 1
        else:
            nuevo = limpiar_titular(titular_actual)
            if nuevo:
                desde_limpieza += 1
            else:
                nuevo = "(titular a confirmar manualmente)"
                a_confirmar += 1

        if nuevo == titular_actual:
            sin_cambio += 1
        else:
            corregidas += 1
            print(f"  acta {acta}: {(titular_actual or '')[:60]!r} -> {nuevo!r}")
            if not args.dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE marcas SET titular = %s, actualizado_en = now() WHERE acta = %s",
                        (nuevo, acta),
                    )
                conn.commit()
        time.sleep(args.delay)

    conn.close()
    resumen = (
        f"{'[DRY RUN] ' if args.dry_run else ''}Corregidas: {corregidas} "
        f"(desde INPI: {desde_inpi}, por limpieza: {desde_limpieza}, a confirmar: {a_confirmar}). "
        f"Sin cambio: {sin_cambio}."
    )
    print(f"::notice::{resumen}")


if __name__ == "__main__":
    main()
