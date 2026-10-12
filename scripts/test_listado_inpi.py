"""
Pruebas de panel/listado_inpi.py (sin red y sin base). Correr con:
    cd scripts && python3 -m unittest test_listado_inpi -v

INPI se simula reemplazando inpi_lead.traer_listado; la base, con una lista en memoria
(el SQL real se probó aparte contra Postgres). El reloj se fija reemplazando
listado_inpi.datetime.
"""

import os
import sys
import time
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import inpi_lead  # noqa: E402
import listado_inpi as L  # noqa: E402

ART = L.ART


def art(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ART)


# 2026-10-07 es miércoles.
class Franja(unittest.TestCase):
    def test_miercoles_dentro_de_la_franja(self):
        ini, fin = L.ultima_franja(art(2026, 10, 7, 8, 30))
        self.assertEqual((ini, fin), (art(2026, 10, 7, 7), art(2026, 10, 7, 11)))

    def test_miercoles_antes_de_las_7_es_la_semana_anterior(self):
        ini, _ = L.ultima_franja(art(2026, 10, 7, 6, 59))
        self.assertEqual(ini, art(2026, 9, 30, 7))

    def test_otros_dias_apuntan_al_ultimo_miercoles(self):
        for d in (8, 9, 10, 11, 12, 13):  # jueves a martes
            ini, _ = L.ultima_franja(art(2026, 10, d, 12))
            self.assertEqual(ini, art(2026, 10, 7, 7), d)

    def test_acepta_utc(self):
        ini, _ = L.ultima_franja(datetime(2026, 10, 7, 11, 30, tzinfo=timezone.utc))  # 8:30 ART
        self.assertEqual(ini, art(2026, 10, 7, 7))


class CuandoActualizar(unittest.TestCase):
    def test_nunca_se_actualizo(self):
        self.assertTrue(L.necesita_actualizar(art(2026, 10, 8, 12), None))

    def test_dentro_de_la_franja_cada_15_minutos(self):
        ahora = art(2026, 10, 7, 8, 0)
        self.assertFalse(L.necesita_actualizar(ahora, art(2026, 10, 7, 7, 55)))
        self.assertTrue(L.necesita_actualizar(ahora, art(2026, 10, 7, 7, 40)))

    def test_primera_vez_de_la_franja_despues_de_un_chequeo_de_ayer(self):
        self.assertTrue(L.necesita_actualizar(art(2026, 10, 7, 7, 1), art(2026, 10, 6, 20)))

    def test_chequeo_final_al_cerrar_la_franja(self):
        ahora = art(2026, 10, 7, 11, 30)
        self.assertTrue(L.necesita_actualizar(ahora, art(2026, 10, 7, 10, 50)))   # falta el final
        self.assertFalse(L.necesita_actualizar(ahora, art(2026, 10, 7, 11, 5)))   # ya hecho

    def test_resto_de_la_semana_no_consulta(self):
        ultimo = art(2026, 10, 7, 11, 5)
        for d in (8, 9, 10, 11, 12, 13):
            self.assertFalse(L.necesita_actualizar(art(2026, 10, d, 15), ultimo), d)

    def test_si_el_panel_estuvo_apagado_en_la_franja_se_pone_al_dia(self):
        # último chequeo el martes; el jueves nadie lo hizo durante el miércoles
        self.assertTrue(L.necesita_actualizar(art(2026, 10, 8, 9), art(2026, 10, 6, 12)))
        # o se hizo a las 6:00 del miércoles (antes de que INPI publique)
        self.assertTrue(L.necesita_actualizar(art(2026, 10, 8, 9), art(2026, 10, 7, 6)))


