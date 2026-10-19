"""
Pruebas de probar_vistas_preboletin.py (sin red ni base). Correr con:
    cd scripts && python3 -m unittest test_probar_vistas -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import probar_vistas_preboletin as pv  # noqa: E402
from probar_vistas_preboletin import _estado_vista, clasificar  # noqa: E402


def fila(indice, fecha, referencia="-"):
    return {"Indice": indice, "Fecha": fecha, "Referencia": referencia}


RECIBO = fila("Recibo de Ingreso", "28/09/2026", "Solicitud de marcas")


class Clasificar(unittest.TestCase):
    def test_en_examen(self):
        self.assertEqual(clasificar([RECIBO, fila("Formulario", "29/09/2026")])[0], "en_examen")

    def test_orden_de_publicacion_sin_vista(self):
        self.assertEqual(clasificar([RECIBO, fila("Orden de Publicación", "13/10/2026")])[0], "orden_publicacion")

    def test_publicada(self):
        self.assertEqual(clasificar([RECIBO, fila("Hoja Publicacion", "14/10/2026", "11124")])[0], "publicada")

    def test_vista_sin_contestar(self):
        self.assertEqual(clasificar([RECIBO, fila("Vista de Marcas", "05/10/2026")])[0], "vista_pendiente")

    def test_vista_contestada_sin_orden_sigue_mirandose(self):
        cat = clasificar([RECIBO, fila("Vista de Marcas", "05/10/2026"), fila("Contestación de Vista", "08/10/2026")])[0]
        self.assertEqual(cat, "vista_resuelta")

    def test_vista_seguida_de_orden_de_publicacion_es_terminal(self):
        cat, _, vista = clasificar([RECIBO, fila("Vista de Marcas", "05/10/2026"), fila("Orden de Publicación", "12/10/2026")])
        self.assertEqual((cat, vista), ("orden_publicacion", "2026-10-05"))

    def test_una_orden_anterior_a_la_vista_no_la_resuelve(self):
        cat = clasificar([RECIBO, fila("Orden de Publicación", "01/10/2026"), fila("Vista de Marcas", "05/10/2026")])[0]
        self.assertEqual(cat, "vista_pendiente")


class EstadoVista(unittest.TestCase):
    def est(self, **kw):
        base = {"estado": "vista_pendiente", "notificacion": "2026-10-07", "vencimiento": "2026-11-21"}
        base.update(kw)
        return base

    def test_en_plazo(self):
        self.assertEqual(_estado_vista(self.est(), "2026-10-17"), "notificada_en_plazo")

    def test_cuenta_el_vencimiento_mas_cercano(self):
        # INPI marca el 21/11, pero el texto dice 28/10 (vista formal): hoy es 30/10
        self.assertEqual(_estado_vista(self.est(), "2026-10-30", "2026-10-28"), "plazo_vencido")
        self.assertEqual(_estado_vista(self.est(), "2026-10-17", "2026-10-28"), "notificada_en_plazo")

    def test_sin_notificar(self):
        self.assertEqual(_estado_vista(self.est(notificacion=None, vencimiento=None), "2026-10-17"), "sin_notificar")

    def test_resuelta_y_con_apoderado(self):
        self.assertEqual(_estado_vista({"estado": "sin_oposicion"}, "2026-10-17"), "resuelta")
        self.assertEqual(_estado_vista({"estado": "con_apoderado"}, "2026-10-17"), "con_apoderado")
        self.assertIsNone(_estado_vista({"estado": "contestada"}, "2026-10-17"))


class AvisoDeVistasSinEntender(unittest.TestCase):
    ITEMS = [{"acta": "4800811", "marca": "ANIMA", "tipo": "desconocido", "problemas": ["no se reconoce el tipo de vista"], "texto": "x"}]

    def test_sin_vistas_no_manda_nada(self):
        with mock.patch("requests.post") as post:
            pv.enviar_aviso_vistas([])
        post.assert_not_called()

    def test_manda_un_solo_mail_con_todas(self):
        env = {"RESEND_API_KEY": "re_x", "RESEND_FROM": "Avisos <a@b.com>", "NOTIFICAR_A": "yo@b.com"}
        with mock.patch.dict(os.environ, env), \
             mock.patch("mails_core.preparar", side_effect=RuntimeError("sin base")), \
             mock.patch("requests.post") as post:
            pv.enviar_aviso_vistas(self.ITEMS + self.ITEMS)
        self.assertEqual(post.call_count, 1)
        enviado = post.call_args.kwargs["json"]
        self.assertEqual(enviado["to"], ["yo@b.com"])
        self.assertIn("2 vistas", enviado["subject"])

    def test_sin_clave_no_rompe(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("mails_core.preparar", return_value={"api_key": None, "env_key": "RESEND_API_KEY", "remitente": "a", "destinatarios": ["b"], "responder_a": None}), \
             mock.patch("requests.post") as post:
            pv.enviar_aviso_vistas(self.ITEMS)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
