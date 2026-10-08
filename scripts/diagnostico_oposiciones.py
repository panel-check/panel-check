"""
Diagnóstico de SOLO LECTURA de la revisión de oposiciones: para los leads
publicados en los últimos N días, consulta la Grilla Digital y el expediente de
INPI, corre el clasificador actual (oposiciones_expediente.py) y lo compara con
el estado que hoy tiene guardado el panel. No escribe nada en la base.

Sirve para revisar juntos qué casos matchean bien y cuáles no, y qué términos
usa INPI de verdad. Al final imprime:
  - de qué estado guardado a qué estado nuevo pasa cada acta (matriz de cambios),
  - el vocabulario real (Indice + Referencia) de las filas posteriores a la
    publicación en las actas con oposición, para ajustar los términos,
  - cuántas actas tienen "Acompaña Poder"/"Ratifica Gestión" en la Grilla y, de
    esas, cuántas tienen representante del titular según el expediente (si el
    poder fuera casi siempre del oponente, esa regla descartaría leads buenos),
  - cuántas Grillas llegan al tope de 50 filas (la consulta no pagina).

El repo es público: no se imprimen nombres ni datos de personas, solo número de
acta, estados y los textos de Indice/Referencia/Fecha de la Grilla.

Uso:
    DATABASE_URL=... python3 diagnostico_oposiciones.py [--dias 60] [--limite 150]
                                                         [--max-minutes 20] [--delay 1.5]
"""

import argparse
import collections
import os
import sys
import time

import psycopg2
import psycopg2.extras

import monitor_bloqueo
from oposiciones_expediente import (
    GRILLA_PODER,
    _fecha_valida,
    _norm,
    _senales_grilla,
    clasificar_estado_oposicion,
    consultar_expediente,
    titular_con_representante,
)
from validar_leads import (
    _es_oposicion_de_tercero,
    buscar_archivos_grilla,
    buscar_fila_oposicion,
    crear_sesion,
)

TOPE_GRILLA = 50  # buscar_archivos_grilla pide limit=50 y no pagina

SQL_ACTAS = """
    SELECT acta, fecha_publicacion, es_lead, caracter, tuvo_oposicion, estado_oposicion
    FROM marcas
    WHERE fecha_publicacion IS NOT NULL
      AND fecha_publicacion >= CURRENT_DATE - %(dias)s
      AND (tuvo_oposicion IS TRUE OR representacion_posterior_oposicion IS TRUE OR es_lead IS TRUE)
    ORDER BY (tuvo_oposicion IS TRUE) DESC, fecha_publicacion DESC
    LIMIT %(limite)s
"""


