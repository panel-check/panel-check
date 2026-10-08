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
  - los "Acompaña Poder"/"Ratifica Gestión" de la Grilla en tres momentos (el día de
    una oposición, entre la oposición y la notificación, y después de notificar al
    titular) y, en cada uno, cuántas actas tienen representante del titular según el
    expediente; las que no, se listan para revisarlas a mano,
  - cuántas Grillas llegan al tope de 50 filas (la consulta no pagina).

El repo es público: no se imprimen nombres ni datos de personas, solo número de
acta, estados y los textos de Indice/Referencia/Fecha de la Grilla.

Uso:
    DATABASE_URL=... python3 diagnostico_oposiciones.py [--dias 60] [--limite 150]
                                                         [--max-minutes 20] [--delay 1.5]
"""

import argparse
import collections
import datetime as dt
import os
import re
import sys
import time

import psycopg2
import psycopg2.extras

import monitor_bloqueo
from oposiciones_expediente import (
    GRILLA_PODER,
    dias_de_oposicion,
    es_presentacion_oposicion,
    _fecha_valida,
    _norm,
    _senales_grilla,
    clasificar_estado_oposicion,
    consultar_expediente,
    titular_con_representante,
)
from validar_leads import (
    buscar_archivos_grilla,
    buscar_fila_oposicion,
    crear_sesion,
)

RE_DOTNET = re.compile(r"Date\((-?\d+)")
ART = dt.timezone(dt.timedelta(hours=-3))
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


def ts_ms(valor):
    """Milisegundos de un campo Fecha .NET de la Grilla ("/Date(ms)/"), o None."""
    m = RE_DOTNET.search(valor or "")
    return int(m.group(1)) if m else None


def fmt_fecha(valor) -> str:
    """Fecha y hora de la Grilla en hora argentina (el portal las manda en UTC)."""
    ms = ts_ms(valor)
    if ms is None:
        return str(valor)
    return dt.datetime.fromtimestamp(ms / 1000, tz=ART).strftime("%d/%m/%Y %H:%M")


def horas_desde_oposicion(archivos: list[dict], fila: dict):
    """Horas entre esta fila y la presentación de oposición (Recibo + Opo.) inmediatamente
    anterior de la Grilla, o None si no hay ninguna anterior."""
    t = ts_ms(fila.get("Fecha"))
    if t is None:
        return None
    previas = [ts_ms(a.get("Fecha")) for a in archivos if es_presentacion_oposicion(a)]
    previas = [x for x in previas if x is not None and x <= t]
    return (t - max(previas)) / 3_600_000 if previas else None


def poder_en_grilla(archivos: list[dict], desde: str | None, notificacion: str | None,
                    dias_oposicion: set) -> dict:
    """Cuenta las filas de poder/gestión de la Grilla (desde `desde`) en tres grupos:
      mismo_dia_oposicion          -- el día que se presentó una oposición o el siguiente
                                      (poder del oponente: nunca descarta)
      entre_oposicion_y_notificar  -- otro día, pero antes de notificar al titular (o sin
                                      notificación conocida): hoy NO descarta; puede ser
                                      un gestor del titular que se suma antes de la cédula
      despues_de_notificar         -- de la notificación en adelante y de otro día que
                                      una oposición: es lo que hoy descarta el lead"""
    out = {"mismo_dia_oposicion": 0, "entre_oposicion_y_notificar": 0, "despues_de_notificar": 0}
    for a in archivos or []:
        texto = _norm(f"{a.get('Indice') or ''} {a.get('Referencia') or ''}")
        if not any(t in texto for t in GRILLA_PODER):
            continue
        f = _fecha_valida(a.get("Fecha") or "")
        if not f or (desde and f < desde):
            continue
        if f in dias_oposicion:
            out["mismo_dia_oposicion"] += 1
        elif notificacion and f >= notificacion:
            out["despues_de_notificar"] += 1
        else:
            out["entre_oposicion_y_notificar"] += 1
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
    # por grupo de poder: actas, con representante del titular en el expediente, y las que no
    grupos = {g: {"actas": 0, "con_rep": 0, "sin_rep": []} for g in
              ("mismo_dia_oposicion", "entre_oposicion_y_notificar", "despues_de_notificar")}
    topadas = sin_consulta = error_lectura = revisadas = 0
    horas_poder = []
    actas_trabajando = []

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
        notif_exp = min([o["notificacion"] for o in exp.get("oposiciones", []) if o["notificacion"]], default=None)
        presentaciones = [o["presentacion"] for o in exp.get("oposiciones", []) if o["presentacion"]]
        sen = _senales_grilla(archivos, desde, notif_exp, presentaciones)
        activas = [k for k in ("trabajando", "desistio", "poder", "notificada") if sen[k]]
        for k in activas:
            señales_n[k] += 1
        referencia = min([f for f in (sen["fecha_notificacion"], notif_exp) if f], default=None)
        dias_opo = dias_de_oposicion(archivos, presentaciones)
        pg = poder_en_grilla(archivos, desde, referencia, dias_opo)
        for a in archivos:
            if any(t in _norm(f"{a.get('Indice')} {a.get('Referencia')}") for t in GRILLA_PODER):
                horas_poder.append(horas_desde_oposicion(archivos, a))
        if sen["trabajando"]:
            actas_trabajando.append(acta)
        tiene_rep = titular_con_representante(exp)
        for g, n in pg.items():
            if n:
                grupos[g]["actas"] += 1
                if tiene_rep:
                    grupos[g]["con_rep"] += 1
                else:
                    grupos[g]["sin_rep"].append(acta)

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
                    h = horas_desde_oposicion(archivos, a) if any(
                        t in _norm(f"{a.get('Indice')} {a.get('Referencia')}") for t in GRILLA_PODER) else None
                    extra = f"   (+{h:.1f} h desde la oposición)" if h is not None else ""
                    print(f"      {fmt_fecha(a.get('Fecha'))} | {a.get('Indice')} | {a.get('Referencia')}{extra}")
        time.sleep(args.delay)

    print("\n" + "=" * 70)
    print(f"Diagnosticadas: {revisadas} de {len(actas)} ({sin_consulta} sin poder consultar, "
          f"{error_lectura} con error de lectura de las tablas del expediente)")
    print("\nCambios de estado (guardado -> nuevo):")
    for (v, n), c in sorted(cambios.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {c:4d}  {v} -> {n}{'' if v == n else '   (cambia)'}")
    print("\nSeñales de la Grilla detectadas (actas con oposición):", dict(señales_n))
    print("\n'Acompaña Poder'/'Ratifica Gestión' en la Grilla, por momento (actas; con representante del "
          "titular según el expediente; sin él):")
    etiquetas = {
        "mismo_dia_oposicion": "el día de una oposición (poder del oponente, no descarta)",
        "entre_oposicion_y_notificar": "entre la oposición y la notificación (hoy NO descarta)",
        "despues_de_notificar": "después de notificar al titular (hoy SÍ descarta)",
    }
    for g, et in etiquetas.items():
        d = grupos[g]
        print(f"  {et}: {d['actas']} actas, {d['con_rep']} con representante del titular, "
              f"{len(d['sin_rep'])} sin él")
        if d["sin_rep"]:
            print(f"      sin representante en el expediente (revisar a mano): {', '.join(d['sin_rep'])}")
    if horas_poder:
        con = [h for h in horas_poder if h is not None]
        print(f"\nHoras entre la presentación de la oposición y cada poder ({len(horas_poder)} filas de poder): "
              f"hasta 12 h: {sum(h <= 12 for h in con)}, 12-24 h: {sum(12 < h <= 24 for h in con)}, "
              f"24-48 h: {sum(24 < h <= 48 for h in con)}, más de 48 h: {sum(h > 48 for h in con)}, "
              f"sin oposición anterior: {len(horas_poder) - len(con)}")
    print(f"\nActas donde se detectó 'Recibo de Ingreso' + 'Escritos de Marcas' (revisar a mano que sea del titular): "
          f"{', '.join(actas_trabajando) or '-'}")
    print(f"\nGrillas que llegan al tope de {TOPE_GRILLA} filas (la consulta no pagina): {topadas}")
    print("\nVocabulario real (Indice | Referencia) en actas con oposición, filas desde la publicación:")
    for (ind, ref), c in vocabulario.most_common(60):
        print(f"  {c:4d}  {ind} | {ref}")


if __name__ == "__main__":
    main()
