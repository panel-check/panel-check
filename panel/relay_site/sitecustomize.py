"""
Python carga este archivo solo al arrancar cuando PYTHONPATH incluye esta carpeta (lo hacen los
workflows de GitHub). Activa el reenvío de los pedidos al INPI por el hosting en Argentina
(ver panel/inpi_relay.py) sin tener que tocar cada script. Sin INPI_RELAY_URL no hace nada,
y si algo falla (por ejemplo, durante `pip install`, cuando requests todavía no está) lo ignora.
"""

import os
import sys

if (os.environ.get("INPI_RELAY_URL") or "").strip() and (os.environ.get("INPI_RELAY_CLAVE") or "").strip():
    try:
        import importlib.util

        _ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "inpi_relay.py")
        _spec = importlib.util.spec_from_file_location("inpi_relay", _ruta)
        _modulo = importlib.util.module_from_spec(_spec)
        sys.modules["inpi_relay"] = _modulo
        _spec.loader.exec_module(_modulo)
    except Exception:  # noqa: BLE001 — nunca impedir que Python arranque
        sys.modules.pop("inpi_relay", None)
