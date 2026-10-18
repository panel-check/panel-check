"""
Pruebas de los dos mails de vista a un lead (panel/mails_core.py), sin red ni base. Correr con:
    cd scripts && python3 -m unittest test_mails_vistas -v
"""

import os
import re
import sys
import unittest
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import mails_core as mc  # noqa: E402

DATOS = {"nombre": "Tomás", "marca": "HUELLA CORAL", "acta": "4798128", "codigo": "AbC123"}


def render(clave, **extra):
    cat = mc.CATALOGO[clave]
    return mc.render_prospecto(cat["asunto"], cat["cuerpo"], {**DATOS, **extra}, cat["encabezado"])


def texto_de_whatsapp(html_):
    href = next(h for h in re.findall(r'href="([^"]+)"', html_) if "wa.me/5491155890784?text=" in h)
    return unquote(parse_qs(urlparse(href.replace("&amp;", "&")).query, keep_blank_values=True)["text"][0])


class MailsDeVista(unittest.TestCase):
    CLAVES = ("prospecto_vista_logo", "prospecto_vista_fondo")

    def test_estan_en_el_catalogo_como_plantillas_de_prospectos_editables(self):
        for k in self.CLAVES:
            cat = mc.CATALOGO[k]
            self.assertEqual((cat["grupo"], cat["cuenta"], cat["editable"]), ("prospectos", "prospectos", True))
            self.assertLessEqual(len(cat["encabezado"]), 60)
            # la configuración por defecto pasa la validación del panel
            mc.validar_config(k, {x: cat[x] for x in ("cuenta", "remitente", "responder_a", "destinatarios", "asunto", "cuerpo", "encabezado")})

    def test_asuntos(self):
        self.assertEqual(render("prospecto_vista_logo")[0], "Tu solicitud de marca recibió una vista del INPI – cómo avanzar")
        self.assertEqual(render("prospecto_vista_fondo")[0], "Tu marca recibió una Vista del INPI (Estudio de Fondo)")

    def test_el_subtitulo_va_arriba_con_el_estilo_de_subtitulo(self):
        for k, sub in (("prospecto_vista_logo", "Tu marca fue observada y requiere una corrección formal"),
                       ("prospecto_vista_fondo", "Tenés 30 días para responder la Vista publicada en el Boletín")):
            _, html_, texto = render(k)
            self.assertIn(sub, html_)
            self.assertIn(f"border-bottom:2px solid {mc.COLOR_DORADO}", html_)
            self.assertLess(html_.index(sub), html_.index("Hola Tomás"))
            self.assertTrue(texto.startswith(sub.upper()))

    def test_el_boton_de_whatsapp_sale_igual_que_el_de_oposicion(self):
        _, html_, _ = render("prospecto_vista_logo")
        self.assertIn("Quiero asesorarme sobre mi vista", html_)
        self.assertEqual(texto_de_whatsapp(html_), "Hola Pamela, recibí tu mail sobre la vista de mi marca HUELLA CORAL, "
                                                    "acta 4798128, y quiero asesorarme. Mi nombre es ")
        _, html_, _ = render("prospecto_vista_fondo")
        self.assertIn("Quiero asesorarme sobre mi Vista de Fondo", html_)
        self.assertEqual(texto_de_whatsapp(html_), "Hola Pamela, recibí tu mail sobre la Vista de Fondo de mi marca HUELLA CORAL, "
                                                    "acta 4798128, y quiero asesorarme. Mi nombre es ")
        # botón principal: relleno azul, sin el borde de los secundarios
        self.assertIn("background:#133465", html_)
        self.assertNotIn("border:2px solid #133465", html_)

    def test_los_plazos_del_texto(self):
        self.assertIn("10 días hábiles administrativos", render("prospecto_vista_logo")[2])
        self.assertIn("Resolución P-357/13", render("prospecto_vista_logo")[2])
        self.assertIn("30 días corridos", render("prospecto_vista_fondo")[2])
        self.assertIn("Ley 22.362", render("prospecto_vista_fondo")[2])

    def test_las_listas_salen_como_listas(self):
        self.assertEqual(render("prospecto_vista_logo")[1].count("<li"), 4)
        self.assertEqual(render("prospecto_vista_fondo")[1].count("<li"), 3)

    def test_titular_empresa_sin_nombre_de_pila_saluda_sin_coma_suelta(self):
        _, html_, texto = render("prospecto_vista_logo", nombre="")
        self.assertIn("Hola,", texto)
        self.assertNotIn("Hola ,", texto)
        self.assertNotIn("{{", html_)

    def test_solo_usan_variables_que_existen(self):
        for k in self.CLAVES:
            usadas = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", mc.CATALOGO[k]["asunto"] + mc.CATALOGO[k]["cuerpo"]))
            self.assertTrue(usadas <= set(mc.VARIABLES_PROSPECTO), usadas - set(mc.VARIABLES_PROSPECTO))

    def test_llevan_la_linea_de_baja(self):
        for k in self.CLAVES:
            self.assertIn("respondé BAJA", render(k)[2])

    def test_mails_mandados_por_fuera_del_panel_se_clasifican_por_el_asunto(self):
        self.assertEqual(mc.tipo_por_asunto("Tu marca recibió una Vista del INPI (Estudio de Fondo)", "prospectos"), "prospecto_vista_fondo")
        self.assertEqual(mc.tipo_por_asunto("Tu solicitud de marca recibió una vista del INPI – cómo avanzar", "prospectos"), "prospecto_vista_logo")
        self.assertEqual(mc.tipo_por_asunto("Tu marca X recibió una oposición: qué significa y cómo seguir", "prospectos"), "prospecto_oposicion")


if __name__ == "__main__":
    unittest.main()
