"""Diagnostico descartable: probar si GrillaMarcasAvanzada acepta buscar
directo por NroActa/NroResolucion (campos que existen en el form de
busqueda avanzada) en vez de por Denominacion -- mas confiable para casos
como "BALI STONE" (Nro. 3.136.523), que no aparece por nombre."""
from validar_leads import BASE, _get_con_reintentos, crear_sesion

s = crear_sesion()
s.get(f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE", timeout=30)

base_payload = {
    "Tipo_Resolucion": "", "Clase": "", "TipoBusquedaDenominacion": "1",
    "Denominacion": "", "Titular": "", "TipoBusquedaTitular": "0",
    "Fecha_IngresoDesde": "", "Fecha_IngresoHasta": "",
    "Fecha_ResolucionDesde": "", "Fecha_ResolucionHasta": "",
    "vigentes": False, "limit": 20, "offset": 0,
}

for campo, valor in [("NroActa", "3136523"), ("NroResolucion", "3136523")]:
    payload = dict(base_payload)
    payload[campo] = valor
    try:
        r = _get_con_reintentos(
            lambda p=payload: s.post(
                f"{BASE}/MarcasConsultas/GrillaMarcasAvanzada",
                json=p,
                headers={
                    "Referer": f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=30,
            )
        )
        data = r.json()
        print(f"::notice::{campo}='{valor}' -> status={r.status_code} total={data.get('total')} rows={data.get('rows')}")
    except Exception as e:
        print(f"::notice::{campo}='{valor}' -> error {e}")

# Tambien probar sin puntos (por si el campo espera el numero formateado
# como se muestra, "3.136.523")
for campo, valor in [("NroActa", "3.136.523"), ("NroResolucion", "3.136.523")]:
    payload = dict(base_payload)
    payload[campo] = valor
    try:
        r = _get_con_reintentos(
            lambda p=payload: s.post(
                f"{BASE}/MarcasConsultas/GrillaMarcasAvanzada",
                json=p,
                headers={
                    "Referer": f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=30,
            )
        )
        data = r.json()
        print(f"::notice::{campo}='{valor}' -> status={r.status_code} total={data.get('total')} rows={data.get('rows')}")
    except Exception as e:
        print(f"::notice::{campo}='{valor}' -> error {e}")
