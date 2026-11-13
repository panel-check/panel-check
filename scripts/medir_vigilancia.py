"""
Medición de solo lectura de la vigilancia marcaria (Clientes → Vigilancia).

Para estimar cuántas consultas por día haría un juez con IA sobre las alertas
de parecido, y qué reglas generan más alertas. No escribe nada en la base
(sesión readonly) y no saca nombres de marcas ni de personas: solo cantidades.

Uso:
    DATABASE_URL=... python3 medir_vigilancia.py [--dias 30] [--salida resultado.md]
"""

import argparse
import os
import statistics
import sys

import psycopg2


def categoria(motivos: str) -> str:
    m = (motivos or "").lower()
    primero = m.split(" · ")[0]
    if "mismo concepto" in primero:
        return "concepto (IA)"
    if primero.startswith("comparten") or primero.startswith("palabras parecidas"):
        return "palabra en común"
    if "contiene" in primero:
        return "una contiene a la otra"
    if "suenan igual" in primero:
        return "suenan igual"
    if "suenan parecido" in primero:
        return "suenan parecido"
    if "idéntico" in primero or "sin espacios" in primero:
        return "idéntico"
    if "escribirse" in primero:
        return "parecido escrito"
    return "otro"


def tabla(titulo, filas, cab):
    out = [f"### {titulo}", "", "| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    out += ["| " + " | ".join(str(x) for x in f) + " |" for f in filas]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=30)
    ap.add_argument("--salida", default="")
    a = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL")
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True)
    cur = conn.cursor()
    d = a.dias
    rep = [f"# Medición de la vigilancia (últimos {d} días)", ""]

    cur.execute("SELECT COUNT(*) FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id WHERE cm.vigilar AND c.activo")
    rep.append(f"- Marcas vigiladas hoy: **{cur.fetchone()[0]}**")
    cur.execute("SELECT clave, valor FROM vigilancia_config WHERE clave IN ('umbral','nivel_alta','nivel_media','meses_universo')")
    rep.append("- Ajustes: " + ", ".join(f"{k}={v}" for k, v in sorted(cur.fetchall())))

    cur.execute("""SELECT (comparada_en AT TIME ZONE 'America/Argentina/Buenos_Aires')::date d, COUNT(*)
                   FROM vigilancia_comparadas WHERE comparada_en >= now() - %s * interval '1 day' AND acta NOT LIKE '%%#agente'
                   GROUP BY 1 ORDER BY 1""", (d,))
    comp = cur.fetchall()
    if comp:
        v = [c for _, c in comp]
        rep.append(f"- Solicitudes comparadas por día (con corrida): promedio **{round(statistics.mean(v))}**, mediana {round(statistics.median(v))}, máximo {max(v)} ({len(v)} días con corrida)")

    cur.execute("""SELECT id, (creada_en AT TIME ZONE 'America/Argentina/Buenos_Aires')::date, nivel, motivos, estado,
                          acta_cliente, cliente_id, acta_nueva
                   FROM vigilancia_alertas WHERE tipo = 'similitud' AND creada_en >= now() - %s * interval '1 day'""", (d,))
    al = cur.fetchall()
    rep.append(f"- Alertas de parecido creadas en el período: **{len(al)}**")
    if not al:
        txt = "\n".join(rep)
        print(txt)
        return
    por_dia = {}
    for r in al:
        por_dia[r[1]] = por_dia.get(r[1], 0) + 1
    v = list(por_dia.values())
    rep.append(f"- Alertas por día (días con alertas): promedio **{round(statistics.mean(v), 1)}**, mediana {statistics.median(v)}, máximo {max(v)} ({len(v)} días)")
    rep.append(f"- Alertas por día calendario (promedio sobre {d} días): **{round(len(al) / d, 1)}**")

    # Pares únicos si se agrupara por marca del cliente (misma denominación y cliente) en vez de por acta.
    cur.execute("""SELECT COUNT(DISTINCT (a.cliente_id, upper(coalesce(cm.denominacion, a.acta_cliente)), a.acta_nueva))
                   FROM vigilancia_alertas a LEFT JOIN cartera_marcas cm ON cm.acta = a.acta_cliente
                   WHERE a.tipo='similitud' AND a.creada_en >= now() - %s * interval '1 day'""", (d,))
    unicas = cur.fetchone()[0]
    rep.append(f"- Si se agrupa por marca (no por acta): **{unicas}** alertas ({len(al) - unicas} duplicadas)")
    rep.append("")

    cats, cat_estado, niveles = {}, {}, {}
    for r in al:
        c = categoria(r[3])
        cats[c] = cats.get(c, 0) + 1
        cat_estado.setdefault(c, {}).setdefault(r[4], 0)
        cat_estado[c][r[4]] += 1
        niveles[r[2]] = niveles.get(r[2], 0) + 1
    rep.append(tabla("Por nivel", sorted(niveles.items(), key=lambda x: -x[1]), ["Nivel", "Alertas"]))
    estados = ["nueva", "monitorear", "oponer", "opuesta", "descartada"]
    filas = []
    for c, n in sorted(cats.items(), key=lambda x: -x[1]):
        filas.append([c, n, f"{round(100 * n / len(al))}%"] + [cat_estado[c].get(e, 0) for e in estados])
    rep.append(tabla("Por regla que la disparó (primer motivo)", filas, ["Regla", "Alertas", "%"] + estados))

    # Alertas que siguen «nueva» con el plazo de oposición ya cerrado.
    cur.execute("""SELECT COUNT(*) FROM vigilancia_alertas a
                   LEFT JOIN marcas m ON m.acta = a.acta_nueva
                   LEFT JOIN boletines b ON b.numero = m.boletin
                   WHERE a.tipo='similitud' AND a.estado IN ('nueva','monitorear','oponer')
                     AND COALESCE(m.fecha_publicacion, b.fecha) IS NOT NULL
                     AND COALESCE(m.fecha_publicacion, b.fecha) + 30 < (now() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date""")
    rep.append(f"- Alertas abiertas (todas las fechas) con el plazo de oposición ya vencido: **{cur.fetchone()[0]}**")
    cur.execute("SELECT estado, COUNT(*) FROM vigilancia_alertas WHERE tipo='similitud' GROUP BY 1 ORDER BY 2 DESC")
    rep.append("- Total histórico por estado: " + ", ".join(f"{e} {n}" for e, n in cur.fetchall()))

    cur.execute("SELECT estado, COALESCE(ia_veredicto, '(sin juzgar)'), COUNT(*) FROM vigilancia_alertas WHERE tipo='similitud' GROUP BY 1,2 ORDER BY 1,2")
    rep.append(tabla("Estado y veredicto de la IA (todas las alertas)", cur.fetchall(), ["Estado", "Veredicto IA", "Alertas"]))
    cur.execute("SELECT COUNT(*) FROM vigilancia_alertas WHERE tipo='similitud' AND estado IN ('nueva','monitorear','oponer')")
    rep.append(f"- Alertas abiertas hoy (lo que se ve en «Abiertas»): **{cur.fetchone()[0]}**")
    cur.execute("SELECT COUNT(*) FROM vigilancia_alertas WHERE tipo='similitud' AND jsonb_array_length(COALESCE(actas_cliente,'[]'::jsonb)) > 1")
    rep.append(f"- Alertas que juntan varias actas/clases de la misma marca: **{cur.fetchone()[0]}**")
    cur.execute("""SELECT (cm.acta IS NOT NULL) AS en_cartera, COALESCE(cm.vigilar, false), COALESCE(c.activo, false), COUNT(*)
                   FROM vigilancia_alertas a LEFT JOIN cartera_marcas cm ON cm.acta = a.acta_cliente LEFT JOIN clientes c ON c.id = a.cliente_id
                   WHERE a.tipo='similitud' AND a.estado='nueva' AND a.ia_veredicto IS NULL GROUP BY 1,2,3""")
    rep.append(tabla("Nuevas sin juzgar: ¿la marca del cliente sigue en la cartera?", cur.fetchall(), ["En cartera", "Vigilar", "Cliente activo", "Alertas"]))
    cur.execute("SELECT metricas FROM registro_corridas WHERE proceso = 'vigilancia.yml' ORDER BY creado_en DESC LIMIT 1")
    f = cur.fetchone()
    rep.append(f"- Última corrida de la vigilancia: {f[0] if f else 'sin datos'}")
    txt = "\n".join(rep) + "\n"
    print(txt)
    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as f:
            f.write(txt)


if __name__ == "__main__":
    main()
