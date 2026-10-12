"""
Une dos clientes que en realidad son el mismo: pasa todo lo del cliente ORIGEN al
cliente DESTINO y borra el origen (que queda vacío). Es lo mismo que el botón
«Unir otro cliente a este» de la ficha del cliente en el panel (la lógica está en
panel/cartera.py: unir_clientes), pero a mano desde GitHub Actions.

Qué pasa al destino: las marcas de la cartera, sus novedades y alertas de vigilancia,
los formularios recibidos y los poderes generados. Si al destino le falta CUIT, email,
teléfono o contacto y el origen los tiene, se completan; las notas del origen se agregan
a las del destino. El tag (referido) y la vigilancia del destino no se tocan.

Seguridad:
  * --acta-control: si se indica, corta si el origen no tiene esa acta (evita unir los
    clientes equivocados por un número mal puesto).
  * Si los dos tienen CUIT y es distinto, corta; solo sigue con --forzar.
  * --simular cuenta lo que se movería sin escribir nada.
  * Todo en una sola transacción: si algo falla, no queda nada a medias.

--buscar TEXTO: no une nada; lista los clientes cuyo nombre contiene el texto (sin tildes ni
mayúsculas) como «#id: N marcas, tag, CUIT sí/no, actas…» para saber qué ids usar, y también las
marcas cuya denominación lo contiene como «acta (cliente #id)».

El repo es público: el log solo muestra ids, tags, cantidades y actas, nunca nombres ni CUIT.

Uso:
    DATABASE_URL=... python3 unir_clientes.py --buscar innocenti
    DATABASE_URL=... python3 unir_clientes.py --origen 25 --destino 24 --acta-control 3237877 --simular
    DATABASE_URL=... python3 unir_clientes.py --origen 25 --destino 24 --acta-control 3237877 --forzar
"""

import argparse
import os
import sys
import traceback

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import cartera  # noqa: E402


def buscar(cur, texto: str):
    objetivo = cartera.normalizar_nombre(texto)
    if not objetivo:
        sys.exit("El texto a buscar está vacío")
    cur.execute(
        """
        SELECT c.id, c.nombre, c.cuit, c.referido, c.activo,
               (SELECT COUNT(*) FROM cartera_marcas m WHERE m.cliente_id = c.id) AS marcas,
               ARRAY(SELECT m.acta FROM cartera_marcas m WHERE m.cliente_id = c.id ORDER BY m.acta LIMIT 4) AS actas
        FROM clientes c ORDER BY c.id
        """
    )
    coincidencias = [f for f in cur.fetchall() if objetivo in cartera.normalizar_nombre(f["nombre"])]
    cur.execute("SELECT acta, cliente_id, denominacion FROM cartera_marcas ORDER BY cliente_id, acta")
    marcas = [f for f in cur.fetchall() if objetivo in cartera.normalizar_nombre(f["denominacion"])]
    if marcas:
        print("::notice::Búsqueda en marcas: " + " | ".join(f"{m['acta']} (cliente #{m['cliente_id']})" for m in marcas))
    if not coincidencias:
        print("::notice::Búsqueda: ningún cliente coincide por nombre.")
        return
    lineas = [f"#{f['id']}: {f['marcas']} marca(s), tag {f['referido'] or '—'}, CUIT {'sí' if f['cuit'] else 'no'}"
              f"{'' if f['activo'] else ', inactivo'}, actas {', '.join(f['actas']) or '—'}" for f in coincidencias]
    print(f"::notice::Búsqueda: {len(coincidencias)} cliente(s). " + " | ".join(lineas))
    for l in lineas:
        print(l)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--buscar", default="", help="lista los clientes cuyo nombre contiene este texto (no une nada)")
    ap.add_argument("--origen", type=int, help="id del cliente que se borra (sus cosas pasan al destino)")
    ap.add_argument("--destino", type=int, help="id del cliente que se queda con todo")
    ap.add_argument("--acta-control", default="", help="acta que el origen debe tener (control de seguridad)")
    ap.add_argument("--simular", action="store_true", help="solo cuenta lo que se movería; no escribe")
    ap.add_argument("--forzar", action="store_true", help="sigue aunque los dos clientes tengan CUIT distinto")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cartera.crear_tablas(cur)
            if args.buscar.strip():
                buscar(cur, args.buscar)
                conn.commit()
                return
            if not args.origen or not args.destino:
                sys.exit("Falta --origen y --destino (o usar --buscar)")
            if args.acta_control:
                cur.execute("SELECT 1 FROM cartera_marcas WHERE cliente_id = %s AND acta = %s",
                            (args.origen, args.acta_control.strip()))
                if not cur.fetchone():
                    sys.exit(f"El cliente #{args.origen} no tiene el acta {args.acta_control}: no se une nada.")
            try:
                res = cartera.unir_clientes(cur, args.origen, args.destino, forzar=args.forzar, simular=args.simular)
            except cartera.UnionError as e:
                print(f"::error::{e}")
                sys.exit(1)
            detalle = ", ".join(f"{t}: {n}" for t, n in res["movidas"].items())
            if args.simular:
                conn.rollback()
                print(f"::notice::Simulación (no se escribió nada): del cliente #{args.origen} al #{args.destino} se moverían {detalle}. "
                      f"Datos que se completan: {', '.join(res['completados']) or 'ninguno'}.")
                return
        conn.commit()
        print(f"::notice::Clientes unidos: el #{args.origen} se borró y todo pasó al #{args.destino} ({detalle}).")
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
