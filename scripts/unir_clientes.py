"""
Une dos clientes que en realidad son el mismo: pasa todo lo del cliente ORIGEN al
cliente DESTINO y borra el origen (que queda vacío). Es lo mismo que hacer, desde
el panel, «mover» cada marca y después borrar el cliente sobrante, pero de una vez
y sin dejar nada colgado.

Qué pasa al destino: las marcas de la cartera, sus novedades y alertas de vigilancia,
los formularios recibidos y los poderes generados. Si al destino le falta CUIT, email,
teléfono o contacto y el origen los tiene, se completan; las notas del origen se agregan
a las del destino. El tag (referido) y la vigilancia del destino no se tocan.

Seguridad:
  * --acta-control: si se indica, corta si el origen no tiene esa acta (evita unir los
    clientes equivocados por un número mal puesto).
  * Si los dos tienen CUIT y es distinto, corta (serían personas distintas); solo sigue con --forzar.
  * --simular cuenta lo que se movería sin escribir nada.
  * Todo en una sola transacción: si algo falla, no queda nada a medias.

El repo es público: el log solo muestra ids y cantidades, nunca nombres ni CUIT.

Uso:
    DATABASE_URL=... python3 unir_clientes.py --origen 25 --destino 24 --acta-control 3237877 --simular
    DATABASE_URL=... python3 unir_clientes.py --origen 25 --destino 24 --acta-control 3237877
"""

import argparse
import os
import sys
import traceback

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import cartera  # noqa: E402

# (tabla, columna que apunta al cliente)
DEPENDIENTES = (
    ("cartera_marcas", "cliente_id"),
    ("cartera_novedades", "cliente_id"),
    ("vigilancia_alertas", "cliente_id"),
    ("formularios_respuestas", "cliente_id"),
    ("poderes_generados", "cliente_id"),
)
COMPLETAR = ("cuit", "email", "telefono", "contacto")


def existe_tabla(cur, tabla: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (tabla,))
    return bool(cur.fetchone()["ok"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origen", type=int, required=True, help="id del cliente que se borra (sus cosas pasan al destino)")
    ap.add_argument("--destino", type=int, required=True, help="id del cliente que se queda con todo")
    ap.add_argument("--acta-control", default="", help="acta que el origen debe tener (control de seguridad)")
    ap.add_argument("--simular", action="store_true", help="solo cuenta lo que se movería; no escribe")
    ap.add_argument("--forzar", action="store_true", help="sigue aunque los dos clientes tengan CUIT distinto")
    args = ap.parse_args()

    if args.origen == args.destino:
        sys.exit("El origen y el destino son el mismo cliente")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cartera.crear_tablas(cur)
            cur.execute("SELECT * FROM clientes WHERE id = ANY(%s)", ([args.origen, args.destino],))
            filas = {f["id"]: f for f in cur.fetchall()}
            for cid in (args.origen, args.destino):
                if cid not in filas:
                    sys.exit(f"No existe el cliente #{cid}")
            org, dst = filas[args.origen], filas[args.destino]

            if args.acta_control:
                cur.execute("SELECT 1 FROM cartera_marcas WHERE cliente_id = %s AND acta = %s",
                            (args.origen, args.acta_control.strip()))
                if not cur.fetchone():
                    sys.exit(f"El cliente #{args.origen} no tiene el acta {args.acta_control}: no se une nada.")
            if org.get("cuit") and dst.get("cuit") and org["cuit"] != dst["cuit"] and not args.forzar:
                print(f"::error::Los clientes #{args.origen} y #{args.destino} tienen CUIT distinto: no se unen (usar forzar si de verdad son la misma persona).")
                sys.exit(1)

            cuentas = {}
            for tabla, col in DEPENDIENTES:
                if existe_tabla(cur, tabla):
                    cur.execute(f"SELECT COUNT(*) AS n FROM {tabla} WHERE {col} = %s", (args.origen,))
                    cuentas[tabla] = cur.fetchone()["n"]
            detalle = ", ".join(f"{t}: {n}" for t, n in cuentas.items())
            completa = [c for c in COMPLETAR if not dst.get(c) and org.get(c)]
            print(f"Cliente #{args.origen} → #{args.destino}. A mover: {detalle}. Datos que se completan en el destino: "
                  f"{', '.join(completa) or 'ninguno'}. Notas del origen: {'sí' if (org.get('notas') or '').strip() else 'no'}.")

            if args.simular:
                conn.rollback()
                print(f"::notice::Simulación (no se escribió nada): del cliente #{args.origen} al #{args.destino} se moverían {detalle}.")
                return

            for tabla, col in DEPENDIENTES:
                if tabla in cuentas:
                    cur.execute(f"UPDATE {tabla} SET {col} = %s WHERE {col} = %s", (args.destino, args.origen))
            for c in completa:
                cur.execute(f"UPDATE clientes SET {c} = %s WHERE id = %s", (org[c], args.destino))
            if (org.get("notas") or "").strip():
                nuevas = ((dst.get("notas") or "").strip() + "\n\n" if (dst.get("notas") or "").strip() else "") + org["notas"].strip()
                cur.execute("UPDATE clientes SET notas = %s WHERE id = %s", (nuevas, args.destino))
            cur.execute("UPDATE clientes SET modificado_en = now(), modificado_por = %s WHERE id = %s", ("unión de clientes", args.destino))
            cur.execute("DELETE FROM clientes WHERE id = %s", (args.origen,))
        conn.commit()
        resumen = f"Clientes unidos: el #{args.origen} se borró y todo pasó al #{args.destino} ({detalle})."
        print(f"::notice::{resumen}")
        print(resumen)
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
        linea = f"{type(e).__name__}: {e}".replace("\n", " ")
        print(f"::error::unir_clientes falló: {linea}")
        traceback.print_exc()
        sys.exit(1)
