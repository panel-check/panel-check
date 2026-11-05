"""Pruebas del Informe Oposición IA (panel/informes_oposicion.py). Correr: python3 -m unittest scripts/test_informes_oposicion.py"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))

import informes_oposicion as io_  # noqa: E402

BASE = {
    "tipo": "informe_oposicion_ia",
    "resumen_express": {"cliente_marca": "PEAKS", "chances": "Bajas a medias", "camino_sugerido": "Negociar"},
    "marca_cliente": {"denominacion": "PEAKS"},
    "oposiciones": [
        {"acta_cliente": "4790681", "oponente": "SKYPEAK S.A.", "vencimiento_negociacion": "20/01/2027"},
        {"acta_cliente": "4790682", "oponente": "SKYPEAK S.A.", "vencimiento_negociacion": "15/12/2026"},
    ],
    "chances": {"nivel": "Bajas_a_medias", "motivo": "productos idénticos"},
}


class TestOposicion(unittest.TestCase):
    def test_valido_y_resumen(self):
        d = io_.normalizar(copy.deepcopy(BASE))
        self.assertEqual(io_.marca(d), "PEAKS")
        r = io_.resumen(d)
        self.assertEqual(r["chances"], "bajas a medias")
        self.assertEqual(r["oponentes"], ["SKYPEAK S.A."])
        self.assertEqual(r["vencimiento"], "2026-12-15")  # el más cercano
        self.assertEqual(r["oposiciones"], 2)

    def test_reconoce_sin_tipo(self):
        d = copy.deepcopy(BASE)
        del d["tipo"]
        self.assertTrue(io_.es_oposicion(d))
        self.assertFalse(io_.es_oposicion({"caso": {}, "busqueda_inpi": {}}))

    def test_faltantes(self):
        for quitar in ("marca_cliente", "resumen_express", "oposiciones"):
            d = copy.deepcopy(BASE)
            d.pop(quitar)
            with self.assertRaises(io_.OposicionInvalida):
                io_.normalizar(d)
        d = copy.deepcopy(BASE)
        d["oposiciones"][0]["oponente"] = ""
        with self.assertRaises(io_.OposicionInvalida):
            io_.normalizar(d)

    def test_chances_invalidas_y_firma(self):
        d = copy.deepcopy(BASE)
        d["chances"]["nivel"] = "regulares"
        with self.assertRaises(io_.OposicionInvalida):
            io_.normalizar(d)
        d = copy.deepcopy(BASE)
        d["resumen"] = "Saludos, [FIRMA]"
        with self.assertRaises(io_.OposicionInvalida):
            io_.normalizar(d)

    def test_rechaza_json_del_preliminar(self):
        with self.assertRaises(io_.OposicionInvalida):
            io_.normalizar({"tipo": "informe_viabilidad_interno", "caso": {}})


if __name__ == "__main__":
    unittest.main()
