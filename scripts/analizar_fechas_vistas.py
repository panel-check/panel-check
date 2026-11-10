"""
Análisis de cuándo aparecen las vistas en el INPI (diagnóstico, solo lectura).

Toma de la base todas las marcas que alguna vez tuvieron una vista (contactadas o no, publicadas o no), vuelve a
pedir su Grilla Digital al INPI y, para cada fila cuyo Índice dice «VISTA», anota la fecha que muestra el INPI.
Compara esa fecha con el día en que el panel detectó la vista (vista_detectada_en) y con la notificación
(vista_fecha_notificacion), y resume:
  - en qué día de la semana tienen fecha las vistas en la Grilla;
  - cuántos días pasaron entre esa fecha y la detección del panel, y entre esa fecha y la notificación;
  - qué textos distintos aparecen en el Índice de esas Grillas (para ver si el INPI usa otros nombres).

No escribe nada en la base. El log de Actions es público: solo imprime actas, fechas y textos del Índice
(nunca titulares, denominaciones ni referencias).

    DATABASE_URL=... python3 analizar_fechas_vistas.py [--limite 600] [--delay 1.0]
"""

import argparse
import collections
import datetime as dt
import os
import re
import statistics
import sys
import time
import unicodedata
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

from validar_leads import _parsear_fecha_grilla, buscar_archivos_grilla, crear_sesion

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
RE_DOTNET = re.compile(r"Date\((-?\d+)")


def norm(texto: str) -> str:
    s = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").upper().strip()


def hora_dotnet(valor: str) -> str:
    """Hora de Argentina si la fecha del INPI viene como Date(ms) con hora (vacío si es medianoche o no hay)."""
    m = RE_DOTNET.search(valor or "")
    if not m:
        return ""
    momento = dt.datetime.fromtimestamp(int(m.group(1)) / 1000, tz=dt.timezone.utc).astimezone(TZ_AR)
    return "" if (momento.hour, momento.minute) == (0, 0) else momento.strftime("%H:%M")


def dia(iso) -> str:
    if not iso:
        return "—"
    d = iso if isinstance(iso, dt.date) else dt.date.fromisoformat(str(iso)[:10])
    return f"{d.isoformat()} {DIAS[d.weekday()][:3]}"


def resumen_dias(nombre, valores):
    c = collections.Counter(DIAS[v.weekday()] for v in valores)
    total = sum(c.values()) or 1
    print(f"\n{nombre} (total {sum(c.values())}):")
    for d in DIAS:
        print(f"  {d:<10} {c.get(d, 0):>4}  {100 * c.get(d, 0) / total:5.1f}%")


def resumen_demora(nombre, valores):
    if not valores:
        print(f"\n{nombre}: sin datos")
        return
    v = sorted(valores)
    print(f"\n{nombre} ({len(v)} vistas): promedio {statistics.mean(v):.1f} días, mediana {statistics.median(v):g}, "
          f"mínimo {v[0]}, máximo {v[-1]}")
    for tope in (0, 1, 2, 3, 7):
        print(f"  en {tope} día(s) o menos: {sum(1 for x in v if x <= tope)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=600)
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT acta, boletin IS NULL AS pre_boletin, contactado IS TRUE AS contactado,
                      vista_fecha, vista_detectada_en, vista_fecha_notificacion, vista_estado
               FROM marcas WHERE tuvo_vista IS TRUE
               ORDER BY vista_detectada_en DESC NULLS LAST LIMIT %s""",
            (args.limite,),
        )
        marcas = cur.fetchall()
    conn.close()
    print(f"Marcas que alguna vez tuvieron vista: {len(marcas)}", flush=True)

    s = crear_sesion()
    fechas_grilla, demoras_deteccion, demoras_notif, dias_deteccion = [], [], [], []
    horas = collections.Counter()
    indices = collections.Counter()
    sin_fila = 0
    print("\nacta     | etapa | contact. | fecha en la Grilla | hora | detectada por el panel | notificación | demora")
    for n, m in enumerate(marcas, 1):
        try:
            archivos = buscar_archivos_grilla(s, m["acta"])
        except Exception as e:  # noqa: BLE001
            print(f"  [{n}] {m['acta']}: error {type(e).__name__}", flush=True)
            time.sleep(args.delay)
            continue
        for a in archivos:
            indices[(a.get("Indice") or "").strip()] += 1
        filas = [a for a in archivos if "VISTA" in norm(a.get("Indice") or "")]
        if not filas:
            sin_fila += 1
            print(f"{m['acta']} | sin fila «VISTA» en el Índice ({len(archivos)} filas)", flush=True)
            time.sleep(args.delay)
            continue
        # la primera vista de la Grilla (la que el panel detectó primero)
        filas.sort(key=lambda a: _parsear_fecha_grilla(a.get("Fecha") or "") or "9999")
        fila = filas[0]
        f_iso = _parsear_fecha_grilla(fila.get("Fecha") or "")
        hora = hora_dotnet(fila.get("Fecha") or "")
        detectada = m["vista_detectada_en"].astimezone(TZ_AR) if m["vista_detectada_en"] else None
        demora = ""
        if f_iso:
            f = dt.date.fromisoformat(f_iso)
            fechas_grilla.append(f)
            if hora:
                horas[hora[:2]] += 1
            if detectada:
                demoras_deteccion.append((detectada.date() - f).days)
                dias_deteccion.append(detectada.date())
                demora = f"{(detectada.date() - f).days} d"
            if m["vista_fecha_notificacion"]:
                demoras_notif.append((m["vista_fecha_notificacion"] - f).days)
        print(f"{m['acta']} | {'pre' if m['pre_boletin'] else 'post'}  | {'sí' if m['contactado'] else 'no':<8} | "
              f"{dia(f_iso):<18} | {hora or '—':<4} | "
              f"{detectada.strftime('%Y-%m-%d %a %H:%M') if detectada else '—':<22} | "
              f"{dia(m['vista_fecha_notificacion']):<14} | {demora}  ({len(filas)} fila(s) de vista: "
              f"{', '.join(sorted({(a.get('Indice') or '').strip() for a in filas}))})", flush=True)
        time.sleep(args.delay)

    print("\n=== Resumen ===")
    print(f"Marcas revisadas: {len(marcas)}; con fila «VISTA» en la Grilla: {len(fechas_grilla)}; sin esa fila: {sin_fila}")
    resumen_dias("Día de la semana de la fecha de la vista en la Grilla del INPI", fechas_grilla)
    resumen_dias("Día de la semana en que la detectó el panel", dias_deteccion)
    resumen_demora("Días entre la fecha de la Grilla y la detección del panel", demoras_deteccion)
    resumen_demora("Días entre la fecha de la Grilla y la notificación del INPI", demoras_notif)
    if horas:
        print("\nHora (Argentina) que trae la fecha de la Grilla:")
        for h, c in sorted(horas.items()):
            print(f"  {h} hs: {c}")
    else:
        print("\nLa fecha de la Grilla no trae hora (solo el día).")
    por_mes = collections.Counter(f.strftime("%Y-%m") for f in fechas_grilla)
    print("\nVistas por mes (fecha de la Grilla): " + ", ".join(f"{k}: {v}" for k, v in sorted(por_mes.items())))
    print("\nTextos del Índice que aparecen en estas Grillas (cantidad de filas):")
    for texto, c in indices.most_common(60):
        print(f"  {c:>5}  {texto or '(vacío)'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"::error::analizar_fechas_vistas falló: {type(e).__name__}: {e}")
        sys.exit(1)
