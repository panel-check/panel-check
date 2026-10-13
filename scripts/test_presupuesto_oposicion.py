"""Pruebas del botón «Presupuesto» de la pestaña «Análisis de marca» (panel/presupuesto_oposicion.py y el
modo presupuesto de analisis_marca.generar_pdf).

Sin base ni servidor: un cursor falso con vigilancia_config en memoria. El PDF se arma de verdad con
reportlab (se saltea si no está instalado).
Ejecutar: cd scripts && python3 -m unittest test_presupuesto_oposicion
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import analisis_marca as am  # noqa: E402
import presupuesto_oposicion as po  # noqa: E402

try:
    import reportlab  # noqa: F401
    HAY_REPORTLAB = True
except ImportError:
    HAY_REPORTLAB = False


class CursorConfig:
    """Lo mínimo de vigilancia_config que usan leer_config / guardar_config."""

    def __init__(self):
        self.filas = {}
        self._res = None

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        if q.startswith("SELECT valor FROM vigilancia_config WHERE clave"):
            v = self.filas.get(params[0])
            self._res = [(v,)] if v is not None else []
        elif q.startswith("INSERT INTO vigilancia_config (clave, valor)"):
            self.filas[params[0]] = params[1]
        else:
            raise AssertionError("consulta inesperada: " + q)

    def fetchone(self):
        return self._res[0] if self._res else None


def sin_negritas(t: str) -> str:
    return t.replace("**", "")


class Texto(unittest.TestCase):
    def test_con_los_valores_por_defecto_sale_el_texto_acordado(self):
        t = sin_negritas(po.texto(po.config_efectiva()))
        self.assertTrue(t.startswith("La primera alternativa que podemos evaluar es:\n\nNegociación con el oponente, con el "
                                     "objetivo de procurar el retiro de la oposición mediante un acuerdo entre las partes."))
        self.assertIn("son de $790.000, e incluyen hasta 3 horas de trabajo profesional.", t)
        self.assertIn("la hora profesional tiene un valor de $190.000. Este presupuesto corresponde exclusivamente a la "
                      "etapa de negociación y se aplica por cada oposición recibida y por cada clase.", t)
        self.assertIn("evaluaremos la conveniencia de continuar con la defensa en el procedimiento administrativo de "
                      "oposición ante el INPI. Esta etapa se cotiza por separado.", t)
        self.assertIn("con una denominación que presente un menor riesgo de conflicto", t)
        self.assertIn("es de $180.000, más la tasa oficial del INPI de $41.259 (valores por una marca en una clase).", t)
        self.assertTrue(t.endswith("reducir el riesgo de nuevas oposiciones."))

    def test_son_cinco_parrafos_sin_viñetas(self):
        bloques = am._bloques_de_texto(po.texto(po.config_efectiva()))
        self.assertEqual([b[0] for b in bloques], ["p"] * 5)

    def test_las_negritas_estan_balanceadas_y_marcan_lo_importante(self):
        t = po.texto(po.config_efectiva())
        self.assertEqual(t.count("**") % 2, 0)
        for resaltado in ("Negociación con el oponente", "$790.000", "hasta 3 horas de trabajo profesional", "$190.000",
                          "exclusivamente a la etapa de negociación", "Esta etapa se cotiza por separado.", "$180.000",
                          "$41.259", "búsqueda integral de viabilidad marcaria"):
            self.assertIn(f"**{resaltado}**", t)

    def test_los_montos_cambian_con_la_config(self):
        t = sin_negritas(po.texto(po.config_efectiva({
            "honorarios_negociacion": 850000, "horas_incluidas": 1, "hora_adicional": 200000,
            "honorarios_registro": 1234567, "tasa_inpi": 50000})))
        self.assertIn("son de $850.000, e incluyen hasta 1 hora de trabajo profesional.", t)
        self.assertIn("valor de $200.000.", t)
        self.assertIn("es de $1.234.567, más la tasa oficial del INPI de $50.000", t)


class Validar(unittest.TestCase):
    def test_acepta_numeros_con_puntos_signo_peso_y_enteros(self):
        v = po.validar({"honorarios_negociacion": "$790.000", "horas_incluidas": "3", "hora_adicional": 190000,
                        "tasa_inpi": " 41259 "})
        self.assertEqual(v, {"honorarios_negociacion": 790000, "horas_incluidas": 3, "hora_adicional": 190000,
                             "tasa_inpi": 41259})

    def test_los_campos_vacios_o_ausentes_no_se_tocan(self):
        self.assertEqual(po.validar({"tasa_inpi": "", "hora_adicional": None}), {})
        self.assertEqual(po.validar(None), {})

    def test_rechaza_lo_que_no_es_un_monto_valido(self):
        for malo in ("abc", "12,5", "1.5", "0", "-5", True, 10.5, "999999999"):
            with self.assertRaises(ValueError, msg=repr(malo)):
                po.validar({"honorarios_negociacion": malo})
        with self.assertRaises(ValueError):
            po.validar({"horas_incluidas": "101"})

    def test_ignora_campos_que_no_son_del_presupuesto(self):
        self.assertEqual(po.validar({"cualquier_cosa": "1", "tasa_inpi": "5"}), {"tasa_inpi": 5})


class ConfigGuardada(unittest.TestCase):
    def test_sin_nada_guardado_salen_los_valores_por_defecto(self):
        self.assertEqual(po.leer_config(CursorConfig()), po.DEFAULTS)

    def test_se_guarda_mezclando_con_lo_anterior(self):
        cur = CursorConfig()
        po.guardar_config(cur, {"honorarios_negociacion": "900.000"})
        cfg = po.guardar_config(cur, {"tasa_inpi": "45000"})
        self.assertEqual(cfg["honorarios_negociacion"], 900000)
        self.assertEqual(cfg["tasa_inpi"], 45000)
        self.assertEqual(cfg["hora_adicional"], po.DEFAULTS["hora_adicional"])
        self.assertEqual(po.leer_config(cur), cfg)
        self.assertEqual(json.loads(cur.filas[po.CONFIG_CLAVE]), {"honorarios_negociacion": 900000, "tasa_inpi": 45000})

    def test_un_valor_invalido_no_guarda_nada(self):
        cur = CursorConfig()
        with self.assertRaises(ValueError):
            po.guardar_config(cur, {"tasa_inpi": "mucho"})
        self.assertEqual(cur.filas, {})

    def test_un_json_roto_o_con_basura_vuelve_a_los_valores_por_defecto(self):
        cur = CursorConfig()
        cur.filas[po.CONFIG_CLAVE] = "{no es json"
        self.assertEqual(po.leer_config(cur), po.DEFAULTS)
        cur.filas[po.CONFIG_CLAVE] = json.dumps({"tasa_inpi": "texto", "otra": 3, "hora_adicional": True})
        self.assertEqual(po.leer_config(cur), po.DEFAULTS)

    def test_los_ajustes_de_un_pdf_van_encima_de_lo_guardado_sin_guardarse(self):
        cur = CursorConfig()
        po.guardar_config(cur, {"tasa_inpi": "45000"})
        cfg = po.con_ajustes(po.leer_config(cur), {"honorarios_negociacion": "1.000.000"})
        self.assertEqual((cfg["honorarios_negociacion"], cfg["tasa_inpi"]), (1000000, 45000))
        self.assertEqual(po.leer_config(cur)["honorarios_negociacion"], po.DEFAULTS["honorarios_negociacion"])


def marca(n="4770308", nombre="PUENTES"):
    return {"acta": n, "url_acta": None, "marca": nombre, "tipo_marca": "Denominativa", "limitacion": "", "publicaciones": [],
            "clase": "41", "logo": None, "titular": "HORST VIVIANA PATRICIA (CUIT 27-23891699-8)",
            "oposiciones": {"origen": "sistema", "cantidad": 0, "items": [], "consultado_en": None},
            "texto": "ANALISIS PROPIO DE LA MARCA", "fecha": "2026-10-13"}


class NombreDelArchivo(unittest.TestCase):
    def test_con_presupuesto_empieza_con_presupuesto(self):
        self.assertEqual(am.nombre_archivo(marca(), True), "Presupuesto - PUENTES - acta 4770308.pdf")
        self.assertEqual(am.nombre_archivo(marca()), "Analisis de marca - PUENTES - acta 4770308.pdf")
        self.assertEqual(am.nombre_archivo([marca(), marca("4770309")], True),
                         "Presupuesto - HORST VIVIANA PATRICIA - 2 marcas.pdf")
        self.assertEqual(am.nombre_archivo([marca(), marca("4770309")]),
                         "Analisis de marcas - HORST VIVIANA PATRICIA - 2 marcas.pdf")

    def test_con_una_sola_marca_en_la_lista_es_el_de_una_marca(self):
        self.assertEqual(am.nombre_archivo([marca()], True), "Presupuesto - PUENTES - acta 4770308.pdf")


@unittest.skipUnless(HAY_REPORTLAB, "no hay reportlab")
class PdfPresupuesto(unittest.TestCase):
    def _texto_pdf(self, pdf: bytes) -> str:
        try:
            import io
            from pypdf import PdfReader
        except ImportError:
            self.skipTest("no hay pypdf para leer el PDF")
        return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)

    def test_el_pdf_con_presupuesto_se_arma_y_empieza_como_pdf(self):
        pdf = am.generar_pdf(marca(), texto_presupuesto=po.texto(po.config_efectiva()))
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_sale_el_presupuesto_y_no_el_analisis_escrito(self):
        t = self._texto_pdf(am.generar_pdf(marca(), texto_presupuesto=po.texto(po.config_efectiva())))
        self.assertIn("Presupuesto", t)
        self.assertIn("PUENTES", t)                      # los datos de la marca salen igual
        self.assertIn("$790.000", t)
        self.assertIn("$41.259", t)
        self.assertNotIn("ANALISIS PROPIO DE LA MARCA", t)
        self.assertNotIn("Análisis de marca", t)

    def test_el_texto_del_presupuesto_arranca_bajo_un_titulo_presupuesto(self):
        t = self._texto_pdf(am.generar_pdf(marca(), texto_presupuesto=po.texto(po.config_efectiva())))
        self.assertEqual(t.count("Presupuesto"), 2)   # el título de arriba y el de la sección
        self.assertLess(t.index("Oposiciones:"), t.rindex("Presupuesto"))
        self.assertLess(t.rindex("Presupuesto"), t.index("La primera alternativa que podemos evaluar es:"))

    def test_las_negritas_salen_como_negritas_y_no_como_asteriscos(self):
        pdf = am.generar_pdf(marca(), texto_presupuesto=po.texto(po.config_efectiva()))
        self.assertNotIn("**", self._texto_pdf(pdf))

    def test_en_el_analisis_escrito_los_asteriscos_no_hacen_negrita(self):
        d = marca()
        d["texto"] = "Riesgo **alto** de confusión"
        t = self._texto_pdf(am.generar_pdf(d))
        self.assertIn("Riesgo **alto** de confusión", t)

    def test_el_pdf_de_siempre_no_cambia(self):
        t = self._texto_pdf(am.generar_pdf(marca()))
        self.assertIn("Análisis de marca", t)
        self.assertIn("ANALISIS PROPIO DE LA MARCA", t)
        self.assertNotIn("$790.000", t)

    def test_con_varias_marcas_el_presupuesto_sale_una_sola_vez_al_final(self):
        t = self._texto_pdf(am.generar_pdf([marca(), marca("4770309", "OTRA")],
                                           texto_presupuesto=po.texto(po.config_efectiva())))
        self.assertIn("PUENTES", t)
        self.assertIn("OTRA", t)
        self.assertEqual(t.count("La primera alternativa que podemos evaluar es:"), 1)
        self.assertNotIn("ANALISIS PROPIO DE LA MARCA", t)

    def test_el_presupuesto_gana_sobre_el_mismo_analisis_para_todas(self):
        t = self._texto_pdf(am.generar_pdf([marca(), marca("4770309", "OTRA")], "TEXTO COMUN",
                                           texto_presupuesto=po.texto(po.config_efectiva())))
        self.assertNotIn("TEXTO COMUN", t)
        self.assertEqual(t.count("La primera alternativa que podemos evaluar es:"), 1)


if __name__ == "__main__":
    unittest.main()
