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

    def test_sin_tildes_ni_mayusculas(self):
        self.assertEqual(se.sector_de("DIVISION PUBLICACION"), "publicacion")
        self.assertEqual(se.sector_de("division resolucion"), "resoluciones")

    def test_desconocido(self):
        self.assertIsNone(se.sector_de("Mesa de Entradas"))
        self.assertIsNone(se.sector_de("Estudio de Forma"))  # «estudio» sin «fondo» no alcanza
        self.assertIsNone(se.sector_de(""))
        self.assertIsNone(se.sector_de(None))

    def test_catalogo_completo(self):
        for clave in se.ORDEN:
            c = se.CATALOGO[clave]
            self.assertTrue(c["nombre"] and c["que_es"] and c["que_sigue"])
        self.assertFalse(se.CATALOGO["notificaciones"]["mail"])


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


if __name__ == "__main__":
    unittest.main()
