"""Solo lectura: cuántos pares daría el motor con distintos umbrales (últimos 30 días de solicitudes)."""
import collections, datetime as dt, os, sys
import psycopg2, psycopg2.extras
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import vigilancia_core as vig, similitud as sim
conn = psycopg2.connect(os.environ.get("DATABASE_URL") or os.environ["DATABASE_PUBLIC_URL"]); conn.set_session(readonly=True)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
aj = vig.cargar_ajustes(cur); marcas, indice = vig._cartera_en_memoria(cur, aj); gen = aj["genericas"]
cur.execute(vig._sql_universo(donde_marcas="WHERE COALESCE(m.fecha_presentacion::date, m.creado_en::date) >= %(d)s",
                              y_escaneadas="AND COALESCE(s.fecha_presentacion, s.creado_en::date) >= %(d)s"),
            {"d": dt.date.today() - dt.timedelta(days=548)})
uni = vig._dict_rows(cur); aj.update(vig.calcular_comunes(uni, gen))
desde = dt.date.today() - dt.timedelta(days=30)
recientes = [f for f in uni if (f.get("fecha_presentacion") or desde) >= desde and f.get("nombre")]
en_cartera = {m["acta"] for m in marcas}
bandas = collections.Counter(); ejemplos = collections.defaultdict(list)
for f in recientes:
    if f["acta"] in en_cartera: continue
    grupos = {}
    for m in vig._candidatas(indice, f["nombre"], gen):
        r = vig._mejor_contra(f["nombre"], f.get("clase"), m, aj)
        if r and r["puntaje"] > grupos.get(m["grupo"], (0,))[0]:
            grupos[m["grupo"]] = (r["puntaje"], m["denominacion"], m.get("clase"), f["nombre"], f.get("clase"), r["motivos"][0] if r["motivos"] else "")
    for g in grupos.values():
        p = g[0]
        for u in (75, 70, 65, 60, 55, 50):
            if p >= u: bandas[u] += 1
        if 60 <= p < 75 and len(ejemplos[p // 5 * 5]) < 12:
            ejemplos[p // 5 * 5].append(g)
out = [f"Solicitudes de los últimos 30 días: {len(recientes)}; marcas vigiladas: {len(marcas)}", "",
       "| Umbral | Pares en 30 días | Por día |", "|---|---|---|"]
for u in (75, 70, 65, 60, 55, 50):
    out.append(f"| {u} | {bandas[u]} | {round(bandas[u]/30,1)} |")
for b in sorted(ejemplos, reverse=True):
    out.append(f"\nEjemplos con puntaje {b}-{b+4}:")
    out += [f"- {g[1]} ({g[2]}) / {g[3]} ({g[4]}): {g[0]} · {g[5]}" for g in ejemplos[b]]
open("/tmp/medicion.md", "w").write("\n".join(out) + "\n"); print("\n".join(out))
