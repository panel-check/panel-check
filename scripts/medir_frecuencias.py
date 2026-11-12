"""Solo lectura: frecuencia de palabras por clase en las solicitudes de los últimos 18 meses (para calibrar la vigilancia)."""
import os, re, sys, collections
import psycopg2
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import similitud as sim, cartera
conn = psycopg2.connect(os.environ.get("DATABASE_URL") or os.environ["DATABASE_PUBLIC_URL"]); conn.set_session(readonly=True)
cur = conn.cursor()
cur.execute("""SELECT COALESCE(NULLIF(trim(denominacion_inpi),''),NULLIF(trim(denominacion),'')), clase FROM marcas
               WHERE COALESCE(fecha_presentacion::date, creado_en::date) >= now()::date - 548
               UNION ALL SELECT denominacion, clase FROM solicitudes_escaneadas s
               WHERE NOT EXISTS (SELECT 1 FROM marcas m WHERE m.acta=s.acta) AND COALESCE(s.fecha_presentacion, s.creado_en::date) >= now()::date - 548""")
g = frozenset(cartera.PALABRAS_GENERICAS_DEFAULT)
porclase, tot, glob = collections.defaultdict(collections.Counter), collections.Counter(), collections.Counter()
for nombre, clase in cur.fetchall():
    if not nombre: continue
    tot[clase] += 1
    for t in set(sim.tokens(nombre, g)):
        porclase[clase][t] += 1; glob[t] += 1
out = [f"Total solicitudes: {sum(tot.values())}", "Tamaño por clase (top 12): " + ", ".join(f"{c}:{n}" for c, n in tot.most_common(12))]
pruebas = [("TURISMO",39),("VIAJES",39),("TRIP",39),("TODO",35),("LLAVES",35),("NUBE",41),("RECORDS",41),("LABO",3),("ECOBRASA",4),("APEX",5),
           ("OLIVIA",43),("CAFE",43),("POLIPLAST",35),("PUNTO",41),("MEDIO",41),("MAS",36),("SOCIA",42),("JACANA",41),("LONDON",41),("SCHOOL",41),("ENGLISH",41),("LODGE",41),("FLUX",35),("BOIS",35)]
for t, c in pruebas:
    out.append(f"{t} clase {c}: {porclase[c][t]} de {tot[c]} ({round(100*porclase[c][t]/max(tot[c],1),2)}%) · global {glob[t]}")
for c in (35, 39, 41, 43, 25, 9):
    out.append(f"Clase {c}, top 25: " + ", ".join(f"{t}:{n}" for t, n in porclase[c].most_common(25)))
out.append("Global top 40: " + ", ".join(f"{t}:{n}" for t, n in glob.most_common(40)))
# Distribución: cuántas palabras superan distintos cortes en su clase
for corte in (5, 10, 15, 25, 50):
    out.append(f"Palabras con >= {corte} apariciones en alguna clase: {sum(1 for c in porclase for t, n in porclase[c].items() if n >= corte)}")
open("/tmp/medicion.md", "w").write("\n".join(out) + "\n"); print("\n".join(out))
