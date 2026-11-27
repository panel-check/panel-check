"""Diagnóstico temporal (27/11/2026): dónde aparece «dictamen» en la página del expediente. Solo texto alrededor."""
import re, sys
from validar_leads import BASE, crear_sesion, _get_con_reintentos
acta = sys.argv[1]
s = crear_sesion()
r = _get_con_reintentos(lambda: s.post(f"{BASE}/MarcasConsultas/Resultado",
                                       headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"}, data={"acta": acta}, timeout=30))
html = r.text
print(f"largo html {len(html)}")
for m in re.finditer(r"(?i)dict[aá]men|deneg", html):
    i = m.start()
    crudo = re.sub(r"\s+", " ", html[max(0, i - 600):i + 300])
    print("---- contexto:", crudo)
for sec in ("VISTAS Y NOTIFICACIONES", "UBICACI", "DICTAMEN", "RESOLUCION", "ESTADO"):
    for m in re.finditer(sec, html):
        print(f"== seccion {sec} en {m.start()}")
for m in re.finditer(r"(\w+)\s*=\s*JSON\.parse\('(\[.*?\])'\)", html, re.S):
    print(f"== JSON {m.group(1)}: {m.group(2)[:1500]}")
