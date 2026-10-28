"""Pruebas de la vigilancia por concepto (traducciones, sinónimos): similitud.puntaje_conceptos,
su paso por vigilancia_core y la limpieza de la lista que devuelve la IA.

Sin base, sin servidor y sin IA: la lista de conceptos se arma a mano.
Ejecutar: cd scripts && python3 -m unittest test_vigilancia_conceptos
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import conceptos_vigilancia as cv  # noqa: E402
import ia_texto  # noqa: E402
import similitud as sim  # noqa: E402
import vigilancia_core as vig  # noqa: E402

GEN = frozenset({"GROUP", "STORE", "SA"})
AJUSTES = {
    "genericas": GEN, "pares": {(25, 35)}, "matriculas": set(),
    "config": {"umbral": 75, "nivel_alta": 90, "nivel_media": 82, "meses_universo": 18},
}


def marca(**extra):
    m = {"acta": "1000001", "cliente_id": 1, "denominacion": "SOL DE MAYO", "terminos_vigilancia": None,
         "conceptos_vigilancia": "SUN OF MAY, MAY SUN, SUN, SOLEIL", "clase": 25, "tipo": "D",
         "vigilar_todas_clases": False, "cuit": None, "cliente_cuit": None}
    m.update(extra)
    m["nombres"] = [m["denominacion"]]
    m["conceptos"] = sim.separar_conceptos(m["conceptos_vigilancia"])
    return m


def fila(nombre, clase=25, **extra):
    f = {"acta": "9000001", "nombre": nombre, "clase": clase, "cuit": None}
    f.update(extra)
    return f


def alertas(f, m):
    return vig._evaluar_fila(f, [m], AJUSTES, {}, set())


class PuntajeConceptos(unittest.TestCase):
    def test_la_solicitud_es_el_concepto(self):
        p, motivos = sim.puntaje_conceptos(["SUN", "SOLEIL"], "SOL", "Sun", GEN)
        self.assertEqual(p, sim.CONCEPTO_NOMBRE)
        self.assertIn("mismo concepto", motivos[0])
        self.assertIn("SUN", motivos[0])

    def test_el_concepto_es_parte_de_la_solicitud(self):
        self.assertEqual(sim.puntaje_conceptos(["SUN"], "SOL", "SUN COAST", GEN)[0], sim.CONCEPTO_PALABRA)

    def test_concepto_de_varias_palabras_dentro_de_la_solicitud(self):
        self.assertEqual(sim.puntaje_conceptos(["GOLDEN EAGLE"], "AGUILA DORADA", "THE GOLDEN EAGLE CLUB", GEN)[0],
                         sim.CONCEPTO_PALABRA)

    def test_plural_o_errata_del_concepto(self):
        self.assertEqual(sim.puntaje_conceptos(["LION"], "LEON", "LIONS", GEN)[0], sim.CONCEPTO_CASI_NOMBRE)

    def test_sin_relacion_no_puntua(self):
        self.assertEqual(sim.puntaje_conceptos(["SUN", "SOLEIL"], "SOL", "LUNA NUEVA", GEN), (0, []))

    def test_no_matchea_dentro_de_otra_palabra(self):
        # «SUN» no tiene que aparecer dentro de «SUNDAY» ni «ASUNTO».
        self.assertEqual(sim.puntaje_conceptos(["SUN"], "SOL", "ASUNTO", GEN)[0], 0)
        self.assertEqual(sim.puntaje_conceptos(["SUN"], "SOL", "SUNDAY", GEN)[0], 0)

    def test_concepto_generico_no_cuenta(self):
        self.assertEqual(sim.puntaje_conceptos(["STORE"], "TIENDA", "STORE", GEN)[0], 0)

    def test_suena_igual_que_el_concepto(self):
        self.assertEqual(sim.puntaje_conceptos(["CASA"], "HOME", "KASA", GEN)[0], sim.CONCEPTO_NOMBRE)

    def test_misma_clase_es_alerta_media_y_no_alta(self):
        p = sim.ajustar_por_clase(sim.CONCEPTO_NOMBRE, [], 25, 25)["puntaje"]
        self.assertEqual(sim.nivel(p), "media")


class EnLaVigilancia(unittest.TestCase):
    def test_traduccion_misma_clase_alerta(self):
        a = alertas(fila("SUN"), marca())
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["nivel"], "media")
        self.assertIn("mismo concepto que «SOL DE MAYO»", a[0]["motivos"])

    def test_clase_vinculada_resta(self):
        a = alertas(fila("SUN", clase=35), marca())
        self.assertEqual(a[0]["puntaje"], sim.CONCEPTO_NOMBRE - 8)

    def test_clase_sin_relacion_no_alerta(self):
        self.assertEqual(alertas(fila("SUN", clase=9), marca()), [])

    def test_sin_conceptos_no_cambia_nada(self):
        self.assertEqual(alertas(fila("SUN"), marca(conceptos_vigilancia=None)), [])

    def test_el_parecido_escrito_fuerte_gana(self):
        a = alertas(fila("SOL DE MAYO"), marca())
        self.assertEqual(a[0]["puntaje"], 100)

    def test_la_solicitud_entra_como_candidata_por_el_indice(self):
        m = marca()
        indice = {}
        for k in vig._claves_marca(m, GEN):
            indice.setdefault(k, []).append(m)
        self.assertEqual([x["acta"] for x in vig._candidatas(indice, "SUN COAST", GEN)], [m["acta"]])
        self.assertEqual(vig._candidatas(indice, "LUNA", GEN), [])

    def test_la_firma_cambia_con_los_conceptos_y_no_si_no_hay(self):
        sin = vig.firma_cartera(marca(conceptos_vigilancia=None))
        self.assertEqual(sin, vig.firma_cartera({**marca(), "conceptos_vigilancia": ""}))
        self.assertNotEqual(sin, vig.firma_cartera(marca()))
        self.assertNotEqual(vig.firma_cartera(marca()), vig.firma_cartera(marca(conceptos_vigilancia="SUN")))


class ListaDeLaIA(unittest.TestCase):
    def test_limpiar(self):
        out = cv.limpiar(["Sun", "sun", "SOL DE MAYO", "solDeMayo", "of", "", None, 3, "  May   Sun ",
                          "UNA FRASE DEMASIADO LARGA PARA SER UN CONCEPTO"], "Sol de Mayo")
        self.assertEqual(out, ["SUN", "MAY SUN"])

    def test_tope(self):
        self.assertEqual(len(cv.limpiar([f"PALABRA{i}" for i in range(40)])), cv.MAX_CONCEPTOS)

    def test_pedir_a_la_ia_lee_el_json(self):
        with mock.patch.object(ia_texto, "_pedir", return_value='```json\n{"conceptos": ["Sun", "Soleil"]}\n```'):
            self.assertEqual(cv.pedir_a_la_ia("SOL"), ["SUN", "SOLEIL"])

    def test_respuesta_rara_es_error(self):
        with mock.patch.object(ia_texto, "_pedir", return_value="no sé"):
            with self.assertRaises(ia_texto.ErrorIA):
                cv.pedir_a_la_ia("SOL")


if __name__ == "__main__":
    unittest.main()
