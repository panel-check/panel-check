"""
existe_expediente (scripts/validar_leads.py): un acta solo se da por inexistente si INPI responde
«Marca no encontrada». Cualquier otra página sin expediente (INPI lento o con error) es «no se sabe».

    cd scripts && python3 -m unittest test_existe_expediente
"""

import unittest

try:
    import validar_leads as vl
except ImportError:  # faltan dependencias del panel
    vl = None


class _Resp:
    def __init__(self, texto):
        self.text = texto


class _Sesion:
    def __init__(self, texto):
        self.texto = texto

    def post(self, *a, **k):
        return _Resp(self.texto)


@unittest.skipIf(vl is None, "faltan dependencias")
class ExisteExpediente(unittest.TestCase):
    def test_expediente_real(self):
        self.assertTrue(vl.existe_expediente(_Sesion("<h2>TITULARIDAD</h2> ... GESTION DEL TRAMITE"), "1")[0])

    def test_marca_no_encontrada(self):
        texto = "Toggle navigation Su operacion no se ha podido realizar! Marca no encontrada. Volver al inicio"
        self.assertIs(vl.existe_expediente(_Sesion(texto), "1")[0], False)

    def test_pagina_de_error_no_es_inexistente(self):
        self.assertIsNone(vl.existe_expediente(_Sesion("<html>Error en el servidor</html>"), "1")[0])
        self.assertIsNone(vl.existe_expediente(_Sesion(""), "1")[0])

    def test_bloqueo_del_waf(self):
        self.assertIsNone(vl.existe_expediente(_Sesion("Web Page Blocked"), "1")[0])


if __name__ == "__main__":
    unittest.main()
