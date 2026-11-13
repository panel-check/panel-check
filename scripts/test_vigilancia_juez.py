"""Pruebas del juez con IA de la vigilancia y de las reglas corregidas el 12/11/2026
(palabras comunes por clase, conceptos solo como parte principal, una alerta por marca).
No llaman a la API: la evaluación real con Claude está en probar_juez_vigilancia.py.

    cd scripts && python3 -m unittest test_vigilancia_juez
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import cartera  # noqa: E402
import juez_vigilancia as juez  # noqa: E402
import similitud as sim  # noqa: E402
import vigilancia_core as vig  # noqa: E402
from casos_vigilancia import COMUNES_MEDIDAS, CONFLICTO, SIN_CONFLICTO  # noqa: E402

GEN = frozenset(cartera.PALABRAS_GENERICAS_DEFAULT)
PARES = set(cartera.CLASES_RELACIONADAS_DEFAULT)


def ajustes():
    comunes = {c: frozenset(t for t, n in d.items() if n >= vig.COMUN_MIN_CLASE) for c, d in COMUNES_MEDIDAS.items()}
    return {"genericas": GEN, "pares": PARES, "matriculas": set(), "comunes_clase": comunes,
            "comunes_global": frozenset(), "frec": COMUNES_MEDIDAS,
            "config": {"umbral": 75, "nivel_alta": 90, "nivel_media": 82, "meses_universo": 18}}


def marca(nombre, clase, acta="1", conceptos="", **extra):
    m = {"acta": acta, "cliente_id": 1, "denominacion": nombre, "clase": clase, "tipo": "D", "nombres": [nombre],
         "conceptos": sim.separar_conceptos(conceptos), "conceptos_vigilancia": conceptos, "vigilar_todas_clases": False,
         "umbral_vigilancia": None, "cuit": "", "cliente_cuit": "", "terminos_vigilancia": ""}
    m.update(extra)
    m["grupo"] = vig.clave_grupo(m)
    return m


def fila(nombre, clase, acta="9"):
    return {"acta": acta, "nombre": nombre, "clase": clase, "tipo": "M", "titular": "X", "cuit": "", "fuente": "boletin",
            "boletin": "1", "fecha_presentacion": None, "matricula_agente": None, "caracter": None}


class ReglasCorregidas(unittest.TestCase):
    def test_palabra_comun_en_la_clase_no_alerta(self):
        aj = ajustes()
        for a, ca, b, cb in SIN_CONFLICTO[:2]:
            self.assertEqual(vig._evaluar_fila(fila(b, cb), [marca(a, ca)], aj, {}, set()), [], f"{a} / {b}")

    def test_concepto_suelto_en_marca_larga_no_alerta(self):
        aj = ajustes()
        self.assertEqual(vig._evaluar_fila(fila("THE GOLDEN TRIP DESTINOS CON PROPOSITOS", 39),
                                           [marca("SI VIAJO", 39, conceptos="TRIP, TRAVEL")], aj, {}, set()), [])

    def test_palabra_rara_compartida_sigue_alertando(self):
        # NUBE, LABO, ECOBRASA, APEX: aparecen 1 o 2 veces en su clase; son conflictos reales.
        aj = ajustes()
        for a, ca, b, cb in (("NUBE 9", 41, "NUBE RECORDS", 41), ("LE LABO", 3, "LABO DE CREMAS", 3),
                             ("APEX PADEL", 5, "APEX LAB", 5)):
            self.assertEqual(len(vig._evaluar_fila(fila(b, cb), [marca(a, ca)], aj, {}, set())), 1, f"{a} / {b}")

    def test_reglas_puntuales(self):
        r = sim.puntaje_nombres("SKYPEAK", "PEAKS")
        self.assertGreaterEqual(r[0], 70)
        self.assertTrue(any("raíz" in m for m in r[1]))
        self.assertGreaterEqual(sim.puntaje_nombres("CYLGEM", "SILEG")[0], 65)  # mismo sonido al empezar
        self.assertTrue(sim.claves_indice("ZAHE") & sim.claves_indice("Z ZAFE"))  # una letra distinta
        self.assertEqual(sim.fonetica_palabra("CYLGEM"), "SILJEM")
        # Una palabra común en la clase no cuenta como raíz.
        self.assertLess(sim.puntaje_nombres("CAFE", "CAFETERIA LUNA", comunes=frozenset({"CAFE"}))[0], 70)

    def test_no_compara_una_palabra_consigo_misma(self):
        _, motivos = sim.puntaje_nombres("NUBE 9", "NUBE RECORDS")
        self.assertFalse(any("palabras parecidas" in m for m in motivos))

    def test_recall_de_casos_reales_no_baja(self):
        # Con umbral 65, las reglas puntuales y las claves nuevas del índice el motor encuentra 21 de 24
        # oposiciones reales (antes del 12/11/2026: 15). Las 3 que faltan son casos débiles o de concepto.
        aj, hits = ajustes(), 0
        aj["config"] = {**aj["config"], "umbral": 65}
        for a, ca, b, cb, _ in CONFLICTO:
            if vig._evaluar_fila(fila(b, cb), [marca(a, ca)], aj, {}, set()) and \
                    sim.claves_indice(a, GEN) & sim.claves_indice(b, GEN):
                hits += 1
        self.assertGreaterEqual(hits, 21)


class UmbralPorClase(unittest.TestCase):
    """13/11/2026: el 65 vale para la misma clase; en otra clase hace falta mucho más parecido."""
    def _aj(self):
        aj = ajustes()
        aj["config"] = {**aj["config"], "umbral": 65}
        return aj

    def test_concepto_en_otra_clase_no_alerta_aunque_vigile_todas(self):
        m = marca("SILVIO SIERRA", 25, conceptos="CIMA, TREES", vigilar_todas_clases=True)
        self.assertEqual(vig._evaluar_fila(fila("CIMA", 41), [m], self._aj(), {}, set()), [])

    def test_identica_en_otra_clase_alerta_si_vigila_todas(self):
        m = marca("KOMUNIKA", 35, vigilar_todas_clases=True)
        self.assertEqual(len(vig._evaluar_fila(fila("KOMUNIKA", 9), [m], self._aj(), {}, set())), 1)

    def test_umbral_para(self):
        self.assertEqual(vig.umbral_para(65, "misma"), 65)
        self.assertEqual(vig.umbral_para(65, "relacionada"), 72)
        self.assertEqual(vig.umbral_para(65, "distinta"), 77)

    def test_relacion_de_motivos(self):
        self.assertEqual(vig.relacion_de_motivos("suenan parecido · otra clase (41)"), "distinta")
        self.assertEqual(vig.relacion_de_motivos("clase vinculada (35 ↔ 41)"), "relacionada")
        self.assertEqual(vig.relacion_de_motivos("misma clase (35)"), "misma")


class UnaAlertaPorMarca(unittest.TestCase):
    def test_dos_actas_de_la_misma_marca_dan_una_alerta_con_las_dos_clases(self):
        m1 = marca("KOMUNIKA", 35, acta="100")
        m2 = marca("KOMUNIKA", 41, acta="101")
        grupo = [{"acta": "100", "clase": 35}, {"acta": "101", "clase": 41}]
        m1["grupo_actas"] = m2["grupo_actas"] = grupo
        out = vig._evaluar_fila(fila("KOMUNIKAS", 35), [m1, m2], ajustes(), {}, set())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["acta_cliente"], "100")
        self.assertEqual(json.loads(out[0]["actas_cliente"]), grupo)

    def test_par_para_el_juez_no_lleva_datos_del_cliente(self):
        m = marca("KOMUNIKA", 35, cliente_nombre="Cliente Secreto SA", cuit="20123456789")
        out = vig._evaluar_fila(fila("KOMUNIKAS", 35), [m], ajustes(), {}, set())
        texto = juez.texto_par(out[0]["_par"])
        self.assertIn("KOMUNIKA", texto)
        self.assertNotIn("Secreto", texto)
        self.assertNotIn("20123456789", texto)


class Juez(unittest.TestCase):
    def test_sensibilidad(self):
        self.assertTrue(juez.alcanza("identica", "identicas"))
        self.assertFalse(juez.alcanza("confundible", "identicas"))
        self.assertTrue(juez.alcanza("confundible", None))  # por defecto: similares
        self.assertFalse(juez.alcanza("cercana", "similares"))
        self.assertTrue(juez.alcanza("cercana", "amplia"))
        self.assertFalse(juez.alcanza("no_confundible", "amplia"))

    def test_aplicar_veredicto(self):
        v = {"veredicto": "no_confundible", "nivel": "baja", "razon": "TURISMO es descriptivo", "modelo": "m",
             "dominante_cliente": "HACELO POR MI", "dominante_nueva": "BORDON"}
        a = vig.aplicar_veredicto({"nivel": "baja"}, v, None)
        self.assertEqual(a["estado"], "descartada_ia")
        a = vig.aplicar_veredicto({}, {**v, "veredicto": "confundible", "nivel": "media"}, "similares")
        self.assertEqual((a["estado"], a["nivel"]), ("nueva", "media"))

    def test_respuesta_de_la_ia(self):
        d = juez._json_de('Acá va: {"veredicto": "Confundible", "nivel": "MEDIA", "razon": "x"}')
        r = juez._normalizar_respuesta(d, "m")
        self.assertEqual((r["veredicto"], r["nivel"]), ("confundible", "media"))
        with self.assertRaises(juez.ErrorJuez):
            juez._normalizar_respuesta({"veredicto": "quizás"}, "m")

    def test_productos_en_el_mensaje(self):
        import productos_vigilancia as prod
        self.assertEqual(prod.texto({"estado": "ok", "proteccion": "Solamente", "limitacion": "SUPLEMENTOS NUTRICIONALES"}),
                         "Solamente: SUPLEMENTOS NUTRICIONALES")
        self.assertEqual(prod.texto({"estado": "ok", "proteccion": "Toda la clase", "limitacion": None}), "Toda la clase")
        self.assertEqual(prod.texto({"estado": "error"}), "")
        t = juez.texto_par({"marca_cliente": "APEX LAB", "marca_nueva": "APEX PERFORMANCE", "clase_nueva": 5,
                            "productos_cliente": "Solamente: SUPLEMENTOS NUTRICIONALES",
                            "productos_nueva": "Solamente: SUPLEMENTOS ALIMENTICIOS PARA PERSONAS"})
        self.assertIn("Productos/servicios de la marca del cliente: Solamente: SUPLEMENTOS NUTRICIONALES", t)
        self.assertIn("SUPLEMENTOS ALIMENTICIOS PARA PERSONAS", t)
        self.assertFalse(prod.consultar_habilitado() and "VIGILANCIA_CONSULTAR_INPI" not in os.environ)

    def test_acta_del_cliente_de_la_misma_clase(self):
        a = {"acta_cliente": "100", "clase_nueva": 32,
             "actas_cliente": json.dumps([{"acta": "100", "clase": 5}, {"acta": "101", "clase": 32}])}
        self.assertEqual(vig._acta_cliente_para(a), "101")
        self.assertEqual(vig._acta_cliente_para({**a, "clase_nueva": 9}), "100")

    def test_sin_clave_no_evalua(self):
        viejo = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            self.assertEqual(juez.evaluar_lote([{"marca_cliente": "A"}]), [None])
        finally:
            if viejo:
                os.environ["ANTHROPIC_API_KEY"] = viejo


if __name__ == "__main__":
    unittest.main()
