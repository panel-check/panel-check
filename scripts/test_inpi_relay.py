"""
Pruebas del reenvío de los pedidos al INPI (panel/inpi_relay.py) contra un reenvío falso local que se
porta como wordpress/inpi-relay.php: devuelve estado, tipo, cookies y redirecciones del «INPI».

    cd scripts && python3 -m unittest test_inpi_relay
"""

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))

import requests  # noqa: E402

PEDIDOS = []


class RelayFalso(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _responder(self):
        partes = urlsplit(self.path)
        if partes.path != "/inpi-relay.php":   # un sitio cualquiera (no INPI): responde directo
            PEDIDOS.append({"directo": self.path, "clave": self.headers.get("X-Relay-Clave")})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"directo")
            return
        if self.headers.get("X-Relay-Clave") != "secreto":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not found")
            return
        destino = parse_qs(partes.query)["u"][0]
        largo = int(self.headers.get("Content-Length") or 0)
        cuerpo = self.rfile.read(largo).decode() if largo else ""
        PEDIDOS.append({"u": destino, "metodo": self.command, "cookie": self.headers.get("Cookie"),
                        "cuerpo": cuerpo, "tipo": self.headers.get("Content-Type")})
        ruta = urlsplit(destino).path
        if ruta == "/caido":
            self.send_response(502)
            self.send_header("X-Relay-Error", "El INPI no respondio desde el hosting: timeout")
            self.end_headers()
            return
        if ruta == "/viejo":
            self.send_response(302)
            self.send_header("Location", "https://portaltramites.inpi.gob.ar/nuevo")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if ruta == "/login":
            self.send_header("Set-Cookie", "ASP.NET_SessionId=abc123; path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(json.dumps({"ruta": ruta}).encode())

    do_GET = do_POST = _responder


class Reenvio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servidor = HTTPServer(("127.0.0.1", 0), RelayFalso)
        cls.base = f"http://127.0.0.1:{cls.servidor.server_port}"
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()
        cls.env = {k: os.environ.get(k) for k in ("INPI_RELAY_URL", "INPI_RELAY_CLAVE", "NO_PROXY", "no_proxy")}
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
        os.environ["INPI_RELAY_URL"] = f"{cls.base}/inpi-relay.php"
        os.environ["INPI_RELAY_CLAVE"] = "secreto"
        import inpi_relay
        cls.relay = inpi_relay
        inpi_relay.activar()

    @classmethod
    def tearDownClass(cls):
        cls.relay.desactivar()
        cls.servidor.shutdown()
        for k, v in cls.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        PEDIDOS.clear()

    def test_get_post_y_cookies_de_sesion(self):
        s = requests.Session()
        r = s.get("https://portaltramites.inpi.gob.ar/login", timeout=5)
        self.assertEqual((r.status_code, r.json(), r.url), (200, {"ruta": "/login"}, "https://portaltramites.inpi.gob.ar/login"))
        self.assertEqual(s.cookies.get("ASP.NET_SessionId", domain="portaltramites.inpi.gob.ar"), "abc123")
        r = s.post("https://portaltramites.inpi.gob.ar/MarcasConsultas/GrillaMarcasAvanzada", json={"Denominacion": "APEX"}, timeout=5)
        self.assertEqual(r.json(), {"ruta": "/MarcasConsultas/GrillaMarcasAvanzada"})
        segundo = PEDIDOS[-1]
        self.assertEqual((segundo["metodo"], segundo["cookie"], json.loads(segundo["cuerpo"])),
                         ("POST", "ASP.NET_SessionId=abc123", {"Denominacion": "APEX"}))
        self.assertEqual(segundo["tipo"], "application/json")

    def test_redireccion_del_inpi_se_sigue_por_el_reenvio(self):
        r = requests.get("https://portaltramites.inpi.gob.ar/viejo", timeout=5)
        self.assertEqual(r.json(), {"ruta": "/nuevo"})
        self.assertEqual([p["u"] for p in PEDIDOS],
                         ["https://portaltramites.inpi.gob.ar/viejo", "https://portaltramites.inpi.gob.ar/nuevo"])

    def test_webservice_tambien_y_query_string_intacto(self):
        requests.post("https://ws.inpi.gob.ar/wsinpi.asmx?op=X&y=1", data=b"<soap/>", timeout=5,
                      headers={"Content-Type": "text/xml; charset=utf-8"})
        self.assertEqual(PEDIDOS[-1]["u"], "https://ws.inpi.gob.ar/wsinpi.asmx?op=X&y=1")
        self.assertEqual(PEDIDOS[-1]["cuerpo"], "<soap/>")

    def test_si_el_hosting_no_llega_al_inpi_es_error_de_conexion(self):
        with self.assertRaisesRegex(requests.ConnectionError, "no respondio desde el hosting"):
            requests.get("https://portaltramites.inpi.gob.ar/caido", timeout=5)

    def test_clave_mal_cargada_es_error_de_conexion(self):
        os.environ["INPI_RELAY_CLAVE"] = "otra"
        try:
            with self.assertRaisesRegex(requests.ConnectionError, "clave o URL"):
                requests.get("https://portaltramites.inpi.gob.ar/login", timeout=5)
        finally:
            os.environ["INPI_RELAY_CLAVE"] = "secreto"

    def test_lo_que_no_es_del_inpi_no_pasa_por_el_reenvio(self):
        r = requests.get(f"{self.base}/otra-cosa", timeout=5)
        self.assertEqual(r.text, "directo")
        self.assertEqual(PEDIDOS, [{"directo": "/otra-cosa", "clave": None}])
        self.assertFalse(self.relay.es_de_inpi("http://portaltramites.inpi.gob.ar/x"))   # solo https
        self.assertFalse(self.relay.es_de_inpi("https://inpi.gob.ar.evil.com/"))


class SinConfigurar(unittest.TestCase):
    def test_sin_variables_no_se_activa(self):
        import inpi_relay
        env = {k: os.environ.pop(k, None) for k in ("INPI_RELAY_URL", "INPI_RELAY_CLAVE")}
        try:
            self.assertEqual(inpi_relay.configuracion(), (None, None))
            self.assertFalse(inpi_relay.activar())
        finally:
            for k, v in env.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
