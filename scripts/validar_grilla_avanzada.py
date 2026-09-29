"""
Validación puntual (uso único) de 3 cosas que propusimos "afanarle" a Argonut
antes de codear nada, para no asumir que funcionan solo porque están en su
código:

  1. ¿El endpoint JSON GrillaMarcasAvanzada realmente existe y devuelve
     Estado / Numero_Resolucion poblados? ¿Sirve el filtro por rango de
     fecha (Fecha_IngresoDesde/Hasta)?
  2. ¿Concurrencia real (httpx.AsyncClient, varias requests en paralelo)
     es más rápida que el patrón serial que usamos hoy, sin que el WAF
     la banee más rápido?
  3. cachetools.TTLCache — no hace falta pegarle al INPI para esto, es una
     librería madura y el patrón (cache por (clase, denominacion), TTL 24h)
     es correcto por inspección. Se deja una prueba mínima igual, local.

No escribe nada en la base. Solo imprime resultados con ::notice:: para
poder leerlos por la API de GitHub.
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
        "Tipo_Resolucion": "",
        "Clase": "",
        "TipoBusquedaDenominacion": "1",
        "Denominacion": "",
        "Titular": "",
        "TipoBusquedaTitular": "0",
        "Fecha_IngresoDesde": "",
        "Fecha_IngresoHasta": "",
        "Fecha_ResolucionDesde": "",
        "Fecha_ResolucionHasta": "",
        "vigentes": False,
        "limit": 20,
        "offset": 0,
    }
    base.update(overrides)
    return base


async def main():
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30.0) as client:
        # ── Sesión ──────────────────────────────────────────────────────
        t0 = time.monotonic()
        r = await client.get(SESION_URL)
        print(f"::notice::[sesion] status={r.status_code} tiempo={time.monotonic()-t0:.2f}s cookies={list(client.cookies.keys())}")

        # ── PRUEBA 1a: búsqueda por denominación conocida (clase 35) ─────
        r1 = await client.post(API_URL, json=payload(Clase="35", Denominacion="MERCADO", limit=10))
        print(f"::notice::[prueba1a denominacion=MERCADO clase=35] status={r1.status_code} content-type={r1.headers.get('content-type')}")
        try:
            data1 = r1.json()
            filas = data1.get("rows") or []
            print(f"::notice::[prueba1a] total={data1.get('total')} filas_devueltas={len(filas)}")
            if filas:
                muestra = filas[0]
                print(f"::notice::[prueba1a muestra fila 0] {json.dumps(muestra, ensure_ascii=False)[:500]}")
                con_estado = sum(1 for f in filas if str(f.get("Estado", "")).strip())
                con_resolucion = sum(1 for f in filas if str(f.get("Numero_Resolucion", "")).strip())
                print(f"::notice::[prueba1a] filas con Estado poblado: {con_estado}/{len(filas)} — con Numero_Resolucion: {con_resolucion}/{len(filas)}")
        except Exception as e:
            print(f"::notice::[prueba1a] NO es JSON válido o falló el parseo: {e} — primeros 300 chars: {r1.text[:300]!r}")

        # ── PRUEBA 1b: filtro por rango de fecha de ingreso, SIN denominación ──
        r2 = await client.post(API_URL, json=payload(Clase="35", Fecha_IngresoDesde="01/09/2026", Fecha_IngresoHasta="07/09/2026", limit=10))
        print(f"::notice::[prueba1b rango fecha ingreso 01-07/09/2026 clase=35] status={r2.status_code}")
        try:
            data2 = r2.json()
            filas2 = data2.get("rows") or []
            print(f"::notice::[prueba1b] total={data2.get('total')} filas_devueltas={len(filas2)}")
            if filas2:
                print(f"::notice::[prueba1b muestra fila 0] {json.dumps(filas2[0], ensure_ascii=False)[:500]}")
        except Exception as e:
            print(f"::notice::[prueba1b] NO es JSON válido: {e} — primeros 300 chars: {r2.text[:300]!r}")

        # ── PRUEBA 2: serial vs concurrente (5 consultas distintas) ──────
        clases_test = ["35", "36", "38", "41", "42"]

        t_serial = time.monotonic()
        resultados_serial = []
        for c in clases_test:
            rr = await client.post(API_URL, json=payload(Clase=c, Denominacion="LA", limit=5))
            resultados_serial.append(rr.status_code)
            await asyncio.sleep(1.5)  # mismo "freno de mano" que usamos hoy en kom
        dur_serial = time.monotonic() - t_serial
        print(f"::notice::[prueba2 serial] status={resultados_serial} tiempo={dur_serial:.2f}s")

        t_conc = time.monotonic()
        tareas = [client.post(API_URL, json=payload(Clase=c, Denominacion="LA", limit=5)) for c in clases_test]
        respuestas = await asyncio.gather(*tareas, return_exceptions=True)
        dur_conc = time.monotonic() - t_conc
        status_conc = [
            (r.status_code if not isinstance(r, Exception) else f"ERROR:{r}")
            for r in respuestas
        ]
        print(f"::notice::[prueba2 concurrente] status={status_conc} tiempo={dur_conc:.2f}s")

        # ── ¿Bloqueo/WAF tras la ráfaga concurrente? re-probar una consulta simple ──
        r3 = await client.post(API_URL, json=payload(Clase="35", Denominacion="MERCADO", limit=5))
        print(f"::notice::[prueba2 post-rafaga] status={r3.status_code} — {'OK, sigue respondiendo' if r3.status_code == 200 else 'posible bloqueo'}")


if __name__ == "__main__":
    asyncio.run(main())
