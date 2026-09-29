"""Validación puntual: ¿se puede encontrar el acta de una marca a partir de
su denominación + número de registro, usando la misma API JSON
GrillaMarcasAvanzada que ya usa validar_leads (Argonut la usa con
Clase fija; probamos si funciona con Clase vacía para buscar en todas las
clases, ya que no siempre sabemos la clase del oponente).

Caso real: "BALI STONE" Nro. 3.136.523 (acta 4764327 cita esta marca en su
FUNDAMENTO de oposición) — clase 19 según el mismo texto, pero probamos sin
clase para validar el caso general.
"""
import json
import sys

from validar_leads import crear_sesion, BASE, _get_con_reintentos


def buscar(denominacion, clase=""):
    s = crear_sesion()
    payload = {
        "Tipo_Resolucion": "",
        "Clase": str(clase),
        "TipoBusquedaDenominacion": "1",
        "Denominacion": denominacion,
        "Titular": "",
        "TipoBusquedaTitular": "0",
        "Fecha_IngresoDesde": "",
        "Fecha_IngresoHasta": "",
        "Fecha_ResolucionDesde": "",
        "Fecha_ResolucionHasta": "",
        "vigentes": False,
        "limit": 100,
        "offset": 0,
    }
    r = _get_con_reintentos(
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
    print(f"::notice::status={r.status_code} content-type={r.headers.get('Content-Type')}")
    try:
        data = r.json()
    except Exception as e:
        print(f"::error::no es JSON: {e} — primeros 300: {r.text[:300]}")
        return
    total = data.get("total")
    rows = data.get("rows") or []
    print(f"::notice::total={total} filas devueltas={len(rows)}")
    for row in rows[:15]:
        print(f"::notice::  Acta={row.get('Acta')} Denominacion={row.get('Denominacion')} Clase={row.get('Clase')} "
              f"Numero_Resolucion={row.get('Numero_Resolucion')} Estado={row.get('Estado')}")


if __name__ == "__main__":
    buscar(sys.argv[1] if len(sys.argv) > 1 else "BALI STONE", sys.argv[2] if len(sys.argv) > 2 else "")
