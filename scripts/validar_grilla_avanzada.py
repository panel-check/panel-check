"""
Segunda pasada de la validación puntual: solo el chequeo de WAF después de
una ráfaga concurrente más agresiva (10 requests en paralelo), y qué campos
trae el JSON para saber si alcanza para reemplazar el scraping de boletín
(necesitamos matrícula de agente/apoderado para saber si es "lead").
"""
import asyncio
import json
import time

import httpx

BASE = "https://portaltramites.inpi.gob.ar"
SESION_URL = f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE"
API_URL = f"{BASE}/MarcasConsultas/GrillaMarcasAvanzada"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Referer": SESION_URL,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


def payload(**overrides):
    base = {
        "Tipo_Resolucion": "", "Clase": "", "TipoBusquedaDenominacion": "1",
        "Denominacion": "", "Titular": "", "TipoBusquedaTitular": "0",
        "Fecha_IngresoDesde": "", "Fecha_IngresoHasta": "",
        "Fecha_ResolucionDesde": "", "Fecha_ResolucionHasta": "",
        "vigentes": False, "limit": 5, "offset": 0,
    }
    base.update(overrides)
    return base


async def main():
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30.0) as client:
        await client.get(SESION_URL)

        # Ráfaga más agresiva: 10 en paralelo, distintas clases
        clases = [str(c) for c in range(30, 40)]
        t0 = time.monotonic()
        tareas = [client.post(API_URL, json=payload(Clase=c, Denominacion="LA")) for c in clases]
        respuestas = await asyncio.gather(*tareas, return_exceptions=True)
        dur = time.monotonic() - t0
        status = [(r.status_code if not isinstance(r, Exception) else f"ERR:{r}") for r in respuestas]
        print(f"::notice::[rafaga 10 concurrentes] status={status} tiempo={dur:.2f}s")

        # ¿Sigue respondiendo bien justo después, sin esperar?
        r_after = await client.post(API_URL, json=payload(Clase="35", Denominacion="MERCADO"))
        print(f"::notice::[post-rafaga inmediato] status={r_after.status_code} body_ok={'rows' in (r_after.json() if r_after.status_code==200 else {})}")

        # Campos completos de una fila real, para ver si hay algo de
        # agente/apoderado/matrícula (necesario para saber si es "lead")
        r_campos = await client.post(API_URL, json=payload(Clase="35", Denominacion="MERCADO", limit=1))
        fila = (r_campos.json().get("rows") or [{}])[0]
        print(f"::notice::[campos disponibles] {sorted(fila.keys())}")
        print(f"::notice::[fila completa] {json.dumps(fila, ensure_ascii=False)}")


if __name__ == "__main__":
    asyncio.run(main())
