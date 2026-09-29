"""Diagnostico descartable: entender como funciona la "busqueda puntual"
por NroActa/NroResolucion de la pagina de busqueda avanzada de INPI (mas
confiable que buscar por denominacion, que falla con diferencias de
espaciado como "BALI STONE") -- a que endpoint postea BtnBuscarPuntual."""
import re

from validar_leads import BASE, _get_con_reintentos, crear_sesion

s = crear_sesion()
r = s.get(f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE", timeout=30)
print(f"::notice::status={r.status_code} len={len(r.text)}")

# listar los <script src=...> de la pagina (buscamos el JS que wirea BtnBuscarPuntual)
srcs = re.findall(r'<script[^>]+src="([^"]+)"', r.text)
print(f"::notice::scripts: {srcs}")

# contexto alrededor de BtnBuscarPuntual en el HTML (el form que envuelve el boton)
# ojo: el snippet puede tener saltos de linea reales, que cortan la anotacion de
# GitHub Actions en varias lineas (solo la primera queda con ::notice::) -- los
# aplanamos a espacios antes de imprimir.
idx = r.text.find("BtnBuscarPuntual")
snippet = r.text[max(0, idx - 400):idx + 200]
snippet = re.sub(r"\s+", " ", snippet)
print(f"::notice::contexto BtnBuscarPuntual: ...{snippet}...")

# tambien buscamos, en los .js propios del sitio (no cdn), cualquier mencion a
# NroActa/NroResolucion/BuscarPuntual para ver a que endpoint postean
propios = [u for u in srcs if u.startswith("/") and "jquery" not in u and "bootstrap" not in u and "owl" not in u and "parallax" not in u and "back-to-top" not in u and "style-switcher" not in u]
print(f"::notice::scripts propios a revisar: {propios}")
for u in propios:
    try:
        rj = s.get(f"{BASE}{u}", timeout=20)
    except Exception as e:
        print(f"::notice::  {u}: error {e}")
        continue
    if "NroActa" in rj.text or "BuscarPuntual" in rj.text or "NroResolucion" in rj.text:
        i2 = rj.text.find("NroActa")
        if i2 < 0:
            i2 = rj.text.find("BuscarPuntual")
        if i2 < 0:
            i2 = rj.text.find("NroResolucion")
        ctx = re.sub(r"\s+", " ", rj.text[max(0, i2 - 300):i2 + 400])
        print(f"::notice::  {u} MATCH: ...{ctx}...")
