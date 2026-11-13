"""
Diagnóstico de ubicaciones del expediente (vigilancia de expediente, 13/11/2026). Solo lee.

Arma la lista real de sectores de «UBICACIÓN ACTUAL» que usa el INPI, para cerrar el catálogo
de panel/seguimiento_expediente.py:

  A) Cartera de clientes: cuenta los valores ya guardados en cartera_marcas (sin consultar al
     INPI). Ahí hay marcas concedidas, así que se ve qué sector viene después de Resoluciones.
  B) Muestra al azar de marcas pre-boletín (sin boletín) y publicadas: lee el expediente en el
     INPI y cuenta la ubicación de cada una.

El log de Actions es público: solo se imprimen sectores, estados del trámite y cantidades
(nunca titulares, denominaciones ni datos de personas).

Uso:
    DATABASE_URL=... python3 diagnostico_ubicaciones.py [--pre 60] [--publicadas 60] [--delay 1.5]
"""

import argparse
import collections
import os
import statistics
import sys
import time
from datetime import date

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import inpi_lead  # noqa: E402
import seguimiento_expediente as se  # noqa: E402

import monitor_bloqueo  # noqa: E402
from validar_leads import crear_sesion  # noqa: E402


def _etiqueta(ubicacion):
    clave = se.sector_de(ubicacion)
    return se.CATALOGO[clave]["nombre"] if clave else "DESCONOCIDO"


def _imprimir_tabla(titulo, filas, resumen):
    print(f"\n=== {titulo} ===", flush=True)
    resumen.append(f"### {titulo}\n")
    if not filas:
        print("  (sin datos)")
        resumen.append("Sin datos.\n")
        return
    resumen.append("| Ubicación (texto limpio) | Catálogo | Estado del trámite | Cantidad | Días en el sector (mediana) |")
    resumen.append("|---|---|---|---|---|")
    for (limpio, estado), (n, ejemplo, dias) in sorted(filas.items(), key=lambda x: -x[1][0]):
        med = f"{statistics.median(dias):.0f}" if dias else "—"
        print(f"  {n:4d}  {limpio or '(vacío)':40s} → {_etiqueta(ejemplo):16s} | estado: {estado or '—'} | mediana {med} días",
              flush=True)
        resumen.append(f"| {limpio or '(vacío)'} | {_etiqueta(ejemplo)} | {estado or '—'} | {n} | {med} |")
    resumen.append("")


def _sumar(filas, ubicacion, fecha, estado):
    clave = (se.limpiar(ubicacion), (estado or "").strip())
    n, ejemplo, dias = filas.get(clave, (0, ubicacion, []))
    if fecha:
        try:
            dias = dias + [(date.today() - date.fromisoformat(str(fecha)[:10])).days]
        except ValueError:
            pass
    filas[clave] = (n + 1, ejemplo, dias)


def cartera(conn, resumen):
    filas = {}
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT ubicacion_actual, ubicacion_fecha, estado_tramite FROM cartera_marcas "
                    "WHERE ubicacion_actual IS NOT NULL")
        for r in cur.fetchall():
            _sumar(filas, r["ubicacion_actual"], r["ubicacion_fecha"], r["estado_tramite"])
    _imprimir_tabla("A) Cartera de clientes (ya guardado, sin consultar INPI)", filas, resumen)


def muestra(conn, publicadas: bool, n: int):
    cond = "boletin IS NOT NULL" if publicadas else "boletin IS NULL"
    meses = 12 if publicadas else 4
    with conn.cursor() as cur:
        cur.execute(f"""SELECT acta FROM marcas
                        WHERE {cond} AND fecha_presentacion IS NOT NULL
                          AND fecha_presentacion >= now() - interval '{meses} months'
                        ORDER BY random() LIMIT %s""", (n,))
        return [r[0] for r in cur.fetchall()]


def consultar(actas, titulo, delay, resumen):
    s = crear_sesion()
    filas, sin_ubicacion, errores = {}, 0, 0
    for i, acta in enumerate(actas, 1):
        if monitor_bloqueo.debe_cortar():
            print("  INPI bloqueó consultas seguidas: se corta la muestra", flush=True)
            break
        exp = inpi_lead.consultar_expediente(acta, s=s, con_grilla=False)
        if exp.get("estado_consulta") != "ok":
            errores += 1
            print(f"  [{i}/{len(actas)}] acta {acta}: {exp.get('estado_consulta')}", flush=True)
        elif not exp.get("ubicacion_actual"):
            sin_ubicacion += 1
            print(f"  [{i}/{len(actas)}] acta {acta}: sin UBICACIÓN ACTUAL", flush=True)
        else:
            _sumar(filas, exp["ubicacion_actual"], exp.get("ubicacion_fecha"), exp.get("estado_tramite"))
            print(f"  [{i}/{len(actas)}] acta {acta}: {se.limpiar(exp['ubicacion_actual'])} → "
                  f"{_etiqueta(exp['ubicacion_actual'])}", flush=True)
        time.sleep(delay)
    _imprimir_tabla(titulo, filas, resumen)
    nota = f"Sin ubicación: {sin_ubicacion} · No se pudieron leer: {errores}"
    print("  " + nota, flush=True)
    resumen.append(nota + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", type=int, default=60, help="Cuántas marcas pre-boletín mirar")
    ap.add_argument("--publicadas", type=int, default=60, help="Cuántas marcas publicadas mirar")
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.set_session(readonly=True)
    resumen = ["## Ubicaciones del expediente\n"]
    cartera(conn, resumen)
    if args.pre:
        consultar(muestra(conn, False, args.pre), "B1) Muestra pre-boletín (consulta al INPI)", args.delay, resumen)
    if args.publicadas:
        consultar(muestra(conn, True, args.publicadas), "B2) Muestra publicadas (consulta al INPI)", args.delay, resumen)
    conn.close()

    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("\n".join(resumen) + "\n")


if __name__ == "__main__":
    main()
