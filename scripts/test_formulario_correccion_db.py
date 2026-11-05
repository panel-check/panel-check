"""Prueba de punta a punta del link para corregir un formulario, contra una base Postgres de prueba
(vacía y descartable: crea sus tablas). Usa datos inventados.

    TEST_DATABASE_URL=postgresql://... python3 -m unittest test_formulario_correccion_db

Sin TEST_DATABASE_URL se saltea. La corre el workflow «Probar formularios con base»."""
import io
import os
import sys
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

DSN = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(DSN, "falta TEST_DATABASE_URL")
class CorreccionDePuntaAPunta(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import cartera
        import formularios_api
        import formularios_core as fc
        import mails_core

        cls.psycopg2, cls.fc = psycopg2, fc

        @contextmanager
        def conexion():
            conn = psycopg2.connect(DSN)
            try:
                yield conn
            finally:
                conn.close()

        cls.conexion = conexion
        with conexion() as conn:
            with conn.cursor() as cur:
                cartera.crear_tablas(cur)
                fc.crear_tablas(cur)
                mails_core.crear_tablas(cur)
                cur.execute("CREATE TABLE IF NOT EXISTS vigilancia_config (clave TEXT PRIMARY KEY, valor TEXT)")
            conn.commit()

        cls.mails = []
        cls.parches = [
            mock.patch.object(formularios_api.mails_core, "preparar", return_value={"api_key": None}),
            mock.patch.object(formularios_api.mails_cola, "enviar_o_encolar",
                              side_effect=lambda conexion, **kw: cls.mails.append(kw) or {"en_cola": False, "id": "x"}),
            mock.patch.dict(os.environ, {"TURNSTILE_SITE_KEY": "", "TURNSTILE_SECRET_KEY": ""}),
        ]
        for p in cls.parches:
            p.start()
        app = FastAPI()
        app.include_router(formularios_api.crear_router(lambda: "tester", conexion))
        cls.cli = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        for p in cls.parches:
            p.stop()

    def _uno(self, sql, val=()):
        with self.conexion() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, val)
                return cur.fetchone()

    @staticmethod
    def _png():
        from PIL import Image
        b = io.BytesIO()
        Image.new("RGB", (8, 8), (10, 120, 200)).save(b, "PNG")
        return b.getvalue()

    def test_circuito_completo(self):
        fc = self.fc
        base = {"email": "prueba@ejemplo.com", "nombre": "Prueba Persona", "nacionalidad": "Argentina", "dni": "30111222",
                "cuit": "20301112229", "domicilio": "Calle Falsa 123", "localidad": "Ramos Mejía", "provincia": "Buenos Aires",
                "codigo_postal": "1704", "estado_civil": "Soltero/a", "servicio": fc.SERVICIO_OPOSICION,
                "marca_nombre": "MARCA DE PRUEBA", "tiene_logo": "SI"}
        r = self.cli.post("/api/publico/formulario/persona-fisica", data=base,
                          files={"logo": ("logo.png", self._png(), "image/png")})
        self.assertEqual(r.status_code, 200, r.text)
        rid, cliente_id = self._uno("SELECT id, cliente_id FROM formularios_respuestas ORDER BY id DESC LIMIT 1")
        self.assertEqual(len(self.mails), 1)  # el poder

        # el panel pide el link; pedirlo de nuevo devuelve el mismo
        l1 = self.cli.post(f"/api/formularios/{rid}/link-correccion", json={}).json()
        l2 = self.cli.post(f"/api/formularios/{rid}/link-correccion", json={}).json()
        self.assertEqual(l1["ruta"], l2["ruta"])
        token = l1["ruta"].split("corregir=")[1]
        ficha = self.cli.get(f"/api/formularios/{rid}").json()
        self.assertEqual(ficha["link_correccion"]["ruta"], l1["ruta"])
        self.assertNotIn("edicion_token", ficha)

        # el cliente abre el link: viene con lo que mandó
        v = self.cli.get(f"/api/publico/formulario-correccion/{token}").json()
        self.assertEqual(v["slug"], "persona-fisica")
        self.assertEqual(v["datos"]["domicilio"], "Calle Falsa 123")
        self.assertEqual(v["archivos"], {"logo": "logo.png"})

        # corrige el domicilio y suma la web, sin volver a subir el logo
        nuevo = {**base, "domicilio": "Avenida Siempre Viva 742", "marca_web_redes": "@marcadeprueba"}
        g = self.cli.post(f"/api/publico/formulario-correccion/{token}", data=nuevo)
        self.assertEqual(g.status_code, 200, g.text)
        self.assertEqual(g.json()["cambios"], 2)
        self.assertTrue(g.json()["poder_enviado"])  # cambió el domicilio: sale el poder de nuevo
        self.assertEqual(len(self.mails), 2)

        datos, estado, ediciones, tok, aviso = self._uno(
            "SELECT datos, estado, ediciones, edicion_token, aviso_enviado_en FROM formularios_respuestas WHERE id = %s", (rid,))
        self.assertEqual((datos["domicilio"], datos["marca_web_redes"]), ("Avenida Siempre Viva 742", "@marcadeprueba"))
        self.assertEqual((estado, ediciones, tok, aviso), ("nueva", 1, None, None))
        self.assertEqual(self._uno("SELECT domicilio FROM clientes WHERE id = %s", (cliente_id,))[0], "Avenida Siempre Viva 742")
        self.assertEqual(self._uno("SELECT COUNT(*) FROM formularios_archivos WHERE respuesta_id = %s AND campo = 'logo'", (rid,))[0], 1)
        cambios = self._uno("SELECT cambios FROM formularios_ediciones WHERE respuesta_id = %s", (rid,))[0]
        self.assertEqual([c["etiqueta"] for c in cambios], ["Domicilio particular", "Web o Redes"])
        ficha = self.cli.get(f"/api/formularios/{rid}").json()
        self.assertIsNone(ficha["link_correccion"])
        self.assertEqual(len(ficha["correcciones"]), 1)

        # el link sirve una sola vez
        self.assertEqual(self.cli.get(f"/api/publico/formulario-correccion/{token}").status_code, 404)
        self.assertEqual(self.cli.post(f"/api/publico/formulario-correccion/{token}", data=nuevo).status_code, 404)

        # link nuevo: cambia a Vistas (sin logo) → el logo se borra; solo cambia la marca → no se reenvía el poder
        token2 = self.cli.post(f"/api/formularios/{rid}/link-correccion", json={}).json()["ruta"].split("corregir=")[1]
        self.assertNotEqual(token, token2)
        vistas = {k: v for k, v in nuevo.items() if k != "tiene_logo"}
        vistas["servicio"] = fc.SERVICIO_VISTAS
        g = self.cli.post(f"/api/publico/formulario-correccion/{token2}", data=vistas)
        self.assertEqual(g.status_code, 200, g.text)
        self.assertIsNone(g.json()["poder_enviado"])
        self.assertEqual(len(self.mails), 2)
        self.assertEqual(self._uno("SELECT COUNT(*) FROM formularios_archivos WHERE respuesta_id = %s", (rid,))[0], 0)

        # renovar reemplaza el link y anular lo corta
        a = self.cli.post(f"/api/formularios/{rid}/link-correccion", json={}).json()["ruta"]
        b = self.cli.post(f"/api/formularios/{rid}/link-correccion", json={"renovar": True}).json()["ruta"]
        self.assertNotEqual(a, b)
        self.assertEqual(self.cli.get(f"/api/publico/formulario-correccion/{a.split('corregir=')[1]}").status_code, 404)
        self.cli.delete(f"/api/formularios/{rid}/link-correccion")
        self.assertEqual(self.cli.get(f"/api/publico/formulario-correccion/{b.split('corregir=')[1]}").status_code, 404)

    def test_error_de_validacion_no_gasta_el_link(self):
        fc = self.fc
        base = {"email": "otra@ejemplo.com", "nombre": "Otra Persona", "nacionalidad": "Argentina", "dni": "30999888",
                "cuit": "20309998887", "domicilio": "Calle 1", "localidad": "CABA", "provincia": "Buenos Aires",
                "codigo_postal": "1000", "estado_civil": "Soltero/a", "servicio": fc.SERVICIO_VISTAS, "marca_nombre": "OTRA"}
        self.assertEqual(self.cli.post("/api/publico/formulario/persona-fisica", data=base).status_code, 200)
        rid = self._uno("SELECT id FROM formularios_respuestas ORDER BY id DESC LIMIT 1")[0]
        token = self.cli.post(f"/api/formularios/{rid}/link-correccion", json={}).json()["ruta"].split("corregir=")[1]
        r = self.cli.post(f"/api/publico/formulario-correccion/{token}", data={**base, "marca_nombre": ""})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Nombre de la marca", r.json()["detail"])
        self.assertEqual(self.cli.get(f"/api/publico/formulario-correccion/{token}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
