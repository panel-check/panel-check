"""
Corrige `marcas.clase` de las marcas CON OPOSICIÓN (tuvo_oposicion = true).

Por qué: hasta el 12/10/2026 la clase se leía con un regex que, en las actas con oposición,
podía tomar la clase de otra marca citada en el texto de la oposición (acta 4782038: el
panel mostraba 43 y el acta es clase 30). Esto relee cada acta en INPI con
validar_leads.clase_de_pagina (solo el campo CLASE de TITULARIDAD) y, si difiere de la
guardada, la corrige. Es idempotente: se puede correr de nuevo sin problema.

Los logs de Actions son públicos: solo imprime acta y clase (nunca nombres ni datos de personas).

Uso:
    DATABASE_URL=... python3 backfill_clase_oposiciones.py [--dry-run] [--limit N]
        [--delay 1.0] [--max-minutes M]
"""

import argparse
import os
import sys
import time

import psycopg2
import psycopg2.extras

import monitor_bloqueo
from validar_leads import BASE, _get_con_reintentos, clase_de_pagina, crear_sesion


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="solo muestra los cambios, no guarda")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--max-minutes", type=float, default=None)
    args = ap.parse_args()

    database_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not database_url:
        print("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT acta, clase FROM marcas WHERE tuvo_oposicion IS TRUE ORDER BY acta::bigint")
        pendientes = cur.fetchall()
    if args.limit:
        pendientes = pendientes[: args.limit]
    print(f"Marcas con oposición a revisar: {len(pendientes)}" + (" (dry-run: no se guarda nada)" if args.dry_run else ""))

    s = crear_sesion()
    inicio = time.time()
    ok = cambiadas = sin_dato = sin_leer = 0
    cortado = None
    for n, fila in enumerate(pendientes, 1):
        acta = fila["acta"]
        if args.max_minutes and (time.time() - inicio) > args.max_minutes * 60:
            cortado = f"tope de {args.max_minutes} min"
            break
        if monitor_bloqueo.debe_cortar():
            cortado = "INPI bloqueó consultas seguidas"
            break
        if n % 25 == 0:
            print(f"  [{n}/{len(pendientes)}]", flush=True)
        try:
            r = _get_con_reintentos(lambda: s.post(
                f"{BASE}/MarcasConsultas/Resultado",
                headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
                data={"acta": acta}, timeout=30,
            ))
        except Exception as e:  # noqa: BLE001
            print(f"  {acta}: error {type(e).__name__}")
            sin_leer += 1
            time.sleep(args.delay)
            continue
        if "Web Page Blocked" in r.text or "Attack ID" in r.text:
            sin_leer += 1
            time.sleep(args.delay)
            continue
        nueva = clase_de_pagina(r.text)
        if nueva is None:
            sin_dato += 1  # la ficha no trae el campo: no se toca lo que hay
        elif fila["clase"] != nueva:
            print(f"  acta {acta}: clase {fila['clase']} -> {nueva}")
            if not args.dry_run:
                with conn.cursor() as cur:
                    cur.execute("UPDATE marcas SET clase = %s WHERE acta = %s", (nueva, acta))
                conn.commit()
            cambiadas += 1
        else:
            ok += 1
        time.sleep(args.delay)

    print(f"Ya estaban bien: {ok} · {'cambiarían' if args.dry_run else 'corregidas'}: {cambiadas} · "
          f"sin clase en la ficha: {sin_dato} · sin poder leer: {sin_leer}")
    if cortado:
        print(f"Se cortó antes de terminar ({cortado}): volver a correr; lo ya corregido queda guardado.")
        sys.exit(1 if "bloque" in cortado else 0)


if __name__ == "__main__":
    main()
