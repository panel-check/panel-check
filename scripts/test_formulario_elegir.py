"""Link único /formulario: muestra las dos opciones (persona física y jurídica) sin pedir sesión.

    cd scripts && python3 -m unittest test_formulario_elegir
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import formularios_api
except Exception:  # pragma: no cover
    TestClient = None

import seguridad


@unittest.skipIf(TestClient is None, "falta fastapi / httpx")
class LinkUnico(unittest.TestCase):
    def setUp(self):
        def sin_sesion():
            raise AssertionError("no debería pedir sesión")
        app = FastAPI()
        app.include_router(formularios_api.crear_router(sin_sesion, None))
        self.c = TestClient(app)

    def test_muestra_las_dos_opciones(self):
        for ruta in ("/formulario", "/formulario/"):
            r = self.c.get(ruta, follow_redirects=False)
            self.assertEqual(r.status_code, 200, ruta)
            self.assertIn('href="/formulario/persona-fisica"', r.text)
            self.assertIn('href="/formulario/persona-juridica"', r.text)
            self.assertEqual(r.headers.get("x-robots-tag"), "noindex")

    def test_los_formularios_siguen_andando(self):
        self.assertEqual(self.c.get("/formulario/persona-fisica").status_code, 200)
        self.assertEqual(self.c.get("/formulario/no-existe").status_code, 404)


class PaginaPublica(unittest.TestCase):
    def test_html_publico(self):
        self.assertFalse(seguridad.es_html_privado("/static/formulario_elegir.html"))
