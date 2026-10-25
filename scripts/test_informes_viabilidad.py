"""Pruebas del informe preliminar de viabilidad (panel/informes_viabilidad.py), sin base ni red.

    cd scripts && python3 -m unittest test_informes_viabilidad
"""
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))

import informes_viabilidad as iv  # noqa: E402

CLIENTE = {
    "tipo": "informe_viabilidad_cliente",
    "encabezado": {
        "marca_pretendida": "SABER LIBROS", "fecha_informe": "25/10/2026", "cliente": "",
        "web": "https://www.saberlibros.com.ar/", "instagram": "",
        "actividad": "Comercialización minorista de libros, a través de un local físico en San Francisco, Córdoba.",
        "antecedentes": ".-", "logo": "Pendiente de análisis.",
    },
    "secciones": [
        {"numero": 1, "titulo": "PROTECCIÓN DE LA MARCA",
         "texto": ["La denominación «SABER LIBROS» constituye el principal signo distintivo de la actividad.",
                   "Considerando la actividad informada, la protección principal corresponde a los servicios de "
                   "comercialización minorista de libros."]},
        {"numero": 2, "titulo": "Resultado de la búsqueda preliminar",
         "texto": "No se identificaron marcas idénticas a la denominación completa \"SABER LIBROS\".\n\n"
                  "Se encontraron antecedentes que comparten algunos de sus términos."},
        {"numero": 3, "titulo": "", "texto": "Para avanzar, completaremos el análisis de los antecedentes identificados."},
        {"numero": 4, "titulo": "", "texto": "En caso de estar interesados, quedamos a la espera de sus instrucciones."},
    ],
}

INTERNO = {
    "tipo": "informe_viabilidad_interno",
    "caso": {"marca_pretendida": "SABER LIBROS", "fecha_consulta_inpi": "25/10/2026 17:30"},
    "busqueda_inpi": {
        "identicas": [],
        "antecedentes_relevantes": [{"acta": "3564238", "denominacion": "SABER", "titular": "LIC AUTOMATION LIMITED",
                                     "clase": 35, "vigencia": "14/02/2028", "por_que_es_relevante": "SABER sola en 35"}],
        "antecedentes_saber_clases_9_16_35_41": [{"acta": 1}, {"acta": 2}],
    },
    "limitaciones": ["Instagram no se pudo leer"],
    "observaciones_para_pame": "Mirar SABER de LIC Automation.",
}


def _copia(d):
    return json.loads(json.dumps(d))


class TestCliente(unittest.TestCase):
    def test_normaliza_y_completa(self):
        d = iv.normalizar_cliente(_copia(CLIENTE), "Pintos, Gabriela")
        self.assertEqual(d["encabezado"]["cliente"], "Pintos, Gabriela")  # vacío → nombre de la ficha
        self.assertEqual(d["firma"]["matricula"], "Matrícula INPI N° 2906")
        self.assertEqual(len(d["secciones"]), 4)
        self.assertEqual(len(d["secciones"][1]["parrafos"]), 2)          # \n\n separa párrafos
        self.assertIn("“SABER LIBROS”", d["secciones"][0]["parrafos"][0])  # comillas como el modelo
        self.assertIn("“SABER LIBROS”", d["secciones"][1]["parrafos"][0])

    def test_no_deja_mostrar_clases(self):
        for txt in ("se identifica como principal la clase 35", "en clases 16 y 41", "Clase N° 9"):
            d = _copia(CLIENTE)
            d["secciones"][0]["texto"] = [txt]
            with self.assertRaises(iv.InformeInvalido) as e:
                iv.normalizar_cliente(d)
            self.assertIn("clase", str(e.exception).lower())

    def test_la_palabra_clase_sin_numero_no_bloquea(self):
        d = _copia(CLIENTE)
        d["secciones"][0]["texto"] = ["Se definirá la clase adecuada al avanzar."]
        iv.normalizar_cliente(d)

    def test_json_equivocado_o_incompleto(self):
        with self.assertRaises(iv.InformeInvalido):
            iv.normalizar_cliente(_copia(INTERNO))
        sin_marca = _copia(CLIENTE)
        sin_marca["encabezado"]["marca_pretendida"] = ""
        with self.assertRaises(iv.InformeInvalido):
            iv.normalizar_cliente(sin_marca)
        with self.assertRaises(iv.InformeInvalido):
            iv.normalizar_cliente({"encabezado": {"marca_pretendida": "X"}, "secciones": []})

    def test_leer_json(self):
        self.assertEqual(iv.leer_json("```json\n{\"a\": 1}\n```"), {"a": 1})
        with self.assertRaises(iv.InformeInvalido) as e:
            iv.leer_json("{ mal")
        self.assertIn("línea", str(e.exception))
        with self.assertRaises(iv.InformeInvalido):
            iv.leer_json("[1, 2]")

    def test_pdf(self):
        d = iv.normalizar_cliente(_copia(CLIENTE), "Pintos, Gabriela")
        pdf = iv.generar_pdf(d)
        self.assertTrue(pdf.startswith(b"%PDF"))
        try:
            from pypdf import PdfReader
        except ImportError:
            return
        texto = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
        for esperado in ("INFORME PRELIMINAR", "SOLICITUD DE REGISTRO DE MARCA EN ARGENTINA", "Marca pretendida:",
                         "SABER LIBROS", "Sitio web:", "Antecedentes:", "PROTECCIÓN DE LA MARCA",
                         "Resultado de la búsqueda preliminar", "Matrícula INPI N° 2906", "Smarties Consultora"):
            self.assertIn(esperado, texto)
        self.assertEqual(iv.nombre_archivo(d), "Informe_Preliminar_SABER_LIBROS.pdf")


class TestInterno(unittest.TestCase):
    def test_resumen(self):
        d = iv.normalizar_interno(_copia(INTERNO))
        self.assertEqual(iv.marca_interno(d), "SABER LIBROS")
        r = iv.resumen_interno(d)
        self.assertEqual(r["identicas"], 0)
        self.assertEqual(r["relevantes"][0]["acta"], "3564238")
        self.assertEqual(r["antecedentes_listados"], 2)
        self.assertEqual(r["fecha_consulta"], "25/10/2026 17:30")

    def test_rechaza_el_del_cliente_o_sin_marca(self):
        with self.assertRaises(iv.InformeInvalido):
            iv.normalizar_interno(_copia(CLIENTE))
        with self.assertRaises(iv.InformeInvalido):
            iv.normalizar_interno({"busqueda_inpi": {}})


if __name__ == "__main__":
    unittest.main()
