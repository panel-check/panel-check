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


# ── Formato 2: informe preliminar de disponibilidad (modelo ATELIER ASTRANITY, 28/10/2026) ──

ASTRANITY = {
    "tipo": "informe_viabilidad_cliente", "version": 2, "estado": "borrador_pendiente_revision",
    "titulo": "Informe preliminar de disponibilidad marcaria",
    "encabezado": {
        "marca_pretendida": "ATELIER ASTRANITY", "cliente": "Carolina", "fecha": "28 de octubre de 2026",
        "referencia": "INFPR-1847",
        "web": {"url": "https://astranity.mitiendanube.com", "texto": "astranity.mitiendanube.com"},
        "instagram": {"url": "https://www.instagram.com/atelier.astranity", "texto": "@atelier.astranity"},
    },
    "secciones": [
        {"titulo": "Actividad y protección de la marca", "texto": [
            "De acuerdo con la actividad declarada y la tienda online consultada, Atelier Astranity se dedica a la "
            "comercialización de insumos para corsetería y costura.",
            "La protección de la marca debe contemplar los servicios de comercialización de estos insumos, incluida la venta online."]},
        {"titulo": "Objeto y alcance", "texto": [
            "Realizamos una verificación preliminar de la denominación \"ATELIER ASTRANITY\" en la base de datos del "
            "Instituto Nacional de la Propiedad Industrial (INPI), limitada a la búsqueda de coincidencias exactas del nombre pretendido."]},
        {"titulo": "Resultado de la búsqueda preliminar", "texto": [
            "En la consulta realizada el 28 de octubre de 2026 no se identificaron marcas vigentes con una denominación "
            "idéntica a “ATELIER ASTRANITY”. Este resultado preliminar es favorable para avanzar con el análisis completo."]},
        {"titulo": "Próximos pasos", "texto": [
            "Esta verificación no incluye el análisis de antecedentes similares ni el estudio fonético, conceptual o gráfico "
            "del signo, la evaluación de riesgos u oposiciones. No constituye una garantía de concesión.",
            "Para avanzar, te proponemos realizar el análisis completo. Podemos enviarte el alcance del servicio y el presupuesto correspondiente."]},
    ],
    "firma": {"nombre": "Pamela Guzzardi", "lineas": [
        "Agente de Propiedad Industrial – Mat. INPI 2906",
        "Smarties Consultora – Registro de Marcas Nacional e Internacional",
        "info@smartiesconsultora.com.ar", "+54 9 11 5589-0784"]},
}


class CursorFalso:
    """Simula la tabla informes_viabilidad para asignar_referencia."""
    def __init__(self, filas):
        self.filas = filas

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return [{"cliente_id": c, "marca": m, "tipo": t, "ref": r} for c, m, t, r in self.filas]


