"""Escrito DESISTE (solapa Poderes de la ficha del cliente): texto del modelo del estudio y archivos PDF / Word.

    cd scripts && python3 -m unittest test_desistimiento
"""
import datetime as dt
import io
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import desistimiento as ds  # noqa: E402
import poderes  # noqa: E402

CLIENTE = {"nombre": "SLFRAGRANCE S.R.L.", "cuit": "30717233170", "tipo_persona": "juridica",
           "domicilio": "Manuela Pedraza 1776", "localidad": "Lanús", "provincia": "Buenos Aires"}
MARCA = {"acta": 4533354, "denominacion": "SL. LAB BUENOS AIRES: 1985", "clase": 3, "tipo": "D",
         "titular": "SLFRAGRANCE S.R.L."}


class TestDesiste(unittest.TestCase):
    def setUp(self):
        self.d = ds.propuesta(MARCA, CLIENTE, dt.date(2026, 11, 19))

    def test_propuesta(self):
        self.assertEqual(self.d["acta"], "4533354")
        self.assertEqual(self.d["tipo"], "DENOMINATIVA")
        self.assertEqual(self.d["identificacion"], "CUIT 30-71723317-0")
        self.assertEqual(self.d["domicilio"], "Manuela Pedraza 1776, Lanús, República Argentina")
        self.assertEqual(self.d["genero"], "domiciliada")

    def test_texto_del_modelo(self):
        t = ds._textos(self.d, poderes.APODERADO_DEFAULT)
        self.assertEqual(t["titulo"], "DESISTE DE LA SOLICITUD DE REGISTRO DE LA MARCA “SL. LAB BUENOS AIRES: 1985” "
                                      "(DENOMINATIVA), ACTA N.º 4533354, CLASE 3 INTERNACIONAL.")
        self.assertEqual(t["presentacion"],
                         "Pamela Daiana Guzzardi, Agente de la Propiedad Industrial, Matrícula INPI N.º 2906, en mi "
                         "carácter de apoderada de SLFRAGRANCE S.R.L., CUIT 30-71723317-0, domiciliada en Manuela "
                         "Pedraza 1776, Lanús, República Argentina, conforme al poder que obra en el expediente, al "
                         "Sr. Director respetuosamente digo:")
        self.assertEqual(t["desiste"], "Que, cumpliendo con expresas instrucciones de mi mandante, vengo por el presente "
                                       "a desistir de la solicitud de registro de la marca “SL. LAB BUENOS AIRES: 1985” "
                                       "(DENOMINATIVA), Acta N.º 4533354, en clase 3 Internacional.")
        self.assertEqual(t["cierre"], "SERÁ JUSTICIA.-")

    def test_persona_fisica(self):
        c = {"nombre": "Ana Pérez", "dni": "30123456", "sexo": "Femenino", "domicilio": "Av. Rivadavia 100",
             "provincia": "Ciudad Autónoma de Buenos Aires"}
        d = ds.propuesta({"acta": "1", "denominacion": "X", "clase": 9, "tipo": "M"}, c, dt.date(2026, 11, 19))
        self.assertEqual(d["identificacion"], "DNI 30.123.456")
        self.assertEqual(d["genero"], "domiciliada")
        self.assertIn("Ciudad Autónoma de Buenos Aires, República Argentina", d["domicilio"])

    def test_archivos(self):
        try:
            import reportlab  # noqa: F401
            import docx  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("faltan reportlab / python-docx")
        self.assertTrue(ds.generar_pdf(self.d, poderes.APODERADO_DEFAULT).startswith(b"%PDF"))
        self.assertTrue(ds.generar_docx(self.d, poderes.APODERADO_DEFAULT).startswith(b"PK"))
        z = zipfile.ZipFile(io.BytesIO(ds.generar_zip([self.d, self.d], poderes.APODERADO_DEFAULT, "pdf")))
        self.assertEqual(len(z.namelist()), 2)


if __name__ == "__main__":
    unittest.main()
