"""
Pruebas del mail de vista a un lead (panel/mails_core.py), sin red ni base. Correr con:
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


class MailDeVista(unittest.TestCase):
    K = "prospecto_vista"

    def test_hay_un_solo_mail_de_vista_para_todos_los_tipos(self):
        self.assertEqual([k for k in mc.CATALOGO if k.startswith("prospecto_vista")], [self.K])

    def test_esta_en_el_catalogo_como_plantilla_de_prospectos_editable(self):
        cat = mc.CATALOGO[self.K]
        self.assertEqual((cat["grupo"], cat["cuenta"], cat["editable"]), ("prospectos", "prospectos", True))
        self.assertEqual(cat["encabezado"], "VISTA DEL INPI – OBSERVACIÓN EN TU SOLICITUD DE MARCA")
        self.assertLessEqual(len(cat["encabezado"]), 60)
        # la configuración por defecto pasa la validación del panel
        mc.validar_config(self.K, {x: cat[x] for x in ("cuenta", "remitente", "responder_a", "destinatarios", "asunto", "cuerpo", "encabezado")})

    def test_asunto(self):
        self.assertEqual(render(self.K)[0], "Tu solicitud de marca recibió una vista del INPI – cómo avanzar")

    def test_el_subtitulo_va_arriba_con_el_estilo_de_subtitulo(self):
        sub = "Tu solicitud de registro recibió una observación del INPI que debe ser respondida para continuar con el trámite."
        _, html_, texto = render(self.K)
        self.assertIn(sub, html_)
        self.assertIn(f"border-bottom:2px solid {mc.COLOR_DORADO}", html_)
        self.assertLess(html_.index(sub), html_.index("Hola Tomás"))
        self.assertTrue(texto.startswith(sub.upper()))

    def test_saluda_con_el_nombre_y_nombra_la_marca(self):
        _, html_, texto = render(self.K)
        self.assertIn("Hola Tomás,", texto)
        self.assertIn("tu solicitud de registro HUELLA CORAL presenta una vista administrativa", texto)

    def test_tiene_las_tres_preguntas(self):
        _, html_, _ = render(self.K)
        for t in ("¿Qué significa que tu solicitud tenga una vista?", "¿Qué plazo tenés para responder?", "¿Cómo podemos ayudarte?"):
            self.assertIn(t, html_)

    def test_no_promete_un_plazo_fijo_porque_sirve_para_todos_los_tipos(self):
        _, _, texto = render(self.K)
        self.assertIn("varían según el tipo de observación", texto)
        self.assertNotIn("30 días", texto)
        self.assertNotIn("10 días", texto)

    def test_el_boton_de_whatsapp_sale_igual_que_el_de_oposicion(self):
        _, html_, _ = render(self.K)
        self.assertIn("Escribinos para recibir asesoramiento sobre tu solicitud", html_)
        self.assertEqual(texto_de_whatsapp(html_), "Hola Pamela, recibí tu mail sobre la vista de mi marca HUELLA CORAL, "
                                                    "acta 4798128, y quiero asesorarme. Mi nombre es ")
        # botón principal: relleno azul, sin el borde de los secundarios
        self.assertIn("background:#133465", html_)
        self.assertNotIn("border:2px solid #133465", html_)

    def test_la_lista_sale_como_lista(self):
        self.assertEqual(render(self.K)[1].count("<li"), 4)

    def test_titular_empresa_sin_nombre_de_pila_saluda_sin_coma_suelta(self):
        _, html_, texto = render(self.K, nombre="")
        self.assertIn("Hola,", texto)
        self.assertNotIn("Hola ,", texto)
        self.assertNotIn("{{", html_)

    def test_solo_usa_variables_que_existen(self):
        usadas = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", mc.CATALOGO[self.K]["asunto"] + mc.CATALOGO[self.K]["cuerpo"]))
        self.assertTrue(usadas <= set(mc.VARIABLES_PROSPECTO), usadas - set(mc.VARIABLES_PROSPECTO))

    def test_lleva_la_linea_de_baja(self):
        self.assertIn("respondé BAJA", render(self.K)[2])

    def test_mails_mandados_por_fuera_del_panel_se_clasifican_por_el_asunto(self):
        self.assertEqual(mc.tipo_por_asunto("Tu solicitud de marca recibió una vista del INPI – cómo avanzar", "prospectos"), self.K)
        self.assertEqual(mc.tipo_por_asunto("Tu marca recibió una Vista del INPI (Estudio de Fondo)", "prospectos"), self.K)
        self.assertEqual(mc.tipo_por_asunto("Tu marca X recibió una oposición: qué significa y cómo seguir", "prospectos"), "prospecto_oposicion")

    def test_el_historial_viejo_sigue_teniendo_nombre(self):
        claves = {t["clave"] for t in mc.tipos_de_mail()}
        self.assertTrue({"prospecto_vista", "prospecto_vista_logo", "prospecto_vista_fondo"} <= claves)


class AvisoVistaSinEntender(unittest.TestCase):
    def test_esta_en_el_catalogo_como_aviso_interno(self):
        cat = mc.CATALOGO["vista_sin_entender"]
        self.assertEqual((cat["grupo"], cat["cuenta"]), ("interno", "interna"))

    def test_se_reconoce_por_el_asunto(self):
        import mails_plantillas as mp
        asunto, html_ = mp.armar_mail_vistas_sin_entender(
            [{"acta": "4800811", "marca": "ANIMA <b>", "tipo": "desconocido", "problemas": ["no se encontró el plazo"], "texto": "Dese vista…"}],
            "https://ejemplo/run")
        self.assertEqual(mc.tipo_por_asunto(asunto, "interna"), "vista_sin_entender")
        self.assertIn("4800811", html_)
        self.assertIn("ANIMA &lt;b&gt;", html_)  # el texto de INPI se escapa
        self.assertIn("https://ejemplo/run", html_)

    def test_la_vista_previa_del_panel_anda(self):
        import mails_plantillas as mp
        asunto, html_ = mp.ejemplo("vista_sin_entender", "https://panel")
        self.assertIn("no supo leer", asunto)


if __name__ == "__main__":
    unittest.main()
