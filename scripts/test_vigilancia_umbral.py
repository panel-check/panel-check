"""Pruebas del umbral de parecido propio de cada marca (cartera_marcas.umbral_vigilancia).

Sin base ni servidor: se arma una marca de la cartera en memoria y se compara contra una solicitud nueva.
Ejecutar: cd scripts && python3 -m unittest test_vigilancia_umbral
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import similitud as sim  # noqa: E402
import vigilancia_core as vig  # noqa: E402

AJUSTES = {
    "genericas": frozenset(), "pares": set(), "matriculas": set(),
    "config": {"umbral": 75, "nivel_alta": 90, "nivel_media": 82, "meses_universo": 18},
}
FILA = {"acta": "9000001", "nombre": "KOMUNIKAS", "clase": 35, "cuit": None}


def marca(**extra):
    m = {"acta": "1000001", "cliente_id": 1, "denominacion": "KOMUNIKA", "nombres": ["KOMUNIKA"], "clase": 35,
         "tipo": "D", "vigilar_todas_clases": False, "terminos_vigilancia": None, "cuit": None, "cliente_cuit": None}
    m.update(extra)
    return m


def puntaje_base():
    return sim.puntaje_marcas("KOMUNIKA", 35, "KOMUNIKAS", 35, frozenset(), set(), False)["puntaje"]


class UmbralPorMarca(unittest.TestCase):
    def evaluar(self, m):
        return vig._evaluar_fila(FILA, [m], AJUSTES, {}, set())

    def test_el_parecido_de_prueba_esta_entre_el_umbral_general_y_100(self):
        self.assertGreaterEqual(puntaje_base(), AJUSTES["config"]["umbral"])
        self.assertLess(puntaje_base(), 100)

    def test_sin_umbral_propio_usa_el_general(self):
        self.assertEqual(len(self.evaluar(marca())), 1)

    def test_umbral_propio_mas_alto_deja_de_alertar(self):
        self.assertEqual(self.evaluar(marca(umbral_vigilancia=puntaje_base() + 1)), [])

    def test_umbral_propio_igual_al_puntaje_alerta(self):
        self.assertEqual(len(self.evaluar(marca(umbral_vigilancia=puntaje_base()))), 1)

    def test_umbral_propio_mas_bajo_que_el_general_alerta_donde_el_general_no(self):
        fila = dict(FILA, nombre="KOMPANIA")   # parecido 74: justo debajo del umbral general (75)
        base = sim.puntaje_marcas("KOMUNIKA", 35, fila["nombre"], 35, frozenset(), set(), False)["puntaje"]
        self.assertLess(base, AJUSTES["config"]["umbral"])
        self.assertEqual(vig._evaluar_fila(fila, [marca()], AJUSTES, {}, set()), [])
        self.assertEqual(len(vig._evaluar_fila(fila, [marca(umbral_vigilancia=base)], AJUSTES, {}, set())), 1)


class FirmaDeCartera(unittest.TestCase):
    def test_sin_umbral_propio_la_firma_no_cambia(self):
        # Para no volver a comparar toda la cartera cuando se despliega la columna nueva.
        self.assertEqual(vig.firma_cartera(marca()), vig.firma_cartera(marca(umbral_vigilancia=None)))
        self.assertFalse(vig.firma_cartera(marca()).endswith("|U"))

    def test_cambiar_el_umbral_cambia_la_firma(self):
        # Así la próxima corrida vuelve a comparar esa marca contra las solicitudes abiertas.
        self.assertNotEqual(vig.firma_cartera(marca()), vig.firma_cartera(marca(umbral_vigilancia=60)))
        self.assertNotEqual(vig.firma_cartera(marca(umbral_vigilancia=60)), vig.firma_cartera(marca(umbral_vigilancia=90)))


if __name__ == "__main__":
    unittest.main()
