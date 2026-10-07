"""Pruebas del link público del PDF de análisis de marca (códigos, anulación y búsqueda por código).

Sin base ni servidor: un cursor falso con la tabla analisis_marca en memoria.
Ejecutar: cd scripts && python3 -m unittest test_analisis_link
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import analisis_marca as am  # noqa: E402


class CursorTabla:
    """Lo mínimo de analisis_marca que usan token_publico / anular_token / acta_por_token."""

    def __init__(self):
        self.filas = {}      # acta -> {"token_publico": ..., "texto": ...}
        self._res = None
        self.rowcount = 0

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self.rowcount = 0
        if q.startswith("SELECT token_publico FROM analisis_marca WHERE acta"):
            f = self.filas.get(params[0])
            self._res = [(f["token_publico"],)] if f else []
        elif q.startswith("INSERT INTO analisis_marca (acta, token_publico)"):
            acta, t = params
            self.filas.setdefault(acta, {"texto": "", "token_publico": None})["token_publico"] = t
        elif q.startswith("UPDATE analisis_marca SET token_publico = NULL"):
            f = self.filas.get(params[0])
            if f and f["token_publico"]:
                f["token_publico"] = None
                self.rowcount = 1
        elif q.startswith("SELECT acta FROM analisis_marca WHERE token_publico"):
            self._res = [(a,) for a, f in self.filas.items() if f["token_publico"] == params[0]]
        else:
            raise AssertionError("consulta inesperada: " + q)

    def fetchone(self):
        return self._res[0] if self._res else None


class LinkPublico(unittest.TestCase):
    def test_el_codigo_es_largo_valido_y_se_repite(self):
        cur = CursorTabla()
        t = am.token_publico(cur, "4688297")
        self.assertTrue(am.RE_TOKEN.match(t))
        self.assertGreaterEqual(len(t), 40)
        self.assertEqual(am.token_publico(cur, "4688297"), t, "pedirlo de nuevo devuelve el mismo link")
        self.assertNotIn("4688297", t)

    def test_cada_marca_tiene_su_codigo(self):
        cur = CursorTabla()
        self.assertNotEqual(am.token_publico(cur, "4688297"), am.token_publico(cur, "4688298"))

    def test_se_encuentra_el_acta_por_el_codigo(self):
        cur = CursorTabla()
        t = am.token_publico(cur, "4688297")
        self.assertEqual(am.acta_por_token(cur, t), "4688297")

    def test_codigos_inventados_o_con_formato_raro_no_abren_nada(self):
        cur = CursorTabla()
        am.token_publico(cur, "4688297")
        for malo in ["", None, "4688297", "a" * 43, "x" * 10, "../" + "a" * 40, "a" * 100, "a b" * 20]:
            self.assertIsNone(am.acta_por_token(cur, malo), repr(malo))

    def test_renovar_anula_el_anterior(self):
        cur = CursorTabla()
        viejo = am.token_publico(cur, "4688297")
        nuevo = am.token_publico(cur, "4688297", renovar=True)
        self.assertNotEqual(viejo, nuevo)
        self.assertIsNone(am.acta_por_token(cur, viejo))
        self.assertEqual(am.acta_por_token(cur, nuevo), "4688297")

    def test_anular_apaga_el_link(self):
        cur = CursorTabla()
        t = am.token_publico(cur, "4688297")
        self.assertTrue(am.anular_token(cur, "4688297"))
        self.assertIsNone(am.acta_por_token(cur, t))
        self.assertFalse(am.anular_token(cur, "4688297"), "anular dos veces no falla")
        self.assertNotEqual(am.token_publico(cur, "4688297"), t, "al pedirlo otra vez sale uno nuevo")

    def test_crear_el_link_no_pisa_el_texto_del_analisis(self):
        cur = CursorTabla()
        cur.filas["4688297"] = {"texto": "Análisis escrito", "token_publico": None}
        am.token_publico(cur, "4688297")
        self.assertEqual(cur.filas["4688297"]["texto"], "Análisis escrito")


if __name__ == "__main__":
    unittest.main()