class FlujoConBaseEnMemoria(unittest.TestCase):
    def setUp(self):
        self.db = {"filas": {}, "ultimo": None, "hist": None}
        self.pedidos = []
        self.ahora = art(2026, 10, 8, 12)  # jueves

        def leer(conexion):
            return ([{"numero": n, "fecha": f} for n, f in self.db["filas"].items()],
                    self.db["ultimo"], self.db["hist"])

        def guardar(conexion, boletines, historial_desde=None):
            for b in boletines:
                self.db["filas"][b["numero"]] = L._fecha_inpi(b["fecha"])
            self.db["ultimo"] = self.ahora
            if historial_desde:
                self.db["hist"] = date.fromisoformat(historial_desde)

        def traer(timeout, desde, estricto=False):
            self.pedidos.append((desde, estricto))
            if getattr(self, "falla", None):
                raise RuntimeError(self.falla)
            return [{"numero": "11133", "fecha": "07/10/2026"}, {"numero": "11088", "fecha": "29/07/2026"}]

        ahora = self

        class Reloj(datetime):
            @classmethod
            def now(cls, tz=None):
                return ahora.ahora

        self.originales = (L._leer, L._guardar, inpi_lead.traer_listado, L.datetime)
        L._leer, L._guardar, inpi_lead.traer_listado, L.datetime = leer, guardar, traer, Reloj
        L._en_curso = False
        L._ultimo_error.update(texto=None, cuando=None)

    def tearDown(self):
        L._leer, L._guardar, inpi_lead.traer_listado, L.datetime = self.originales

    def esperar(self):
        for _ in range(100):
            if not L._en_curso:
                return
            time.sleep(0.02)
        self.fail("el hilo no terminó")

    def test_primera_vez_arma_el_historial_una_sola_vez(self):
        filas, cargando, error, _ = L.listado(None)
        self.assertEqual((filas, cargando, error), ([], True, None))
        self.esperar()
        self.assertEqual(self.pedidos, [(inpi_lead.BOLETINES_DESDE, True)])
        self.assertEqual(self.db["hist"], date.fromisoformat(inpi_lead.BOLETINES_DESDE))
        self.assertEqual(self.db["filas"]["11088"], date(2026, 7, 29))
        # un jueves, con el historial guardado, abrir la pantalla muchas veces no consulta a INPI
        for _ in range(5):
            filas, cargando, error, _ = L.listado(None)
            self.esperar()
        self.assertEqual(len(filas), 2)
        self.assertEqual((cargando, error), (False, None))
        self.assertEqual(len(self.pedidos), 1)

    def test_el_miercoles_solo_pide_los_ultimos_dos_meses(self):
        self.db.update(filas={"11088": date(2026, 7, 29)}, ultimo=art(2026, 10, 6, 20),
                       hist=date.fromisoformat(inpi_lead.BOLETINES_DESDE))
        self.ahora = art(2026, 10, 7, 8)
        L.listado(None)
        self.esperar()
        self.assertEqual(self.pedidos, [("2026-09-01", True)])

    def test_si_inpi_falla_avisa_y_no_insiste_enseguida(self):
        self.falla = "bloqueado"
        L.listado(None)
        self.esperar()
        filas, cargando, error, _ = L.listado(None)
        self.esperar()
        self.assertEqual((filas, cargando, error), ([], False, "bloqueado"))
        self.assertEqual(len(self.pedidos), 1)  # no reintenta antes de 5 minutos
        self.ahora += timedelta(minutes=6)
        L.listado(None)
        self.esperar()
        self.assertEqual(len(self.pedidos), 2)

    def test_boton_actualizar(self):
        self.db.update(filas={"11088": date(2026, 7, 29)}, ultimo=self.ahora,
                       hist=date.fromisoformat(inpi_lead.BOLETINES_DESDE))
        self.assertEqual(L.actualizar_ahora(None), {"ok": True, "error": None})
        self.assertEqual(self.pedidos, [("2026-09-01", True)])
        self.assertIn("11133", self.db["filas"])

    def test_boton_actualizar_con_error(self):
        self.db.update(hist=date.fromisoformat(inpi_lead.BOLETINES_DESDE))
        self.falla = "timeout"
        r = L.actualizar_ahora(None)
        self.assertFalse(r["ok"])
        self.assertIn("timeout", r["error"])

    def test_boton_actualizar_con_otra_actualizacion_en_curso(self):
        L._en_curso = True
        r = L.actualizar_ahora(None)
        self.assertFalse(r["ok"])
        self.assertEqual(self.pedidos, [])
        L._en_curso = False


class Fechas(unittest.TestCase):
    def test_fecha_inpi(self):
        self.assertEqual(L._fecha_inpi("29/07/2026"), date(2026, 7, 29))
        self.assertEqual(L._fecha_inpi("5/1/2024"), date(2024, 1, 5))
        self.assertIsNone(L._fecha_inpi(None))
        self.assertIsNone(L._fecha_inpi("basura"))


if __name__ == "__main__":
    unittest.main()
