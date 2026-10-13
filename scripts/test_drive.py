"""
Pruebas de drive_core.py (guardar el PDF del análisis de marca en Google Drive), sin red y sin base.
Correr con:
    cd scripts && python3 -m unittest test_drive -v

Google se simula con una sesión de mentira que anota cada pedido y contesta lo que le indique cada prueba.
"""

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

try:  # calendario_core importa psycopg2 al cargarse: el SQL no se corre acá
    import psycopg2.extras  # noqa: F401
except ImportError:
    fake = types.ModuleType("psycopg2")
    fake.extras = types.ModuleType("psycopg2.extras")
    fake.extras.RealDictCursor = object
    fake.extras.execute_batch = lambda cur, sql, filas: cur.lotes.append(list(filas))   # igual que test_calendario
    sys.modules["psycopg2"] = fake
    sys.modules["psycopg2.extras"] = fake.extras

import drive_core as dc  # noqa: E402

PDF = b"%PDF-1.4 prueba"
URL_VER = "https://drive.google.com/file/d/F1/view"


class Resp:
    def __init__(self, status=200, cuerpo=None, headers=None):
        self.status_code = status
        self._cuerpo = cuerpo
        self.headers = headers or {}
        self.content = b"x" if cuerpo is not None else b""

    def json(self):
        if self._cuerpo is None:
            raise ValueError("sin cuerpo")
        return self._cuerpo


class Sesion:
    """Contesta los pedidos con `guion(metodo, url, kw)`; guarda todo lo que le llegó en `.pedidos`."""

    def __init__(self, guion):
        self.guion = guion
        self.pedidos = []

    def request(self, metodo, url, **kw):
        self.pedidos.append((metodo, url, kw))
        r = self.guion(metodo, url, kw)
        if isinstance(r, Exception):
            raise r
        return r


def guion_normal(carpeta=None, previo=None):
    """Drive de mentira: `carpeta` y `previo` son lo que ya existe ({} o None = no existe)."""

    def g(metodo, url, kw):
        q = (kw.get("params") or {}).get("q", "")
        if metodo == "GET" and url == dc.API + "/files":
            if dc.MIME_CARPETA in q:
                return Resp(200, {"files": [carpeta] if carpeta else []})
            return Resp(200, {"files": [previo] if previo else []})
        if metodo == "POST" and url == dc.API + "/files":          # crear carpeta
            return Resp(200, {"id": "CARPETA-NUEVA"})
        if url.startswith(dc.SUBIDA) and (kw.get("params") or {}).get("uploadType") == "resumable":
            return Resp(200, None, headers={"Location": "https://upload.example/sesion-1"})
        if metodo == "PUT" and url == "https://upload.example/sesion-1":
            return Resp(200, {"id": "F1", "name": "x.pdf", "webViewLink": URL_VER})
        raise AssertionError(f"pedido inesperado: {metodo} {url} {kw}")

    return g


def con_sesion(guion):
    s = Sesion(guion)
    return s, mock.patch.object(dc, "_sesion", lambda: s)