class TestDisponibilidad(unittest.TestCase):
    def test_normaliza_el_modelo(self):
        d = iv.normalizar_cliente(_copia(ASTRANITY))
        self.assertEqual(d["formato"], 2)
        self.assertEqual(d["titulo"], "Informe preliminar de disponibilidad marcaria")
        e = d["encabezado"]
        self.assertEqual((e["fecha"], e["referencia"], e["cliente"]), ("28 de octubre de 2026", "INFPR-1847", "Carolina"))
        self.assertEqual(e["instagram"]["texto"], "@atelier.astranity")
        self.assertEqual([s["titulo"] for s in d["secciones"]],
                         ["Actividad y protección de la marca", "Objeto y alcance", "Resultado de la búsqueda preliminar", "Próximos pasos"])
        self.assertIn("“ATELIER ASTRANITY”", d["secciones"][1]["parrafos"][0])
        self.assertEqual(len(d["firma"]["lineas"]), 4)

    def test_completa_y_omite(self):
        x = _copia(ASTRANITY)
        x["encabezado"].update({"cliente": "", "fecha": "", "referencia": "INFPR-", "web": "astranity.mitiendanube.com/",
                                "instagram": "@atelier.astranity"})
        x.pop("firma")
        import datetime
        d = iv.normalizar_cliente(x, "Carolina Pérez", hoy=datetime.date(2026, 11, 3))
        e = d["encabezado"]
        self.assertEqual(e["cliente"], "Carolina Pérez")
        self.assertEqual(e["fecha"], "3 de noviembre de 2026")
        self.assertEqual(e["referencia"], "")  # la asigna el panel al guardar
        self.assertEqual(e["web"], {"url": "https://astranity.mitiendanube.com/", "texto": "astranity.mitiendanube.com"})
        self.assertEqual(e["instagram"]["url"], "https://www.instagram.com/atelier.astranity")
        self.assertEqual(d["firma"]["lineas"][2], "info@smartiesconsultora.com.ar")
        x["encabezado"]["instagram"] = ""
        self.assertIsNone(iv.normalizar_cliente(x)["encabezado"]["instagram"])

    def test_fechas_y_referencias(self):
        self.assertEqual(iv.fecha_en_letras("28/10/2026"), "28 de octubre de 2026")
        self.assertEqual(iv.fecha_en_letras("2026-01-05"), "5 de enero de 2026")
        with self.assertRaises(iv.InformeInvalido):
            iv.fecha_en_letras("31/02/2026")
        self.assertEqual(iv.normalizar_referencia("infpr 1848"), "INFPR-1848")
        with self.assertRaises(iv.InformeInvalido):
            iv.normalizar_referencia("REF-12")

    def test_bloquea_lo_que_los_criterios_prohiben(self):
        for txt in ("Se protege en la clase 35.", "Es una buena señal para avanzar.", "La marca está libre.",
                    "Está disponible al 100 %.", "El riesgo bajo permite avanzar.", "No se halló “[MARCA]”."):
            x = _copia(ASTRANITY)
            x["secciones"][2]["texto"] = [txt]
            with self.assertRaises(iv.InformeInvalido, msg=txt):
                iv.normalizar_cliente(x)
        x = _copia(ASTRANITY)
        x["secciones"][3]["titulo"] = ""
        with self.assertRaises(iv.InformeInvalido):
            iv.normalizar_cliente(x)

    def test_el_formato_anterior_sigue_andando(self):
        self.assertFalse(iv.es_v2(_copia(CLIENTE)))
        d = iv.normalizar_cliente(_copia(CLIENTE))
        self.assertNotIn("formato", d)
        self.assertTrue(iv.generar_pdf(d).startswith(b"%PDF"))

    def test_pdf_como_el_modelo(self):
        d = iv.normalizar_cliente(_copia(ASTRANITY))
        pdf = iv.generar_pdf(d)
        self.assertEqual(iv.nombre_archivo(d), "Informe_Preliminar_ATELIER_ASTRANITY_INFPR-1847.pdf")
        try:
            from pypdf import PdfReader
        except ImportError:
            return
        r = PdfReader(io.BytesIO(pdf))
        self.assertEqual(len(r.pages), 1)
        texto = r.pages[0].extract_text()
        for esperado in ("Informe preliminar de disponibilidad marcaria", "Marca pretendida: ATELIER ASTRANITY",
                         "Referencia de consulta: INFPR-1847", "astranity.mitiendanube.com", "@atelier.astranity",
                         "Objeto y alcance", "Próximos pasos", "Pamela Guzzardi", "+54 9 11 5589-0784"):
            self.assertIn(esperado, texto)
        for viejo in ("SOLICITUD DE REGISTRO", "Antecedentes:", "Logo:"):
            self.assertNotIn(viejo, texto)
        enlaces = [a["/A"]["/URI"] for a in r.pages[0].get("/Annots", []) or [] if "/A" in a.get_object()]
        self.assertIn("https://www.instagram.com/atelier.astranity", enlaces)

    def test_asignar_referencia(self):
        usadas = [(1, "ATELIER ASTRANITY", "cliente", "INFPR-1847"), (2, "OTRA", "interno", "INFPR-1850")]
        # sin referencia → la siguiente a la más alta
        d = iv.normalizar_cliente(_copia(ASTRANITY))
        d["encabezado"]["referencia"] = ""
        d["encabezado"]["marca_pretendida"] = "NUEVA"
        self.assertEqual(iv.asignar_referencia(CursorFalso(usadas), 5, d), "INFPR-1851")
        # base vacía → arranca en 1848 (la 1847 fue la del modelo)
        d["encabezado"]["referencia"] = ""
        self.assertEqual(iv.asignar_referencia(CursorFalso([]), 5, d), "INFPR-1847")
        # corrección de la misma marca del mismo cliente → conserva la suya
        d["encabezado"].update({"referencia": "", "marca_pretendida": "Atelier Astranity"})
        self.assertEqual(iv.asignar_referencia(CursorFalso(usadas), 1, d), "INFPR-1847")
        # la misma referencia explícita en otro cliente → error
        d["encabezado"]["referencia"] = "INFPR-1847"
        with self.assertRaises(iv.InformeInvalido):
            iv.asignar_referencia(CursorFalso(usadas), 9, d)
        self.assertEqual(iv.referencia_de_marca(CursorFalso(usadas), 1, "atelier astranity"), "INFPR-1847")

    def test_resumen_interno_v2(self):
        d = {"tipo": "informe_viabilidad_interno", "version": 2,
             "caso": {"marca_pretendida": "ATELIER ASTRANITY", "referencia": "INFPR-1847"},
             "busqueda_inpi": {"consultas_procedimiento": [{"orden": 1}, {"orden": 2}, {"orden": 3}],
                               "coincidencias_exactas": [{"acta": "1"}],
                               "busqueda_amplia": {"antecedentes_astranity_clases_24_26_35": [{"acta": 1}, {"acta": 2}]}}}
        r = iv.resumen_interno(iv.normalizar_interno(d))
        self.assertEqual((r["referencia"], r["consultas"], r["identicas"], r["antecedentes_listados"]), ("INFPR-1847", 3, 1, 2))


if __name__ == "__main__":
    unittest.main()
