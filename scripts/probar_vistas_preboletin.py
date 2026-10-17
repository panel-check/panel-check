"""
PRUEBA (solo lectura) de la revisión de vistas de INPI en marcas PRE-BOLETÍN.

Toma los leads que todavía no tienen boletín (boletin IS NULL, es_lead = true)
y se presentaron hace --dias-minimos días o más, mira la Grilla Digital de cada
una y las clasifica:

  publicada          ya tiene "Hoja Publicacion": salió en un boletín; sigue el
                     flujo de oposiciones de siempre
  orden_publicacion  tiene "Orden de Publicación" (y ninguna vista sin resolver):
                     sale en el próximo miércoles, no hay nada que ofrecer
  vista_pendiente    tiene "Vista de Marcas" y todavía no hay Orden de
                     Publicación ni contestación posterior: CANDIDATA. Se pide
                     además el expediente para ver notificación/vencimiento
  vista_resuelta     tuvo una vista pero ya hay Orden de Publicación o
                     contestación posterior
  en_examen          sin vista ni orden: INPI todavía la está examinando
  sin_datos          la Grilla vino vacía (bloqueo del WAF o sin filas)

Mide además cuánto tarda cada consulta, para dimensionar un proceso diario
(cuántas marcas entran en una corrida y cuánto dura). NO modifica nada: ni la
base ni el panel.

Uso:
    DATABASE_URL=... python3 probar_vistas_preboletin.py [--dias-minimos 7]
        [--limite 600] [--delay 1.5] [--max-minutes 90]
"""

import argparse
import datetime
import os
import statistics
import sys
import time
import unicodedata

import psycopg2
import psycopg2.extras

import monitor_bloqueo
from oposiciones_expediente import clasificar_estado_oposicion, consultar_expediente
from validar_leads import _parsear_fecha_grilla, buscar_archivos_grilla, crear_sesion


def _norm(texto: str) -> str:
    """Mayúsculas y sin tildes, para comparar el Indice de la Grilla."""
    s = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").upper().strip()


def _fechas(archivos, contiene):
    """Fechas ISO de las filas cuyo Indice contiene ese texto (sin tildes)."""
    out = []
    for a in archivos:
        if contiene in _norm(a.get("Indice") or ""):
            f = _parsear_fecha_grilla(a.get("Fecha") or "")
            if f:
                out.append(f)
    return sorted(out)


def clasificar(archivos):
    """(categoria, fecha_ingreso, fecha_vista) a partir de las filas de la Grilla."""
    ingreso = next(iter(_fechas([a for a in archivos if "SOLICITUD DE MARCA" in _norm(a.get("Referencia") or "")],
                                "RECIBO DE INGRESO")), None)
    if _fechas(archivos, "HOJA PUBLICACION"):
        return "publicada", ingreso, None
    vistas = _fechas(archivos, "VISTA")
    ordenes = _fechas(archivos, "ORDEN DE PUBLICACION")
    contestaciones = _fechas(archivos, "CONTEST")
    if vistas:
        ultima = vistas[-1]
        resuelta = any(o >= ultima for o in ordenes) or any(c >= ultima for c in contestaciones)
        return ("vista_resuelta" if resuelta else "vista_pendiente"), ingreso, ultima
    if ordenes:
        return "orden_publicacion", ingreso, None
    return "en_examen", ingreso, None


