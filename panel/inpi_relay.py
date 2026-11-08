"""
Reenvío de los pedidos al INPI por el hosting de la web, en Argentina (wordpress/inpi-relay.php).

Desde el 06/11/2026 el portal del INPI no acepta conexiones desde servidores fuera de Argentina
(Railway y GitHub Actions: ConnectTimeout). Si están las variables

    INPI_RELAY_URL    = https://smartiesconsultora.com.ar/inpi-relay.php
    INPI_RELAY_CLAVE  = la clave del archivo PHP

todo pedido que haga `requests` (en cualquier parte del panel o de los scripts) a portaltramites.inpi.gob.ar
o ws.inpi.gob.ar sale por ese archivo: se le manda el pedido con la URL real en `b` (base64url) y la clave en la
cabecera X-Relay-Clave, y la respuesta vuelve como si fuera del INPI (mismo estado, tipo, cookies, URL).
Los demás pedidos (Resend, Apify, GitHub, la base) no se tocan. Sin las variables no hace nada.

Cómo se activa:
  - En el panel: `import inpi_relay` al principio de app.py.
  - En los procesos de GitHub: PYTHONPATH apunta a panel/relay_site, cuyo sitecustomize.py lo carga
    al arrancar Python, así no hay que tocar cada script.

Si el hosting no puede llegar al INPI (responde con X-Relay-Error) se levanta requests.ConnectionError,
igual que si fallara la conexión directa: los reintentos y avisos que ya existen siguen funcionando.
"""

import base64
import os
import sys
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

HOSTS_INPI = {"portaltramites.inpi.gob.ar", "ws.inpi.gob.ar"}
CABECERA_CLAVE = "X-Relay-Clave"
# Espera mínima de lectura para los pedidos que pasan por el reenvío. Con INPI saturado (07-08/11/2026) el
# expediente tardaba hasta 80 s; con 30 s se cortaban las corridas. El PHP espera hasta 90 s y Cloudflare
# corta a los 100 s, así que 85 s deja que llegue la respuesta del INPI o el error del propio reenvío.
LECTURA_MINIMA = 85


def _timeout_ampliado(timeout):
    """Devuelve el timeout con la lectura llevada a LECTURA_MINIMA (respeta uno mayor o sin límite)."""
    if timeout is None:
        return None
    if isinstance(timeout, tuple):
        conexion, lectura = timeout
        return (conexion, None if lectura is None else max(lectura, LECTURA_MINIMA))
    try:
        return (timeout, max(float(timeout), LECTURA_MINIMA))
    except (TypeError, ValueError):   # urllib3 Timeout u otro objeto: se deja como vino
        return timeout

_send_original = HTTPAdapter.send
_activo = False


def configuracion():
    """(url, clave) del reenvío, o (None, None) si no está configurado."""
    url = (os.environ.get("INPI_RELAY_URL") or "").strip()
    clave = (os.environ.get("INPI_RELAY_CLAVE") or "").strip()
    if not url or not clave:
        return None, None
    return url, clave


def es_de_inpi(url: str) -> bool:
    try:
        partes = urlsplit(url)
    except ValueError:
        return False
    return partes.scheme == "https" and (partes.hostname or "").lower() in HOSTS_INPI


def _send(self, request, **kwargs):
    url, clave = configuracion()
    if not url or not es_de_inpi(request.url) or CABECERA_CLAVE in request.headers:
        return _send_original(self, request, **kwargs)
    copia = request.copy()
    # base64url y no la URL a la vista: el firewall de los hostings compartidos (ModSecurity) bloquea
    # pedidos con «https://…» dentro de la dirección.
    copia.prepare_url(url, {"b": base64.urlsafe_b64encode(request.url.encode()).decode().rstrip("=")})
    copia.headers[CABECERA_CLAVE] = clave
    copia.headers.pop("Host", None)
    if "timeout" in kwargs:
        kwargs["timeout"] = _timeout_ampliado(kwargs["timeout"])
    respuesta = _send_original(self, copia, **kwargs)
    error = respuesta.headers.get("X-Relay-Error")
    if error or (respuesta.status_code == 404 and not respuesta.headers.get("Content-Type", "").startswith("text/html")):
        respuesta.close()
        motivo = error or "el reenvío respondió 404 (¿clave o URL del reenvío mal cargadas?)"
        raise requests.ConnectionError(f"Reenvío al INPI por el hosting: {motivo}", request=request)
    # Para el resto del código la respuesta es del INPI: cookies, redirecciones y URL quedan del INPI.
    respuesta.url = request.url
    respuesta.request = request
    return respuesta


def activar() -> bool:
    """Instala el reenvío (una sola vez). Devuelve True si quedó activo."""
    global _activo
    url, _ = configuracion()
    if not url:
        return False
    if not _activo:
        HTTPAdapter.send = _send
        _activo = True
        print(f"[inpi-relay] pedidos al INPI por {urlsplit(url).hostname}", file=sys.stderr, flush=True)
    return True


def desactivar() -> None:
    """Vuelve al comportamiento normal (lo usan las pruebas)."""
    global _activo
    HTTPAdapter.send = _send_original
    _activo = False


activar()
