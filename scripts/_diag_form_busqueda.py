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
idx = r.text.find("BtnBuscarPuntual")
print(f"::notice::contexto BtnBuscarPuntual: ...{r.text[max(0, idx - 400):idx + 200]}...")
