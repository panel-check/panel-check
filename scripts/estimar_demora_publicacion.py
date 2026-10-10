"""
Estima cuántos días pasan entre que se presenta una solicitud ("Recibo de Ingreso /
Solicitud de marcas" en la Grilla Digital) y que sale en un boletín ("Hoja Publicacion").

Toma una muestra al azar de marcas ya publicadas (con boletín), mira la Grilla Digital de
cada una y calcula la diferencia en días. Solo lee: no modifica nada en la base.

Uso:
    DATABASE_URL=... python3 estimar_demora_publicacion.py [--muestra 50] [--meses 6] [--delay 1.0]
"""

import argparse
import os
import statistics
import sys
import time

import psycopg2
import psycopg2.extras

from validar_leads import _parsear_fecha_grilla, buscar_archivos_grilla, crear_sesion


def _fecha_de(archivos, indice, referencia_contiene=None):
    """La fecha más antigua (ISO) de las filas con ese Indice (y, si se pide, cuya
    Referencia contenga ese texto)."""
    fechas = []
    for a in archivos:
        if (a.get("Indice") or "").strip().upper() != indice.upper():
            continue
        if referencia_contiene and referencia_contiene.upper() not in (a.get("Referencia") or "").upper():
            continue
        f = _parsear_fecha_grilla(a.get("Fecha") or "")
        if f:
            fechas.append(f)
    return min(fechas) if fechas else None


def dias_entre(archivos):
    """(fecha_ingreso, fecha_publicacion, días) o None si falta alguna de las dos."""
    ingreso = _fecha_de(archivos, "Recibo de Ingreso", "Solicitud de marca")
    publicacion = _fecha_de(archivos, "Hoja Publicacion")
    if not ingreso or not publicacion:
        return None
    import datetime as dt
    dias = (dt.date.fromisoformat(publicacion) - dt.date.fromisoformat(ingreso)).days
    return ingreso, publicacion, dias


def resumen(dias):
    """Líneas de texto con las estadísticas de la lista de días."""
    if not dias:
        return ["Sin datos suficientes."]
    d = sorted(dias)
    c = statistics.quantiles(d, n=4) if len(d) >= 4 else [d[0], statistics.median(d), d[-1]]
    return [
        f"Marcas medidas: {len(d)}",
        f"Promedio: {statistics.mean(d):.1f} días (≈ {statistics.mean(d) / 7:.1f} semanas)",
        f"Mediana: {statistics.median(d):.0f} días",
        f"Rango típico (25%–75% de los casos): {c[0]:.0f} a {c[2]:.0f} días",
        f"Mínimo / máximo: {d[0]} / {d[-1]} días",
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--muestra", type=int, default=50, help="cuántas marcas mirar")
    ap.add_argument("--meses", type=int, default=6, help="solo marcas presentadas en los últimos N meses (0 = todas)")
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()

    database_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not database_url:
        print("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT acta FROM marcas WHERE boletin IS NOT NULL "
            "AND (%s = 0 OR fecha_presentacion >= now() - make_interval(months => %s)) "
            "ORDER BY random() LIMIT %s",
            (args.meses, args.meses, args.muestra),
        )
        actas = [f["acta"] for f in cur.fetchall()]
    print(f"Marcas elegidas al azar: {len(actas)}")

    s = crear_sesion()
    dias, sin_dato = [], 0
    for n, acta in enumerate(actas, 1):
        try:
            archivos = buscar_archivos_grilla(s, acta)
        except Exception as e:  # noqa: BLE001
            print(f"  {acta}: error {e}")
            archivos = []
        r = dias_entre(archivos)
        if r:
            ingreso, publicacion, d = r
            dias.append(d)
            print(f"  [{n}/{len(actas)}] acta {acta}: ingreso {ingreso} -> publicación {publicacion} = {d} días")
        else:
            sin_dato += 1
            print(f"  [{n}/{len(actas)}] acta {acta}: sin fecha de ingreso o de publicación en la Grilla")
        time.sleep(args.delay)

    lineas = resumen(dias)
    if sin_dato:
        lineas.append(f"Sin datos en la Grilla: {sin_dato}")
    print("\n=== Demora entre Recibo de Ingreso y Hoja Publicación ===")
    print("\n".join(lineas))
    resumen_gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen_gh:
        with open(resumen_gh, "a", encoding="utf-8") as f:
            f.write("## Demora entre Recibo de Ingreso y Hoja Publicación\n\n")
            f.write("\n".join(f"- {l}" for l in lineas) + "\n")


if __name__ == "__main__":
    main()