class SubirPdf(unittest.TestCase):
    def test_primera_vez_crea_la_carpeta_y_sube_el_archivo(self):
        s, p = con_sesion(guion_normal())
        with p:
            r = dc.subir_pdf(PDF, "Analisis de marca - SEPIA - acta 4782038.pdf", ("acta", "4782038"))
        self.assertFalse(r["reemplazo"])
        self.assertEqual(r["url"], URL_VER)
        self.assertEqual(r["carpeta"], "Análisis de marca")
        # La carpeta se creó con la marca propia del panel…
        crear = [x for x in s.pedidos if x[0] == "POST" and x[1] == dc.API + "/files"][0]
        self.assertEqual(crear[2]["json"]["mimeType"], dc.MIME_CARPETA)
        self.assertEqual(crear[2]["json"]["appProperties"], {"panel": "analisis-marca"})
        # …y el archivo quedó adentro de ella, marcado con su acta.
        inicio = [x for x in s.pedidos if x[0] == "POST" and x[1].startswith(dc.SUBIDA)][0]
        self.assertEqual(inicio[2]["json"]["parents"], ["CARPETA-NUEVA"])
        self.assertEqual(inicio[2]["json"]["appProperties"], {"panel": "analisis-marca", "acta": "4782038"})
        self.assertEqual(inicio[2]["json"]["name"], "Analisis de marca - SEPIA - acta 4782038.pdf")
        self.assertEqual(inicio[2]["headers"]["X-Upload-Content-Length"], str(len(PDF)))
        # El contenido se manda en el segundo pedido, como PDF.
        subida = s.pedidos[-1]
        self.assertEqual((subida[0], subida[1]), ("PUT", "https://upload.example/sesion-1"))
        self.assertEqual(subida[2]["data"], PDF)
        self.assertEqual(subida[2]["headers"]["Content-Type"], "application/pdf")

    def test_si_la_carpeta_ya_existe_no_crea_otra(self):
        s, p = con_sesion(guion_normal(carpeta={"id": "CARPETA-VIEJA"}))
        with p:
            dc.subir_pdf(PDF, "a.pdf", ("acta", "1234567"))
        self.assertFalse([x for x in s.pedidos if x[0] == "POST" and x[1] == dc.API + "/files"])
        inicio = [x for x in s.pedidos if x[0] == "POST" and x[1].startswith(dc.SUBIDA)][0]
        self.assertEqual(inicio[2]["json"]["parents"], ["CARPETA-VIEJA"])

    def test_volver_a_subir_la_misma_acta_reemplaza_el_archivo(self):
        s, p = con_sesion(guion_normal(carpeta={"id": "C1"}, previo={"id": "VIEJO"}))
        with p:
            r = dc.subir_pdf(PDF, "nombre nuevo.pdf", ("acta", "4782038"))
        self.assertTrue(r["reemplazo"])
        inicio = [x for x in s.pedidos if x[1].startswith(dc.SUBIDA) and x[0] in ("POST", "PATCH")][0]
        self.assertEqual((inicio[0], inicio[1]), ("PATCH", dc.SUBIDA + "/files/VIEJO"))
        self.assertNotIn("parents", inicio[2]["json"])          # no se lo cambia de lugar si lo movieron
        self.assertEqual(inicio[2]["json"]["name"], "nombre nuevo.pdf")   # el nombre se actualiza (cambió la marca)
        # El archivo anterior se busca por su acta, entre los que son de este panel y no están en la papelera.
        q = [x[2]["params"]["q"] for x in s.pedidos if x[0] == "GET" and dc.MIME_CARPETA not in x[2]["params"]["q"]][0]
        self.assertIn("trashed = false", q)
        self.assertIn("key='acta' and value='4782038'", q)
        self.assertIn("key='panel' and value='analisis-marca'", q)

    def test_sin_clave_siempre_crea_uno_nuevo_sin_buscar(self):
        s, p = con_sesion(guion_normal(carpeta={"id": "C1"}))
        with p:
            r = dc.subir_pdf(PDF, "a.pdf")
        self.assertFalse(r["reemplazo"])
        busquedas = [x for x in s.pedidos if x[0] == "GET"]
        self.assertEqual(len(busquedas), 1)                      # solo la de la carpeta
        self.assertEqual(s.pedidos[-2][2]["json"]["appProperties"], {"panel": "analisis-marca"})

    def test_pdf_vacio(self):
        s, p = con_sesion(guion_normal())
        with p, self.assertRaises(dc.DriveError):
            dc.subir_pdf(b"", "a.pdf")
        self.assertEqual(s.pedidos, [])

    def test_google_no_dice_donde_subir(self):
        def g(metodo, url, kw):
            if metodo == "GET":
                return Resp(200, {"files": [{"id": "C1"}]})
            return Resp(200, None, headers={})                   # sin Location
        s, p = con_sesion(g)
        with p, self.assertRaises(dc.DriveError):
            dc.subir_pdf(PDF, "a.pdf")

    def test_comillas_en_la_consulta(self):
        self.assertEqual(dc._q("a'b"), "'a\\'b'")
        self.assertEqual(dc._q("a\\b"), "'a\\\\b'")


