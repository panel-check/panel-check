"""
Pruebas de la plantilla «Confirmación de desistimiento por oposición» (panel/mails_core.py), sin red ni base.
    cd scripts && python3 -m unittest test_mail_desistimiento -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import mails_core as mc  # noqa: E402

K = "prospecto_desistimiento_oposicion"


class CursorFalso:
    def __init__(self, fila):
        self.fila = fila

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return self.fila


def fila(**extra):
    base = {"acta": "4790681", "clase": 36, "titular": "PEREZ TOMAS", "denominacion": "Más", "denominacion_inpi": "MÁS",
            "tipo": "M", "oponente_nombre": "", "marca_oponente_denominacion": "", "marca_oponente_numero_registro": "",
            "fecha": None}
    return {**base, **extra}


def render(datos):
    cat = mc.CATALOGO[K]
    d = mc.completar_corchetes(K, datos)
    return mc.render_prospecto(cat["asunto"], cat["cuerpo"], d, cat["encabezado"], simple=mc.es_simple(K))


class PlantillaDesistimiento(unittest.TestCase):
    def test_va_despues_de_recibio_una_vista_en_el_desplegable(self):
        claves = list(mc.CATALOGO)
        self.assertEqual(claves.index(K), claves.index("prospecto_vista") + 1)
        cat = mc.CATALOGO[K]
        self.assertEqual((cat["grupo"], cat["editable"], cat["requiere_oposicion"]), ("prospectos", True, True))
        self.assertEqual(cat["nombre"], "Confirmación de desistimiento por oposición")
        mc.validar_config(K, cat)   # la configuración por defecto pasa la validación del panel

    def test_completa_nombre_solicitud_oponente_y_registro(self):
        cur = CursorFalso(fila(oponente_nombre="ACME SA", marca_oponente_denominacion="MAS+",
                               marca_oponente_numero_registro="2.900.123"))
        asunto, _html, texto = render(mc.datos_de_marca(cur, "4790681"))
        self.assertEqual(asunto, "Marca MÁS – Oposiciones recibidas – Necesitamos tu confirmación")
        self.assertTrue(texto.startswith("Tomás, buenos días."))
        self.assertIn("la marca MÁS (clase 36, acta 4790681, MIXTA), que recibió [una oposición / N oposiciones] de ACME SA, "
                      "titular de la marca MAS+ (Registro N° 2.900.123).", texto)
        self.assertIn("ACME SA podría intimarte", texto)
        # lo que decide el estudio queda siempre entre corchetes
        for c in ("[bajas / moderadas / altas]", "[motivo breve]", "[fecha]"):
            self.assertIn(c, texto)
        self.assertIn("☐ Acepto desistir de la solicitud de la marca MÁS (clase 36, acta 4790681, MIXTA).", texto)
        self.assertIn("☐ No deseo desistir y prefiero [negociar con la oponente", texto)

    def test_lo_que_no_esta_en_el_panel_queda_entre_corchetes(self):
        _a, _h, texto = render(mc.datos_de_marca(CursorFalso(fila(titular="ACME SRL")), "4790681"))
        self.assertIn("de [oponente], titular de la marca [marca del oponente] (Registro N° [número]).", texto)
        sin_nombre = mc.completar_corchetes(K, {"titular": "", "marca": "X", "acta": "1"})
        self.assertEqual(sin_nombre["destinatario"], "[nombre]")
        self.assertEqual(sin_nombre["solicitud"], "[MARCA (clase N, acta N, TIPO)]")

    def test_estilo_simple_sin_linea_de_baja(self):
        _a, html_, texto = render(mc.datos_de_marca(CursorFalso(fila()), "4790681"))
        self.assertNotIn(mc.PIE_BAJA, texto)
        self.assertIn("<strong>MÁS (clase 36, acta 4790681, MIXTA)</strong>", html_)
        self.assertIn("<ul", html_)

    def test_otras_plantillas_no_cambian(self):
        self.assertIsNone(mc.CATALOGO["prospecto_oposicion"].get("corchetes"))
        self.assertEqual(mc.completar_corchetes("prospecto_oposicion", {"oponente": ""}), {"oponente": ""})

    def test_tipo_por_asunto(self):
        self.assertEqual(mc.tipo_por_asunto("Marca MÁS – Oposiciones recibidas – Necesitamos tu confirmación", "prospectos"), K)

    def test_texto_solicitud(self):
        self.assertEqual(mc.texto_solicitud({"denominacion_inpi": "MÁS", "clase": 36, "acta": "4790681", "tipo": "MIXTA"}),
                         "MÁS (clase 36, acta 4790681, MIXTA)")
        self.assertEqual(mc.texto_solicitud({"denominacion": "", "clase": 36, "acta": "1", "tipo": "F"}), "")


if __name__ == "__main__":
    unittest.main()
