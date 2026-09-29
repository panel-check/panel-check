"""Diagnostico descartable: inspecciona el HTML/JS de la pagina de
busqueda avanzada de INPI para ver si existe un campo de busqueda por
NUMERO de registro/acta (mas confiable que por denominacion, que falla
con diferencias de espaciado como "BALI STONE")."""
import re
from validar_leads import crear_sesion, BASE, _get_con_reintentos

s = crear_sesion()
r = s.get(f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE", timeout=30)
print(f"::notice::status={r.status_code} len={len(r.text)}")

idx = r.text.find("NroActa")
print(f"::notice::contexto NroActa: ...{r.text[max(0,idx-300):idx+300]}...")

# listar TODOS los inputs/selects de la pagina con su name/id
for m in re.finditer(r'<(input|select)\b[^>]*>', r.text):
    tag = m.group(0)
    nm = re.search(r'name="([^"]*)"', tag)
    ident = re.search(r'\bid="([^"]*)"', tag)
    if nm or ident:
        print(f"::notice::campo: name={nm.group(1) if nm else ''!r} id={ident.group(1) if ident else ''!r}")

# probar la busqueda real por NroActa=3136523 (numero de registro de BALI STONE)
payload = {
    "Tipo_Resolucion": "", "Clase": "", "TipoBusquedaDenominacion": "1",
    "Denominacion": "", "Titular": "", "TipoBusquedaTitular": "0",
    "Fecha_IngresoDesde": "", "Fecha_IngresoHasta": "",
    "Fecha_ResolucionDesde": "", "Fecha_ResolucionHasta": "",
    "vigentes": False, "limit": 20, "offset": 0,
    "NroActa": "3136523",
}
try:
    rr = _get_con_reintentos(
        lambda: s.post(
            f"{BASE}/MarcasConsultas/GrillaMarcasAvanzada",
            json=payload,
            headers={
                "Referer": f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=30,
        )
    )
    data = rr.json()
    print(f"::notice::busqueda NroActa=3136523 -> total={data.get('total')} rows={data.get('rows')}")
except Exception as e:
    print(f"::notice::error en busqueda NroActa: {e}")
