"""Pruebas del catálogo de sectores de la vigilancia de expediente (panel/seguimiento_expediente.py)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import seguimiento_expediente as se  # noqa: E402


class TestSector(unittest.TestCase):
    def test_nombres_reales(self):
        self.assertEqual(se.sector_de("Sector Estudio Fondo 583/25"), "estudio_fondo")
        self.assertEqual(se.sector_de("ESTUDIO DE FONDO IA"), "estudio_fondo")
        self.assertEqual(se.sector_de("División Publicación"), "publicacion")
        self.assertEqual(se.sector_de("División Notificaciones"), "notificaciones")
        self.assertEqual(se.sector_de("División Resoluciones"), "resoluciones")
        self.assertEqual(se.sector_de("Sector Estudio Forma"), "estudio_forma")
        self.assertEqual(se.sector_de("Sector Estudio Forma IA"), "estudio_forma")
        self.assertEqual(se.sector_de("División Análisis de Fondo"), "analisis_fondo")

    def test_sin_tildes_ni_mayusculas(self):
        self.assertEqual(se.sector_de("DIVISION PUBLICACION"), "publicacion")
        self.assertEqual(se.sector_de("division resolucion"), "resoluciones")

    def test_desconocido(self):
        self.assertIsNone(se.sector_de("Mesa de Entradas"))
        self.assertIsNone(se.sector_de("División Renovaciones"))
        self.assertIsNone(se.sector_de("Dirección de Coordinación Operativa Dpto Operativo Archivo"))
        self.assertIsNone(se.sector_de(""))
        self.assertIsNone(se.sector_de(None))

    def test_catalogo_completo(self):
        for clave in se.ORDEN:
            c = se.CATALOGO[clave]
            self.assertTrue(c["nombre"] and c["que_es"] and c["que_sigue"])
        self.assertFalse(se.CATALOGO["notificaciones"]["mail"])
        self.assertFalse(se.CATALOGO["analisis_fondo"]["mail"])  # sin texto validado todavía


class TestCambio(unittest.TestCase):
    def test_primera_lectura_no_es_cambio(self):
        self.assertFalse(se.hubo_cambio((None, None), ("División Publicación", "2026-11-11")))

    def test_cambio_de_sector(self):
        self.assertTrue(se.hubo_cambio(("Sector Estudio Fondo 583/25", "2026-11-12"),
                                       ("División Publicación", "2026-12-20")))

    def test_renombre_misma_fecha_no_es_cambio(self):
        self.assertFalse(se.hubo_cambio(("ESTUDIO DE FONDO IA", "2026-11-12"),
                                        ("Sector Estudio Fondo 583/25", "2026-11-12")))

    def test_vuelve_al_mismo_sector_con_fecha_nueva(self):
        self.assertTrue(se.hubo_cambio(("Sector Estudio Fondo 583/25", "2026-11-12"),
                                       ("Sector Estudio Fondo 583/25", "2027-01-05")))

    def test_desconocido_tambien_detecta_cambio(self):
        self.assertTrue(se.hubo_cambio(("Mesa de Entradas", "2026-11-01"), ("Otro Sector", "2026-11-05")))


class TestEvaluar(unittest.TestCase):
    def exp(self, ub="Sector Estudio Fondo 583/25", fecha="2026-11-12", estado=None, caracter="", matricula=""):
        return {"ubicacion_actual": ub, "ubicacion_fecha": fecha, "estado_tramite": estado,
                "caracter": caracter, "matricula_agente": matricula}

    def test_primera_lectura(self):
        cambios, ev = se.evaluar({}, self.exp())
        self.assertEqual(cambios["sector"], "estudio_fondo")
        self.assertEqual([(e["tipo"], e["aviso"]) for e in ev], [("ubicacion_inicial", "no_corresponde")])

    def test_sin_cambios(self):
        g = {"ubicacion_actual": "Sector Estudio Fondo 583/25", "ubicacion_fecha": "2026-11-12"}
        self.assertEqual(se.evaluar(g, self.exp())[1], [])

    def test_cambio_a_publicacion_queda_para_mail(self):
        g = {"ubicacion_actual": "Sector Estudio Fondo 583/25", "ubicacion_fecha": "2026-11-12"}
        _, ev = se.evaluar(g, self.exp("División Publicación", "2026-12-01"))
        self.assertEqual([(e["tipo"], e["aviso"], e["sector"]) for e in ev], [("ubicacion", "pendiente", "publicacion")])

    def test_notificaciones_no_manda_mail(self):
        g = {"ubicacion_actual": "División Publicación", "ubicacion_fecha": "2026-12-01"}
        _, ev = se.evaluar(g, self.exp("División Notificaciones", "2027-01-05"))
        self.assertEqual(ev[0]["aviso"], "no_corresponde")

    def test_sector_desconocido_es_alerta(self):
        g = {"ubicacion_actual": "Sector Estudio Fondo 583/25", "ubicacion_fecha": "2026-11-12"}
        _, ev = se.evaluar(g, self.exp("Sector Nuevo Raro", "2026-11-20"))
        self.assertEqual(ev[0]["aviso"], "alerta")

    def test_concedida_corta_y_queda_mail(self):
        g = {"ubicacion_actual": "División Resoluciones", "ubicacion_fecha": "2027-01-10"}
        cambios, ev = se.evaluar(g, self.exp("División Resoluciones", "2027-01-10", estado="Concedida"))
        self.assertEqual((cambios["activo"], cambios["motivo_fin"]), (False, "concedida"))
        self.assertEqual([(e["tipo"], e["aviso"]) for e in ev], [("concedida", "pendiente")])

    def test_apoderado_corta_sin_mails(self):
        g = {"ubicacion_actual": "Sector Estudio Fondo 583/25", "ubicacion_fecha": "2026-11-12"}
        cambios, ev = se.evaluar(g, self.exp("División Publicación", "2026-12-01", caracter="Apoderado"))
        self.assertEqual(cambios["motivo_fin"], "apoderado")
        self.assertTrue(all(e["aviso"] == "no_corresponde" for e in ev))

    def test_denegada_es_alerta(self):
        cambios, ev = se.evaluar({"ubicacion_actual": "x", "ubicacion_fecha": "2026-11-01"},
                                 self.exp("x", "2026-11-01", estado="Denegada"))
        self.assertEqual(cambios["motivo_fin"], "denegada")
        self.assertEqual([(e["tipo"], e["aviso"]) for e in ev], [("denegada", "alerta")])

    def test_prueba_nunca_deja_mails_ni_alertas(self):
        g = {"ubicacion_actual": "Sector Estudio Fondo 583/25", "ubicacion_fecha": "2026-11-12"}
        _, ev = se.evaluar(g, self.exp("División Publicación", "2026-12-01"), prueba=True)
        self.assertEqual(ev[0]["aviso"], "no_corresponde")


if __name__ == "__main__":
    unittest.main()
