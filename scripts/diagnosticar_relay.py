"""
Diagnóstico del reenvío al INPI (panel/inpi_relay.py + wordpress/inpi-relay.php): revisa unas actas con
el mismo código del escaneo y anota cada pedido HTTP (destino, estado, tamaño, cuánto tardó hasta la
primera respuesta y hasta el final, cabeceras de la respuesta) y cada pausa del script (time.sleep).
No escribe en la base. No imprime datos de personas: solo rutas, estados, tiempos y tamaños.

    python3 diagnosticar_relay.py 4822137 4822138
"""

import sys
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

T0 = time.time()


def t():
    return f"{time.time() - T0:7.2f}s"


# ── Pausas del script ──────────────────────────────────────────────────────
_sleep = time.sleep


def sleep_registrado(seg):
    if seg and seg >= 0.5:
        print(f"{t()}  PAUSA {seg:.1f}s", flush=True)
    _sleep(seg)


time.sleep = sleep_registrado

# ── Pedidos HTTP (se envuelve el envío ya interceptado por inpi_relay) ─────
_send = HTTPAdapter.send


def send_registrado(self, request, **kwargs):
    inicio = time.time()
    destino = urlsplit(request.url)
    nombre = f"{request.method} {destino.hostname}{destino.path}"
    try:
        r = _send(self, request, **kwargs)
    except Exception as e:
        print(f"{t()}  {nombre}  ERROR {type(e).__name__} tras {time.time() - inicio:.2f}s: {str(e)[:160]}", flush=True)
        raise
    primera = time.time() - inicio
    _ = r.content   # leer el cuerpo entero para medir cuánto tarda en terminar
    total = time.time() - inicio
    h = r.headers
    print(f"{t()}  {nombre}  {r.status_code}  {len(r.content)}B  primera={primera:.2f}s total={total:.2f}s  "
          f"tipo={h.get('Content-Type', '')[:30]} largo={h.get('Content-Length')} chunked={h.get('Transfer-Encoding')} "
          f"conexion={h.get('Connection')} server={h.get('Server')}", flush=True)
    if r.status_code != 200:   # solo errores (los logs son públicos: nada de datos de personas)
        print(f"           cuerpo: {r.text[:150]!r}", flush=True)
    return r


import inpi_relay  # noqa: E402  (ya activado por sitecustomize si están los secrets)

HTTPAdapter.send = send_registrado

sys.path.insert(0, ".")
from validar_leads import crear_sesion, existe_expediente, revisar_acta  # noqa: E402

print(f"reenvío activo: {inpi_relay._activo}  url: {urlsplit(inpi_relay.configuracion()[0] or '').hostname}", flush=True)

actas = sys.argv[1:] or ["4822137", "4822138"]
s = crear_sesion()
for acta in actas:
    print(f"\n{t()}  ===== acta {acta} =====", flush=True)
    a = time.time()
    existe, texto = existe_expediente(s, acta)
    print(f"{t()}  existe_expediente -> {existe} ({time.time() - a:.1f}s)", flush=True)
    if existe:
        b = time.time()
        info = revisar_acta(s, acta, html_previo=texto)
        print(f"{t()}  revisar_acta -> es_lead={info.get('es_lead')} ({time.time() - b:.1f}s)", flush=True)
    print(f"{t()}  acta {acta}: {time.time() - a:.1f}s en total", flush=True)
