"""Diagnostico descartable: leer Busqueda.js completo para ver a que
endpoint postea BtnBuscarPuntual (busqueda por NroActa/NroResolucion) --
mas confiable que buscar por denominacion (falla con espaciado distinto,
ej. "BALI STONE")."""
import re

from validar_leads import BASE, crear_sesion

s = crear_sesion()
s.get(f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE", timeout=30)

r = s.get(f"{BASE}/Scripts/MarcasConsultas/Busqueda.js", timeout=30)
print(f"::notice::status={r.status_code} len={len(r.text)}")

texto = r.text
for palabra in ["Puntual", "NroActa", "NroResolucion", "ajax", "url:"]:
    n = texto.count(palabra)
    print(f"::notice::'{palabra}' aparece {n} veces")

# contexto alrededor de cada aparicion de "Puntual" (suele ser poco: el
# handler del boton + el ajax que dispara)
for m in re.finditer("Puntual", texto):
    i = m.start()
    ctx = re.sub(r"\s+", " ", texto[max(0, i - 200):i + 400])
    print(f"::notice::ctx Puntual@{i}: ...{ctx}...")
