"""
Prueba del juez con IA de la vigilancia contra casos reales (idea 1 del plan).

Corre el juez (Claude) sobre los pares de scripts/casos_vigilancia.py:
  * CONFLICTO: oposiciones reales del INPI: deberían salir idéntica,
    confundible o al menos cercana (las «fuertes», idéntica o confundible).
  * SIN_CONFLICTO: alertas que el equipo marcó como ruido: deberían salir
    no_confundible.
Muestra el veredicto y la razón de cada par y los aciertos. Si hay
DATABASE_URL, usa la frecuencia real de las palabras en cada clase (solo
lectura); si no, la medida el 12/11/2026.

Uso:
    ANTHROPIC_API_KEY=... python3 probar_juez_vigilancia.py [--salida resultado.md]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import cartera  # noqa: E402
import juez_vigilancia as juez  # noqa: E402
import similitud as sim  # noqa: E402
import vigilancia_core as vig  # noqa: E402
from casos_vigilancia import COMUNES_MEDIDAS, CONFLICTO, SIN_CONFLICTO  # noqa: E402


def ajustes():
    aj = {"genericas": frozenset(cartera.PALABRAS_GENERICAS_DEFAULT), "pares": set(cartera.CLASES_RELACIONADAS_DEFAULT),
          "frec": COMUNES_MEDIDAS, "comunes_clase": {}, "comunes_global": frozenset()}
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if dsn:
        import psycopg2
        conn = psycopg2.connect(dsn)
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(vig._sql_universo(
                donde_marcas="WHERE COALESCE(m.fecha_presentacion::date, m.creado_en::date) >= now()::date - 548",
                y_escaneadas="AND COALESCE(s.fecha_presentacion, s.creado_en::date) >= now()::date - 548"))
            aj.update(vig.calcular_comunes(vig._dict_rows(cur), aj["genericas"]))
        conn.close()
    return aj


def par(a, ca, b, cb, aj):
    rel = sim.relacion_clases(ca, cb, aj["pares"])
    r = sim.puntaje_marcas(a, ca, b, cb, aj["genericas"], aj["pares"], True) or {"motivos": [], "relacion_clases": rel}
    m = {"denominacion": a, "tipo": "D", "terminos_vigilancia": ""}
    return vig.par_para_juez(m, [{"acta": "-", "clase": ca}], {"nombre": b, "tipo": "D", "clase": cb},
                             {"relacion_clases": rel, "motivos": r["motivos"]}, aj)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="")
    args = ap.parse_args()
    if not juez.configurado():
        sys.exit("Falta ANTHROPIC_API_KEY")
    aj = ajustes()
    uso = juez.Uso()
    casos = [("conflicto", a, ca, b, cb, f) for a, ca, b, cb, f in CONFLICTO] + \
            [("sin_conflicto", a, ca, b, cb, False) for a, ca, b, cb in SIN_CONFLICTO]
    veredictos = juez.evaluar_lote([par(a, ca, b, cb, aj) for _, a, ca, b, cb, _ in casos], uso=uso)
    filas, ok = [], {"conflicto": 0, "fuerte": 0, "sin_conflicto": 0}
    tot = {"conflicto": len(CONFLICTO), "fuerte": sum(1 for c in CONFLICTO if c[4]), "sin_conflicto": len(SIN_CONFLICTO)}
    for (tipo, a, ca, b, cb, fuerte), v in zip(casos, veredictos):
        ver = v["veredicto"] if v else "ERROR"
        if tipo == "conflicto":
            bien = ver in ("identica", "confundible", "cercana")
            ok["conflicto"] += bien
            if fuerte:
                ok["fuerte"] += ver in ("identica", "confundible")
        else:
            bien = ver == "no_confundible"
            ok["sin_conflicto"] += bien
        filas.append(f"| {'✅' if bien else '❌'} | {tipo}{' (fuerte)' if fuerte else ''} | {a} ({ca}) | {b} ({cb}) | "
                     f"{juez.VEREDICTO_LEGIBLE.get(ver, ver)} / {v['nivel'] if v else ''} | {(v or {}).get('razon', '')} |")
    rapido, fino = juez.modelos()
    rep = [f"# Prueba del juez de vigilancia ({rapido} → {fino})", "",
           f"- Oposiciones reales detectadas (cercana o más): **{ok['conflicto']} de {tot['conflicto']}**",
           f"- Oposiciones fuertes como idéntica/confundible (sensibilidad «Similares»): **{ok['fuerte']} de {tot['fuerte']}**",
           f"- Ruido marcado por el equipo descartado: **{ok['sin_conflicto']} de {tot['sin_conflicto']}**",
           f"- Uso: {uso.resumen()} · costo máx. USD {juez.costo_estimado(uso)}", "",
           "| | Tipo | Marca anterior / del cliente | Solicitud nueva | Veredicto | Razón |", "|---|---|---|---|---|---|"] + filas
    txt = "\n".join(rep) + "\n"
    print(txt)
    if args.salida:
        open(args.salida, "w", encoding="utf-8").write(txt)


if __name__ == "__main__":
    main()