class Errores(unittest.TestCase):
    def _falla(self, status, cuerpo=None, excepcion=None):
        def g(metodo, url, kw):
            return excepcion if excepcion else Resp(status, cuerpo if cuerpo is not None else {})
        s, p = con_sesion(g)
        with p, self.assertRaises(dc.DriveError) as cm:
            dc.subir_pdf(PDF, "a.pdf", ("acta", "1234567"))
        return str(cm.exception)

    def test_api_de_drive_apagada(self):
        m = self._falla(403, {"error": {"message": "Google Drive API has not been used in project 1 before or it is disabled.",
                                        "errors": [{"reason": "accessNotConfigured"}]}})
        self.assertIn("Google Drive API", m)
        self.assertIn("Habilitar", m)

    def test_falta_el_permiso_de_drive(self):
        m = self._falla(403, {"error": {"message": "Request had insufficient authentication scopes.",
                                        "errors": [{"reason": "insufficientPermissions"}]}})
        self.assertIn("Conectar con Google", m)
        self.assertIn("GOOGLE_OAUTH_REFRESH_TOKEN", m)

    def test_token_viejo_sin_scope_de_drive(self):
        # google-auth falla al pedir el token con un scope que la conexión original nunca aceptó.
        m = self._falla(0, excepcion=Exception("('invalid_scope: Bad Request', {'error': 'invalid_scope'})"))
        self.assertIn("Conectar con Google", m)
        self.assertIn("permiso", m)

    def test_permiso_revocado(self):
        m = self._falla(0, excepcion=Exception("('invalid_grant: Token has been expired or revoked.', {})"))
        self.assertIn("ya no acepta", m)
        self.assertIn("Conectar con Google", m)

    def test_401(self):
        self.assertIn("Conectar con Google", self._falla(401, {"error": {"message": "Invalid Credentials"}}))

    def test_sin_espacio(self):
        m = self._falla(403, {"error": {"message": "The user's Drive storage quota has been exceeded.",
                                        "errors": [{"reason": "storageQuotaExceeded"}]}})
        self.assertIn("espacio", m)

    def test_otro_error_lleva_el_codigo_y_el_detalle(self):
        m = self._falla(500, {"error": {"message": "Backend Error"}})
        self.assertIn("500", m)
        self.assertIn("Backend Error", m)

    def test_red_caida(self):
        m = self._falla(0, excepcion=ConnectionError("sin red"))
        self.assertIn("No se pudo hablar con Google Drive", m)


class Conexion(unittest.TestCase):
    VARS = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN")

    def _entorno(self, **kw):
        base = {k: "" for k in self.VARS}
        base.update(kw)
        return mock.patch.dict(os.environ, base)

    def test_configurado_solo_con_las_tres_variables(self):
        with self._entorno():
            self.assertFalse(dc.configurado())
        with self._entorno(GOOGLE_OAUTH_CLIENT_ID="a", GOOGLE_OAUTH_CLIENT_SECRET="b"):
            self.assertFalse(dc.configurado())                    # falta conectar la cuenta (el token)
        with self._entorno(GOOGLE_OAUTH_CLIENT_ID="a", GOOGLE_OAUTH_CLIENT_SECRET="b", GOOGLE_OAUTH_REFRESH_TOKEN="c"):
            self.assertTrue(dc.configurado())

    def test_la_cuenta_de_servicio_no_alcanza(self):
        # Una cuenta de servicio no tiene espacio propio en Drive: sin OAuth no se ofrece el botón.
        with self._entorno(GOOGLE_SERVICE_ACCOUNT_JSON='{"client_email": "x@y", "private_key": "k"}'):
            self.assertFalse(dc.configurado())

    def test_sin_conexion_explica_como_conectar(self):
        with self._entorno(), self.assertRaises(dc.DriveError) as cm:
            dc._sesion()
        self.assertIn("GOOGLE_OAUTH_REFRESH_TOKEN", str(cm.exception))

    def test_pide_solo_el_permiso_que_crea_los_archivos_del_panel(self):
        # drive.file: el panel no puede ver ni tocar el resto del Drive del estudio.
        self.assertEqual(dc.SCOPE, "https://www.googleapis.com/auth/drive.file")


if __name__ == "__main__":
    unittest.main()
