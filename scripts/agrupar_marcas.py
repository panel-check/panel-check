"""
Junta a mano varias marcas bajo un mismo nombre de grupo en Clientes → Marcas (por ejemplo
«LA BONNE VIE» + «LBV BONVIELA»). Es lo mismo que el link «Agrupar…» de cada acta en el panel.
Con --grupo vacío, las marcas vuelven a agruparse solas por nombre.

Uso:
    DATABASE_URL=... python3 agrupar_marcas.py --grupo "LA BONNE VIE" --actas "3237877,4524139" --simular
    DATABASE_URL=... python3 agrupar_marcas.py --grupo "LA BONNE VIE" --actas "3237877,4524139"
"""

import argparse
import os
import re
import sys
import traceback

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import cartera  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grupo", default="", help="nombre del grupo (vacío = sacarlas del grupo)")
    ap.add_argument("--actas", required=True, help="actas separadas por coma, espacio o salto de línea")
    ap.add_argument("--simular", action="store_true", help="solo cuenta; no escribe")
    args = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")
    grupo = re.sub(r"\s+", " ", args.grupo).strip()[:120] or None
    actas = []
    for crudo in re.split(r"[\s,;]+", args.actas):
        a = re.sub(r"\D", "", crudo)
        if a and a not in actas:
            actas.append(a)
    if not actas:
        sys.exit("No hay actas válidas")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cartera.crear_tablas(cur)
            cur.execute("SELECT acta FROM cartera_marcas WHERE acta = ANY(%s)", (actas,))
            estan = {f[0] for f in cur.fetchall()}
            faltan = [a for a in actas if a not in estan]
            if args.simular:
                conn.rollback()
                print(f"::notice::Simulación: {len(estan)} marca(s) pasarían al grupo '{grupo or '(sin grupo)'}'. "
                      f"No están en la cartera: {', '.join(faltan) or 'ninguna'}.")
                return
            cur.execute("UPDATE cartera_marcas SET grupo_marca = %s WHERE acta = ANY(%s)", (grupo, list(estan)))
        conn.commit()
        print(f"::notice::{len(estan)} marca(s) en el grupo '{grupo or '(sin grupo)'}'. "
              f"No están en la cartera: {', '.join(faltan) or 'ninguna'}.")
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"::error::agrupar_marcas falló: {type(e).__name__}: {e}".replace("\n", " "))
        traceback.print_exc()
        sys.exit(1)
