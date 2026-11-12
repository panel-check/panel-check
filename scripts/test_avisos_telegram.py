"""Pruebas de los avisos por Telegram a cada tema (scripts/avisos_telegram.py y el aviso a Sistema de
reporte_corrida.py), sin red ni base. cd scripts && python3 -m unittest test_avisos_telegram"""

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

import avisos_telegram as at
import reporte_corrida as rc

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class TestResumen(unittest.TestCase):
    def test_una_vez_por_dia_desde_las_7_30(self):
        temprano = dt.datetime(2026, 11, 13, 7, 0, tzinfo=TZ)
        hora = dt.datetime(2026, 11, 13, 7, 35, tzinfo=TZ)
        self.assertFalse(at.toca_resumen(None, temprano))
        self.assertTrue(at.toca_resumen("2026-11-12", hora))
        self.assertFalse(at.toca_resumen("2026-11-13", hora))
        self.assertTrue(at.toca_resumen("2026-11-13", hora, forzar=True))


class TestLineas(unittest.TestCase):
    def test_oposicion(self):
        txt = at.armar_oposicion({"acta": "4797001", "denominacion": "", "denominacion_inpi": "ACME <SA>",
                                  "clase": 35, "boletin": "11150", "estado_oposicion": "sin_notificar",
                                  "oposicion_fecha_vencimiento": dt.date(2026, 12, 1), "con_email": True,
                                  "contactado": False})
        self.assertIn("ACME &lt;SA&gt;", txt)
        self.assertIn("clase 35", txt)
        self.assertIn("sin notificar", txt)
        self.assertIn("vence 01/12", txt)
        self.assertIn("q=4797001", txt)

    def test_vista(self):
        txt = at.armar_vista({"acta": "1", "denominacion": "X", "clase": 9, "vista_tipo": "fondo",
                              "vista_motivo": "NIKE", "vista_estado": "sin_notificar", "vence": None,
                              "con_email": False, "contactado": True})
        for parte in ("vista de fondo", "«NIKE»", "sin notificar", "sin email", "ya contactado"):
            self.assertIn(parte, txt)

    def test_lista_larga_se_corta(self):
        txt = at.lista(list(range(40)), str, "titulo")
        self.assertIn("y 15 más", txt)


class TestAvisoSistema(unittest.TestCase):
    BASE = {"nombre": "Oposiciones", "url": "https://x/runs/1", "falla": "revisar › Revisar", "error": ""}

    def test_bien_no_avisa(self):
        self.assertIsNone(rc.aviso_sistema({**self.BASE, "conclusion": "success"}))

    def test_falla_comun(self):
        txt = rc.aviso_sistema({**self.BASE, "conclusion": "failure"})
        self.assertIn("FALLÓ", txt)
        self.assertIn("Corridas", txt)

    def test_bloqueo_masivo(self):
        txt = rc.aviso_sistema({**self.BASE, "conclusion": "failure",
                                "error": "::error::BLOQUEO MASIVO DE INPI en 'Oposiciones': 12 consultas seguidas bloqueadas"})
        self.assertIn("bloqueando las consultas en forma masiva", txt)
        self.assertIn("12 consultas seguidas", txt)


if __name__ == "__main__":
    unittest.main()
