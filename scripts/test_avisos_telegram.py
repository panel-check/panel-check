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


class VigilanciaAltas(unittest.TestCase):
    """Alertas ALTAS de la vigilancia al tema «Vigilancia marcaria» (13/11/2026)."""

    def fila(self, **k):
        import datetime as dt
        base = {"id": 1, "acta_cliente": "100", "acta_nueva": "4810369", "denominacion_nueva": "APEX PERFORMANCE",
                "clase_nueva": 5, "titular_nuevo": "COOPERATIVA X", "puntaje": 78, "motivos": "comparten «APEX»",
                "ia_veredicto": "confundible", "ia_razon": "APEX es dominante en ambas.",
                "actas_cliente": [{"acta": "100", "clase": 5}, {"acta": "101", "clase": 32}],
                "denominacion_cliente": "APEX LAB", "clase_cliente": 5, "cliente": "Cliente <SA>", "contratada": False,
                "publicacion": None, "detectada_en": dt.datetime.now(dt.timezone.utc)}
        base.update(k)
        return base

    def test_mensaje_con_veredicto_clases_y_deteccion_temprana(self):
        t = at.armar_alerta_alta(self.fila())
        self.assertIn("APEX LAB", t)
        self.assertIn("clase 5, 32", t)
        self.assertIn("Confundible", t)
        self.assertIn("Cliente &lt;SA&gt;", t)  # escapado para HTML
        self.assertIn("sin vigilancia contratada", t)
        self.assertIn("Todavía no se publicó", t)
        self.assertIn("detectada hoy", t)

    def test_publicada_muestra_plazo_y_dias_ganados(self):
        import datetime as dt
        hoy = dt.datetime.now(at.TZ).date()
        f = self.fila(publicacion=hoy - dt.timedelta(days=2),
                      detectada_en=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=12))
        t = at.armar_alerta_alta(f)
        self.assertIn("Plazo para oponerse hasta el", t)
        self.assertIn("(28 días)", t)
        self.assertIn("detectada 10 días antes de la publicación", t)

    def test_sin_veredicto_dice_que_falta_la_ia(self):
        t = at.armar_alerta_alta(self.fila(ia_veredicto=None))
        self.assertIn("todavía sin revisar por la IA", t)


if __name__ == "__main__":
    unittest.main()
