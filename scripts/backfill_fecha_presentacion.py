"""
Corrige `fecha_presentacion` de las marcas PRE-BOLETÍN (boletin IS NULL) con el
campo PRESENTACIÓN de la ficha del acta en INPI (DATOS GENERALES).

Recuerda por sí solo hasta qué acta llegó (tabla `progreso_tareas`): cada corrida
sigue desde la última marca revisada, sin que haya que indicar nada. Para empezar
de cero, usar --desde-acta 0.

Uso:
    DATABASE_URL=... python3 backfill_fecha_presentacion.py [--limit N] [--delay 1.0]
        [--max-minutes M] [--desde-acta N] [--dry-run]
"""

import argparse
import os
import sys
import time

import psycopg2
import psycopg2.extras

from validar_leads import BASE, _get_con_reintentos, crear_sesion, datos_generales_de_pagina

CLAVE = "backfill_fecha_presentacion"
# Punto de partida de la primera vez: la corrida #4 (10/10/2026) ya revisó todas las
# marcas hasta el acta anterior a esta. Solo se usa si todavía no hay progreso guardado.
ACTA_INICIAL = 4799678


def _progreso(conn):
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS progreso_tareas "
            "(clave TEXT PRIMARY KEY, valor TEXT NOT NULL, actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur.execute(
            "INSERT INTO progreso_tareas (clave, valor) VALUES (%s, %s) ON CONFLICT (clave) DO NOTHING",
            (CLAVE, str(ACTA_INICIAL - 1)),
        )
        cur.execute("SELECT valor FROM progreso_tareas WHERE clave = %s", (CLAVE,))
        valor = int(cur.fetchone()[0])
    conn.commit()
    return valor


def _guardar_progreso(conn, acta):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE progreso_tareas SET valor = %s, actualizado_en = now() WHERE clave = %s",
            (str(acta), CLAVE),
        )
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--max-minutes", type=float, default=None, help="corta solo pasados estos minutos (lo ya corregido queda guardado)")
    ap.add_argument("--desde-acta", default=None, help="empezar desde este acta (inclusive) en vez de seguir el progreso guardado; 0 = desde el principio")
    ap.add_argument("--dry-run", action="store_true", help="solo muestra los cambios, no guarda (ni el progreso)")
    args = ap.parse_args()

    database_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not database_url:
        print("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    guardado = _progreso(conn)
    desde = int(args.desde_acta) if args.desde_acta not in (None, "") else guardado + 1
    print(f"Se sigue desde el acta {desde}" + ("" if args.desde_acta not in (None, "") else " (progreso guardado)"))

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT acta, fecha_presentacion FROM marcas WHERE boletin IS NULL "
            "AND acta::bigint >= %s ORDER BY acta::bigint",
            (desde,),
        )
        pendientes = cur.fetchall()
    if args.limit:
        pendientes = pendientes[: args.limit]
    print(f"Marcas pre-boletín a revisar: {len(pendientes)}")

    s = crear_sesion()
    cambiadas = sin_dato = 0
    inicio = time.time()
    # Hasta qué acta se puede dar por revisado: se congela en la primera marca que no se
    # pudo leer (bloqueo/error de INPI), para que la próxima corrida la vuelva a intentar.
    ultimo_ok = None
    congelado = False
    cortado = False
    for n, fila in enumerate(pendientes, 1):
        acta = fila["acta"]
        if args.max_minutes and (time.time() - inicio) > args.max_minutes * 60:
            print(f"Tope de {args.max_minutes} min alcanzado: se corta acá (lo ya corregido quedó guardado)")
            cortado = True
            break
        if n % 25 == 0:
            print(f"  [{n}/{len(pendientes)}] {(time.time() - inicio) / n:.1f} s por marca")
        nueva = None
        leida = False
        try:
            r = _get_con_reintentos(lambda: s.post(
                f"{BASE}/MarcasConsultas/Resultado",
                headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
                data={"acta": acta}, timeout=30,
            ))
            if "Web Page Blocked" not in r.text and "Attack ID" not in r.text:
                leida = True
                nueva = datos_generales_de_pagina(r.text)["fecha_presentacion"]
        except Exception as e:  # noqa: BLE001
            print(f"  {acta}: error {e}")
        if not nueva:
            sin_dato += 1
        elif str(fila["fecha_presentacion"])[:10] != nueva:
            print(f"  {acta}: {str(fila['fecha_presentacion'])[:10]} -> {nueva}")
            if not args.dry_run:
                with conn.cursor() as cur:
                    cur.execute("UPDATE marcas SET fecha_presentacion = %s WHERE acta = %s", (nueva, acta))
                conn.commit()
            cambiadas += 1
        if leida and not congelado:
            ultimo_ok = int(acta)
            if not args.dry_run and n % 10 == 0:
                _guardar_progreso(conn, ultimo_ok)
        elif not leida:
            congelado = True
        time.sleep(args.delay)

    if not args.dry_run and ultimo_ok is not None:
        _guardar_progreso(conn, ultimo_ok)
    print(f"Corregidas: {cambiadas} · sin dato en la ficha: {sin_dato}")
    if cortado and ultimo_ok is not None:
        print(f"QUEDAN_MARCAS: la próxima tanda sigue sola después del acta {ultimo_ok}")
    elif cortado:
        print("No se pudo leer ninguna marca (¿INPI bloqueando?): no se relanza sola, probar más tarde.")
    else:
        print("Terminó: no quedan marcas por revisar (para empezar de nuevo desde el principio, desde_acta = 0).")


if __name__ == "__main__":
    main()
