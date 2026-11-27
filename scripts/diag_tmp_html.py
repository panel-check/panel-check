"""Diagnóstico temporal (27/11/2026): secciones de ubicación/estado del expediente y pedidos AJAX que usa."""
import re, sys
from validar_leads import BASE, crear_sesion, _get_con_reintentos
acta = sys.argv[1]
s = crear_sesion()
r = _get_con_reintentos(lambda: s.post(f"{BASE}/MarcasConsultas/Resultado",
                                       headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"}, data={"acta": acta}, timeout=30))
html = r.text
def limpio(x):
    x = re.sub(r"<script.*?</script>", " ", x, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x))
for pat in ("UBICACI", "ESTADO"):
    for m in re.finditer(pat, html):
        print(f"== {pat} @{m.start()}: {limpio(html[m.start()-200:m.start()+1500])[:900]}")
for m in re.finditer(r"url\s*:\s*['\"]([^'\"]+)['\"]", html):
    print("== ajax url:", m.group(1))
for m in re.finditer(r"\$\.(?:post|get|ajax)\(\s*['\"]([^'\"]+)", html):
    print("== ajax:", m.group(1))
for m in re.finditer(r"data-url=['\"]([^'\"]+)", html):
    print("== data-url:", m.group(1))
