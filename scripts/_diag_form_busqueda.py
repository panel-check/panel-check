"""Diagnostico descartable: inspecciona el HTML/JS de la pagina de
busqueda avanzada de INPI para ver si existe un campo de busqueda por
NUMERO de registro/acta (mas confiable que por denominacion, que falla
con diferencias de espaciado como "BALI STONE")."""
import re
from validar_leads import crear_sesion, BASE

s = crear_sesion()
r = s.get(f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE", timeout=30)
print(f"::notice::status={r.status_code} len={len(r.text)}")

# Buscar inputs/selects con "numero" en el name/id (case-insensitive)
for m in re.finditer(r'<(?:input|select)[^>]*(?:name|id)="([^"]*[Nn]umero[^"]*)"[^>]*>', r.text):
    print(f"::notice::campo numero: {m.group(0)[:200]}")

# Buscar tambien cualquier mencion a "Numero_Resolucion" o "NumeroActa" en el JS embebido
for palabra in ["Numero_Resolucion", "NumeroActa", "Numero_Acta", "NroActa", "Nro_Registro", "Numero_Registro"]:
    if palabra in r.text:
        idx = r.text.index(palabra)
        print(f"::notice::encontrado {palabra!r} en contexto: ...{r.text[max(0,idx-80):idx+80]}...")
