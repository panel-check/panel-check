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
bandas = collections.Counter(); bandas_todas = collections.Counter()
for f in recientes:
    if f["acta"] in en_cartera: continue
    grupos = {}
    for m in vig._candidatas(indice, f["nombre"], gen):
        r = vig._mejor_contra(f["nombre"], f.get("clase"), m, aj)
        if r: grupos[m["grupo"]] = max(grupos.get(m["grupo"], 0), r["puntaje"])
    for p in grupos.values():
        for u in (75, 70, 65, 60, 55, 50):
            if p >= u: bandas[u] += 1
    # Sin índice (todas las marcas): cuántos pares aparecerían si el índice no filtrara
    for m in marcas:
        r = vig._mejor_contra(f["nombre"], f.get("clase"), m, aj)
        if r:
            for u in (75, 70, 65, 60, 55, 50):
                if r["puntaje"] >= u: bandas_todas[u] += 1
out = [f"Solicitudes de los últimos 30 días: {len(recientes)}; marcas vigiladas: {len(marcas)}", "",
       "| Umbral | Pares en 30 días (con índice) | Por día | Pares sin índice (todas contra todas) | Por día |", "|---|---|---|---|---|"]
for u in (75, 70, 65, 60, 55, 50):
    out.append(f"| {u} | {bandas[u]} | {round(bandas[u]/30,1)} | {bandas_todas[u]} | {round(bandas_todas[u]/30,1)} |")
open("/tmp/medicion.md", "w").write("\n".join(out) + "\n"); print("\n".join(out))
