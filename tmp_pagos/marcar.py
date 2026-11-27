"""Temporal (27/11/2026): marca como pagas en 2026 las marcas de la planilla de vigilancia paga.
Logs públicos: solo se imprimen actas, nombres de marca y clases (datos públicos del INPI), nunca clientes."""
import os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))
import psycopg2, psycopg2.extras
import cartera

APLICAR = os.environ.get("APLICAR") == "1"
texto = open(os.path.join(os.path.dirname(__file__), "filas.tsv"), encoding="utf-8").read()
filas = cartera.parsear_pagos(texto)
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute("SELECT acta, denominacion, clase, tipo FROM cartera_marcas")
marcas = cur.fetchall()
r = cartera.emparejar_pagos(filas, marcas)
enc = {m["acta"]: m for m in r["encontradas"]}

def base(n):   # sin números sueltos al final («SL LAB BUENOS AIRES 1985» → «SL LAB BUENOS AIRES»)
    return re.sub(r"(\s+\d+)+$", "", cartera.normalizar_nombre(n)).strip()

sigue = []
for f in r["no_encontradas"]:
    cand = []
    if f.get("clase"):
        b = base(f["marca"])
        cand = [m for m in marcas if str(m.get("clase")) == str(f["clase"]) and b and
                (base(m["denominacion"]) == b or base(m["denominacion"]).startswith(b) or b.startswith(base(m["denominacion"]) or "§"))]
        if len(cand) > 1 and f.get("tipo"):
            cand = [m for m in cand if (m.get("tipo") or "")[:1].upper() == f["tipo"]] or cand
    elif f.get("marca"):   # sin clase: todas las clases con ese nombre
        b = base(f["marca"])
        cand = [m for m in marcas if b and base(m["denominacion"]) == b]
    if cand:
        for m in cand:
            enc.setdefault(m["acta"], {**m, "linea": f["linea"] + "  (parecido)"})
    else:
        sigue.append(f)

print(f"Encontradas: {len(enc)}")
for m in sorted(enc.values(), key=lambda m: (cartera.normalizar_nombre(m["denominacion"]), str(m["clase"]))):
    print(f"  {m['acta']}  {m['denominacion']}  cl.{m['clase']}  {m.get('tipo') or ''}   <- {m['linea']}")
print(f"\nNo encontradas: {len(sigue)}")
for f in sigue:
    b = base(f["marca"]).split(" ")[0] if f.get("marca") else ""
    pista = sorted({f"{m['denominacion']} cl.{m['clase']} ({m['acta']})" for m in marcas
                    if b and b in cartera.normalizar_nombre(m["denominacion"])})[:6]
    print(f"  {f['linea']}  — {f.get('error')}" + (f"   · en la cartera hay: {'; '.join(pista)}" if pista else ""))
if APLICAR:
    n = cartera.marcar_pagos(cur, list(enc), 2026, True, "planilla 2026")
    conn.commit()
    cur.execute("SELECT COUNT(*) AS n FROM cartera_vigilancia_pagos WHERE anio = 2026")
    print(f"\nAplicado: {n} nuevas; total pagas 2026 = {cur.fetchone()['n']}")