def poder_en_grilla(archivos: list[dict], fecha_publicacion: str | None) -> dict:
    """Filas de poder/gestión de la Grilla posteriores a la publicación, separadas
    en las que caen el MISMO DÍA que una fila de oposición (típico: el abogado del
    oponente acompaña su poder al presentar) y las posteriores a todas las
    oposiciones. Devuelve {"mismo_dia": n, "posterior": n, "anterior": n}."""
    fechas_opo = []
    for a in archivos or []:
        if _es_oposicion_de_tercero(a):
            f = _fecha_valida(a.get("Fecha") or "")
            if f and (not fecha_publicacion or f >= fecha_publicacion):
                fechas_opo.append(f)
    out = {"mismo_dia": 0, "posterior": 0, "anterior": 0}
    if not fechas_opo:
        return out
    for a in archivos or []:
        texto = _norm(f"{a.get('Indice') or ''} {a.get('Referencia') or ''}")
        if not any(t in texto for t in GRILLA_PODER):
            continue
        f = _fecha_valida(a.get("Fecha") or "")
        if not f:
            continue
        if f in fechas_opo:
            out["mismo_dia"] += 1
        elif f > max(fechas_opo):
            out["posterior"] += 1
        elif f >= min(fechas_opo):
            out["mismo_dia"] += 1  # entre dos oposiciones: se trata como acompañante de una
        else:
            out["anterior"] += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias", type=int, default=60, help="publicadas en los últimos N días")
    ap.add_argument("--limite", type=int, default=150, help="tope de actas")
    ap.add_argument("--max-minutes", type=float, default=20)
    ap.add_argument("--delay", type=float, default=1.5, help="segundos entre acta y acta")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")

    inicio = time.time()
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=True)  # garantiza que no se escribe nada
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(SQL_ACTAS, {"dias": args.dias, "limite": args.limite})
        actas = cur.fetchall()
    conn.close()
    print(f"Actas a diagnosticar: {len(actas)} (publicadas en los últimos {args.dias} días)\n")

    s = crear_sesion()
    cambios = collections.Counter()
    vocabulario = collections.Counter()
    señales_n = collections.Counter()
    poder_actas = poder_con_rep_titular = poder_mismo_dia = poder_posterior = 0
    topadas = sin_consulta = error_lectura = revisadas = 0

    for i, fila in enumerate(actas, 1):
        if (time.time() - inicio) / 60 >= args.max_minutes:
            print(f"Se llegó al tope de {args.max_minutes:g} minutos.")
            break
        if monitor_bloqueo.debe_cortar():
            print("Se corta por bloqueos seguidos de INPI.")
            break
        acta = fila["acta"]
        pub = fila["fecha_publicacion"].isoformat()
        archivos = buscar_archivos_grilla(s, acta)
        if not archivos:
            sin_consulta += 1
            print(f"[{i}/{len(actas)}] acta {acta}: sin poder consultar la Grilla")
            time.sleep(args.delay)
            continue
        exp = consultar_expediente(s, acta)
        if exp.get("bloqueado"):
            sin_consulta += 1
            print(f"[{i}/{len(actas)}] acta {acta}: INPI bloqueó el expediente")
            time.sleep(args.delay)
            continue
        revisadas += 1
        if exp.get("error_lectura"):
            error_lectura += 1
        if len(archivos) >= TOPE_GRILLA:
            topadas += 1

        est = clasificar_estado_oposicion(exp, archivos, pub)
        nuevo = est["estado"]
        viejo = fila["estado_oposicion"] or ("sin_oposicion" if not fila["tuvo_oposicion"] else "(sin estado)")
        cambios[(viejo, nuevo)] += 1

        desde = min([o["presentacion"] for o in exp.get("oposiciones", []) if o["presentacion"]], default=None) or pub
        sen = _senales_grilla(archivos, desde)
        activas = [k for k in ("trabajando", "desistio", "poder", "notificada") if sen[k]]
        for k in activas:
            señales_n[k] += 1
        pg = poder_en_grilla(archivos, pub)
        if sen["poder"]:
            poder_actas += 1
            poder_con_rep_titular += int(titular_con_representante(exp))
            poder_mismo_dia += int(pg["mismo_dia"] > 0)
            poder_posterior += int(pg["posterior"] > 0)

        hay_opo = nuevo != "sin_oposicion" or bool(fila["tuvo_oposicion"]) or bool(buscar_fila_oposicion(archivos, pub))
        if hay_opo:
            for a in archivos:
                f = _fecha_valida(a.get("Fecha") or "")
                if f and f >= pub:
                    vocabulario[((a.get("Indice") or "").strip(), (a.get("Referencia") or "").strip())] += 1

        if viejo != nuevo or hay_opo:
            marca = "  <-- CAMBIA" if viejo != nuevo else ""
            print(f"[{i}/{len(actas)}] acta {acta} pub={pub} guardado={viejo} -> nuevo={nuevo}{marca}")
            print(f"    señales Grilla: {','.join(activas) or '-'} | poder: {pg} | "
                  f"expediente: opos={len(exp.get('oposiciones', []))} vistas={len(exp.get('vistas', []))} "
                  f"titular_con_representante={titular_con_representante(exp)} | filas Grilla={len(archivos)}")
            for a in archivos:
                f = _fecha_valida(a.get("Fecha") or "")
                if f and f >= pub:
                    print(f"      {a.get('Fecha')} | {a.get('Indice')} | {a.get('Referencia')}")
        time.sleep(args.delay)

    print("\n" + "=" * 70)
    print(f"Diagnosticadas: {revisadas} de {len(actas)} ({sin_consulta} sin poder consultar, "
          f"{error_lectura} con error de lectura de las tablas del expediente)")
    print("\nCambios de estado (guardado -> nuevo):")
    for (v, n), c in sorted(cambios.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {c:4d}  {v} -> {n}{'' if v == n else '   (cambia)'}")
    print("\nSeñales de la Grilla detectadas (actas con oposición):", dict(señales_n))
    print(f"\n'Acompaña Poder'/'Ratifica Gestión' en la Grilla: {poder_actas} actas")
    print(f"  con representante del TITULAR según el expediente: {poder_con_rep_titular}")
    print(f"  con el poder el mismo día que una oposición (probable poder del oponente): {poder_mismo_dia}")
    print(f"  con el poder posterior a todas las oposiciones: {poder_posterior}")
    print(f"\nGrillas que llegan al tope de {TOPE_GRILLA} filas (la consulta no pagina): {topadas}")
    print("\nVocabulario real (Indice | Referencia) en actas con oposición, filas desde la publicación:")
    for (ind, ref), c in vocabulario.most_common(60):
        print(f"  {c:4d}  {ind} | {ref}")


if __name__ == "__main__":
    main()
