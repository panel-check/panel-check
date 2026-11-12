"""Pruebas del Informe Vista IA (panel/informes_vista.py) y de cómo se agrupa en Más → Informes.
Correr: python3 -m unittest scripts/test_informes_vista.py"""

import copy
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))

import informes_oposicion as io_  # noqa: E402
import informes_vista as ivi  # noqa: E402

BASE = {
    "tipo": "informe_vista_ia",
    "resumen_express": {"cliente_marca": "TIENDA COOPERATIVA", "chances": "Bajas a medias", "camino_sugerido": "Contestar las dos vistas"},
    "marca_cliente": {"denominacion": "TIENDA COOPERATIVA"},
    "vistas": [
        {"acta_cliente": "4815648", "articulos": ["art. 2 inc. a Ley 22.362"], "vencimiento": "11/12/2026"},
        {"acta_cliente": "4815647", "articulos": ["art. 2 inc. a Ley 22.362"], "vencimiento": "05/12/2026"},
    ],
    "chances": {"nivel": "Bajas_a_medias", "motivo": "dos palabras genéricas"},
}


class TestVista(unittest.TestCase):
    def test_valido_y_resumen(self):
        d = ivi.normalizar(copy.deepcopy(BASE))
        self.assertEqual(ivi.marca(d), "TIENDA COOPERATIVA")
        r = ivi.resumen(d)
        self.assertEqual(r["chances"], "bajas a medias")
        self.assertEqual(r["articulos"], ["art. 2 inc. a Ley 22.362"])  # sin repetir
        self.assertEqual(r["vencimiento"], "2026-12-05")  # el más cercano
        self.assertEqual(r["vistas"], 2)
        self.assertEqual(r["actas"], ["4815648", "4815647"])

    def test_reconoce_sin_tipo_y_no_confunde(self):
        d = copy.deepcopy(BASE)
        del d["tipo"]
        self.assertTrue(ivi.es_vista(d))
        self.assertFalse(io_.es_oposicion(d))
        opo = {"tipo": "informe_oposicion_ia", "marca_cliente": {"denominacion": "X"}, "oposiciones": [{"oponente": "Y"}]}
        self.assertFalse(ivi.es_vista(opo))
        with self.assertRaises(ivi.VistaInvalida):
            ivi.normalizar(copy.deepcopy(opo))
        with self.assertRaises(io_.OposicionInvalida):
            io_.normalizar(copy.deepcopy(BASE))

    def test_faltantes(self):
        for borrar, msj in (("resumen_express", "resumen_express"), ("vistas", "vistas")):
            d = copy.deepcopy(BASE)
            del d[borrar]
            with self.assertRaises(ivi.VistaInvalida) as e:
                ivi.normalizar(d)
            self.assertIn(msj, str(e.exception))
        d = copy.deepcopy(BASE)
        d["vistas"][0]["acta_cliente"] = ""
        with self.assertRaises(ivi.VistaInvalida):
            ivi.normalizar(d)
        d = copy.deepcopy(BASE)
        d["marca_cliente"] = {}
        with self.assertRaises(ivi.VistaInvalida):
            ivi.normalizar(d)

    def test_chances_y_firma(self):
        d = copy.deepcopy(BASE)
        d["chances"] = "Altísimas"
        with self.assertRaises(ivi.VistaInvalida):
            ivi.normalizar(d)
        d = copy.deepcopy(BASE)
        d["chances"] = "medias"
        self.assertEqual(ivi.normalizar(d)["chances"], {"nivel": "medias", "motivo": ""})
        d = copy.deepcopy(BASE)
        d["resumen"] = "Saludos [FIRMA]"
        with self.assertRaises(ivi.VistaInvalida):
            ivi.normalizar(d)


class TestAgrupar(unittest.TestCase):
    def test_familia_y_filtro(self):
        try:
            import informes_api as ia
        except ImportError as e:  # sin fastapi o psycopg2 instalados
            self.skipTest(f"falta una dependencia del panel: {e}")
        ahora = dt.datetime(2026, 11, 12, 10, 0)
        filas = [
            {"id": 1, "tipo": "vista", "marca": "TIENDA COOPERATIVA", "datos": ivi.normalizar(copy.deepcopy(BASE)),
             "generado_en": ahora, "cliente_id": 7, "cliente_nombre": "Coop. Pehuajó"},
            {"id": 2, "tipo": "interno", "marca": "TIENDA COOPERATIVA", "datos": {"caso": {"marca_pretendida": "TIENDA COOPERATIVA"}},
             "generado_en": ahora, "cliente_id": 7, "cliente_nombre": "Coop. Pehuajó"},
        ]
        grupos = ia.agrupar_informes(filas)
        self.assertEqual(sorted(g["familia"] for g in grupos), ["preliminar", "vista"])
        solo = ia.agrupar_informes(filas, falta="vistas")
        self.assertEqual(len(solo), 1)
        self.assertEqual(solo[0]["vista"][0]["chances"], "bajas a medias")
        self.assertEqual(len(ia.agrupar_informes(filas, q="art. 2 inc. a")), 1)  # se busca por artículo
        self.assertEqual(ia.agrupar_informes(filas, falta="cliente")[0]["familia"], "preliminar")
        self.assertEqual(ia.tipo_de_json(copy.deepcopy(BASE)), "vista")


if __name__ == "__main__":
    unittest.main()
