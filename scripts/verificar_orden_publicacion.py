"""
Verifica (solo lectura) si, cuando una marca figura en la Grilla Digital con «Orden de
Publicación», sale publicada en el boletín del miércoles siguiente.

Toma unas pocas actas de un boletín ya publicado, mira la Grilla de cada una y compara la fecha
de la fila «Orden de Publicación» con la de la fila «Hoja Publicacion» (la del boletín):
  cumple     la Hoja Publicacion cae el primer miércoles posterior a la Orden de Publicación
  mas_tarde  salió publicada más de una semana después
  antes      salió publicada antes de ese miércoles (p. ej. la Orden es de un miércoles)
  sin_orden  la Grilla no tiene fila de Orden de Publicación anterior a la publicación
  sin_hoja   la Grilla no tiene «Hoja Publicacion»
Sirve para saber si «Orden de Publicación hoy = publicada el miércoles que viene» se cumple siempre,
y por lo tanto si se puede dejar de revisar las marcas con orden (ver probar_vistas_preboletin.py).

Si el boletín pedido no está en la base, lo avisa, lista los más recientes y usa el último importado.

Uso:
    DATABASE_URL=... python3 verificar_orden_publicacion.py [--boletin 11127] [--cantidad 10] [--delay 1.5]
"""

import argparse
import datetime as dt
import os
import sys
import time

import psycopg2
import psycopg2.extras

import monitor_bloqueo
from probar_vistas_preboletin import _fechas
from validar_leads import buscar_archivos_grilla, crear_sesion

DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _d(iso):
    return dt.date.fromisoformat(iso) if iso else None


def _dia(d):
    return f"{d.isoformat()} ({DIAS[d.weekday()]})" if d else "—"


def miercoles_siguiente(d: dt.date) -> dt.date:
    """Primer miércoles estrictamente posterior a d."""
    return d + dt.timedelta(days=((2 - d.weekday()) % 7) or 7)


def comparar(ordenes, hojas, fecha_boletin=None, ingresos=None):
    """(categoria, orden, hoja, siguiente_miercoles). Empareja la Hoja Publicacion más cercana a la
    fecha del boletín con la última Orden de Publicación anterior o igual a ella."""
    if not hojas:
        return "sin_hoja", None, None, None
    hoja = min(hojas, key=lambda h: abs((h - fecha_boletin).days)) if fecha_boletin else max(hojas)
    previas = [o for o in ordenes if o <= hoja]
    if not previas:
        return "sin_orden", None, hoja, None
    orden = max(previas)
    sig = miercoles_siguiente(orden)
    if hoja == sig:
        return "cumple", orden, hoja, sig
    return ("mas_tarde" if hoja > sig else "antes"), orden, hoja, sig


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boletin", default="", help="número de boletín del que se toman las actas (vacío = el último importado)")
    ap.add_argument("--cantidad", type=int, default=10, help="cuántas actas mirar")
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    conn = psycopg2.connect(dsn)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT numero, fecha, total_marcas FROM boletines WHERE estado = 'procesado' ORDER BY fecha DESC NULLS LAST LIMIT 12")
        recientes = cur.fetchall()
        cur.execute("SELECT numero, fecha FROM boletines WHERE numero = %s", (args.boletin,)) if args.boletin else None
        elegido = cur.fetchone() if args.boletin else None
        if not elegido:
            if args.boletin:
                print(f"AVISO: el boletín {args.boletin!r} no está en la base.", flush=True)
            print("Boletines más recientes importados: " + ", ".join(f"{b['numero']} ({b['fecha']})" for b in recientes), flush=True)
            if not recientes:
                sys.exit("No hay boletines importados")
            elegido = recientes[0]
            print(f"Se usa el último importado: {elegido['numero']} ({elegido['fecha']}).", flush=True)
        cur.execute("SELECT acta FROM marcas WHERE boletin = %s ORDER BY md5(acta) LIMIT %s", (elegido["numero"], args.cantidad))
        actas = [r["acta"] for r in cur.fetchall()]
    conn.close()
    fecha_boletin = elegido["fecha"]
    print(f"Boletín {elegido['numero']} ({_dia(fecha_boletin)}): {len(actas)} actas elegidas.\n", flush=True)

    s = crear_sesion()
    filas, conteo = [], {}
    for n, acta in enumerate(actas, 1):
        if monitor_bloqueo.debe_cortar():
            print("INPI bloqueó consultas seguidas: se corta.", flush=True)
            break
        try:
            archivos = buscar_archivos_grilla(s, acta)
        except Exception as e:  # noqa: BLE001
            print(f"  acta {acta}: error {e}", flush=True)
            archivos = []
        if not archivos:
            print(f"  [{n}/{len(actas)}] acta {acta}: Grilla vacía (bloqueo o sin filas)", flush=True)
            time.sleep(args.delay)
            continue
        ordenes = [_d(x) for x in _fechas(archivos, "ORDEN DE PUBLICACION")]
        hojas = [_d(x) for x in _fechas(archivos, "HOJA PUBLICACION")]
        ingreso = next(iter(_fechas([a for a in archivos if "SOLICITUD DE MARCA" in (a.get("Referencia") or "").upper()], "RECIBO DE INGRESO")), None)
        cat, orden, hoja, sig = comparar(ordenes, hojas, fecha_boletin)
        conteo[cat] = conteo.get(cat, 0) + 1
        lag = (hoja - orden).days if orden and hoja else None
        filas.append((acta, _d(ingreso), orden, hoja, sig, cat, lag))
        print(f"  [{n}/{len(actas)}] acta {acta}: ingreso {_dia(_d(ingreso))} · orden {_dia(orden)} · publicada {_dia(hoja)} · "
              f"miércoles siguiente a la orden {_dia(sig)} → {cat}{f' ({lag} días)' if lag is not None else ''}", flush=True)
        time.sleep(args.delay)

    con_ambas = [f for f in filas if f[2] and f[3]]
    cumplen = conteo.get("cumple", 0)
    lineas = [f"Boletín {elegido['numero']} ({elegido['fecha']}): {len(filas)} actas miradas, {len(con_ambas)} con Orden de Publicación y Hoja Publicacion"]
    lineas.append(f"Cumplen «sale el miércoles siguiente a la orden»: {cumplen} de {len(con_ambas)}")
    for k in ("mas_tarde", "antes", "sin_orden", "sin_hoja"):
        if conteo.get(k):
            lineas.append(f"{k}: {conteo[k]}")
    if con_ambas:
        lags = sorted(f[6] for f in con_ambas)
        lineas.append(f"Días entre la orden y la publicación: de {lags[0]} a {lags[-1]} (todas: {', '.join(str(x) for x in lags)})")
        lineas.append("Día de la semana de la orden: " + ", ".join(sorted({DIAS[f[2].weekday()] for f in con_ambas})))
    print("\n=== Resultado ===\n" + "\n".join(lineas))
    resumen_gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen_gh:
        with open(resumen_gh, "a", encoding="utf-8") as f:
            f.write("## Orden de Publicación → publicada el miércoles siguiente\n\n" + "\n".join(f"- {l}" for l in lineas) + "\n\n")
            f.write("| Acta | Ingreso | Orden de Publicación | Publicada | Miércoles siguiente a la orden | Resultado |\n|---|---|---|---|---|---|\n")
            for acta, ing, orden, hoja, sig, cat, lag in filas:
                f.write(f"| {acta} | {_dia(ing)} | {_dia(orden)} | {_dia(hoja)} | {_dia(sig)} | {cat} |\n")


if __name__ == "__main__":
    main()