def _percentil(valores, p):
    v = sorted(valores)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias-minimos", type=int, default=7, help="solo marcas presentadas hace N días o más")
    ap.add_argument("--limite", type=int, default=600, help="tope de marcas a consultar (las más viejas primero)")
    ap.add_argument("--delay", type=float, default=1.5, help="segundos entre marcas (freno de mano)")
    ap.add_argument("--max-minutes", type=float, default=90, help="corta solo pasados estos minutos")
    args = ap.parse_args()

    database_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not database_url:
        print("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT acta, COALESCE(NULLIF(denominacion, ''), denominacion_inpi) AS denominacion, titular, fecha_presentacion::date AS presentada
            FROM marcas
            WHERE es_lead = true AND boletin IS NULL
              AND fecha_presentacion IS NOT NULL
              AND fecha_presentacion::date <= CURRENT_DATE - %s
            ORDER BY fecha_presentacion ASC
            """,
            (args.dias_minimos,),
        )
        todas = cur.fetchall()
    conn.close()
    marcas = todas[: args.limite]
    print(f"Leads pre-boletín con {args.dias_minimos}+ días: {len(todas)}. A consultar en esta prueba: {len(marcas)}.", flush=True)

    s = crear_sesion()
    inicio = time.time()
    duraciones, duraciones_exp = [], []
    conteo, candidatas = {}, []
    consultadas = 0
    corte = None

    for n, m in enumerate(marcas, 1):
        if (time.time() - inicio) / 60 >= args.max_minutes:
            corte = "se agotó el tiempo de la prueba"
            break
        if monitor_bloqueo.debe_cortar():
            corte = "INPI bloqueó consultas seguidas"
            break
        t0 = time.time()
        try:
            archivos = buscar_archivos_grilla(s, m["acta"])
        except Exception as e:  # noqa: BLE001
            print(f"  [{n}/{len(marcas)}] acta {m['acta']}: error {e}", flush=True)
            archivos = []
        dt_grilla = time.time() - t0
        duraciones.append(dt_grilla)
        consultadas += 1

        if not archivos:
            categoria, ingreso, vista = "sin_datos", None, None
        else:
            categoria, ingreso, vista = clasificar(archivos)

        extra = ""
        if categoria == "vista_pendiente":
            t1 = time.time()
            try:
                exp = consultar_expediente(s, m["acta"])
                est = clasificar_estado_oposicion(exp, archivos, None)
                extra = f" | expediente: {est['estado']}, notif {est['notificacion'] or '—'}, vence {est['vencimiento'] or '—'}"
                if est["estado"] == "con_apoderado":
                    categoria = "vista_con_apoderado"
            except Exception as e:  # noqa: BLE001
                extra = f" | expediente: error {e}"
            duraciones_exp.append(time.time() - t1)
            time.sleep(args.delay)
            if categoria == "vista_pendiente":
                candidatas.append((m, ingreso, vista, extra))

        conteo[categoria] = conteo.get(categoria, 0) + 1
        dias = (datetime.date.today() - m["presentada"]).days
        print(f"  [{n}/{len(marcas)}] acta {m['acta']} ({dias} d): {categoria} [{dt_grilla:.1f} s]{extra}", flush=True)
        time.sleep(args.delay)

    total_s = time.time() - inicio
    lineas = [
        f"Leads pre-boletín con {args.dias_minimos}+ días: {len(todas)} (consultadas: {consultadas})",
        f"Tiempo total de la prueba: {total_s / 60:.1f} min ({total_s / max(consultadas, 1):.1f} s por marca, incluida la espera de {args.delay} s)",
    ]
    if duraciones:
        lineas += [
            f"Grilla (2 consultas a INPI): promedio {statistics.mean(duraciones):.1f} s, mediana {statistics.median(duraciones):.1f} s, "
            f"p95 {_percentil(duraciones, 0.95):.1f} s, máximo {max(duraciones):.1f} s",
            f"Proyección de 600 marcas por noche: ≈ {600 * total_s / max(consultadas, 1) / 60:.0f} min",
        ]
    if duraciones_exp:
        lineas.append(f"Expediente (solo marcas con vista): {len(duraciones_exp)} consultas, promedio {statistics.mean(duraciones_exp):.1f} s")
    lineas.append(f"Consultas OK / bloqueadas por el WAF: {monitor_bloqueo._estado['ok']} / {monitor_bloqueo._estado['bloqueadas']}")
    if corte:
        lineas.append(f"AVISO: la prueba se cortó antes de terminar: {corte}")

    print("\n=== Resultado de la prueba ===")
    print("\n".join(lineas))
    print("\nPor categoría:")
    for k, v in sorted(conteo.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print(f"\nCandidatas con vista pendiente: {len(candidatas)}")
    for m, ingreso, vista, extra in candidatas:
        print(f"  acta {m['acta']} · {m['denominacion'] or '—'} · {m['titular'] or '—'} · ingreso {ingreso or '—'} · vista {vista or '—'}{extra}")

    resumen_gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen_gh:
        with open(resumen_gh, "a", encoding="utf-8") as f:
            f.write("## Prueba de vistas en pre-boletín (solo lectura)\n\n")
            f.write("\n".join(f"- {l}" for l in lineas) + "\n\n")
            f.write("| Categoría | Marcas |\n|---|---|\n")
            for k, v in sorted(conteo.items(), key=lambda kv: -kv[1]):
                f.write(f"| {k} | {v} |\n")
            if candidatas:
                f.write("\n### Candidatas con vista pendiente\n\n| Acta | Marca | Titular | Ingreso | Vista | Expediente |\n|---|---|---|---|---|---|\n")
                for m, ingreso, vista, extra in candidatas:
                    f.write(f"| {m['acta']} | {m['denominacion'] or '—'} | {m['titular'] or '—'} | {ingreso or '—'} | {vista or '—'} | {extra.replace(' | expediente: ', '')} |\n")


if __name__ == "__main__":
    main()
