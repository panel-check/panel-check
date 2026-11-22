"""Pruebas de Procesos y plantillas (panel/plantillas_core.py)."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))
import plantillas_core as pc  # noqa: E402


class Carga(unittest.TestCase):
    def test_las_14_de_notion(self):
        self.assertEqual(len(pc.PLANTILLAS_INICIALES), 14)
        claves = [p["clave"] for p in pc.PLANTILLAS_INICIALES]
        self.assertEqual(len(claves), len(set(claves)))
        por = {}
        for p in pc.PLANTILLAS_INICIALES:
            por[p["servicio"]] = por.get(p["servicio"], 0) + 1
            self.assertIn(p["servicio"], pc.SERVICIOS_PLANTILLA)
            self.assertIn(p["canal"], pc.CANALES)
            self.assertTrue(p["cuerpo"].strip())
            self.assertNotIn("\\[", p["cuerpo"])          # sin los escapes de Notion
        self.assertEqual(por, {"registro": 10, "oposicion": 3, "vista": 1})

    def test_datos_a_completar(self):
        datos = set()
        for p in pc.PLANTILLAS_INICIALES:
            datos |= set(re.findall(r"\[([^\[\]\n]{1,40})\]", (p["asunto"] or "") + p["cuerpo"]))
        self.assertTrue({"nombre", "marca", "clase", "acta", "tipo"} <= datos)

    def test_carga_solo_con_la_tabla_vacia(self):
        class Cur:
            def __init__(s, n): s.n, s.sql = n, []
            def execute(s, sql, val=None): s.sql.append(sql)
            def fetchone(s): return (s.n,)
        cur = Cur(0)
        self.assertEqual(pc.crear_tablas(cur), 14)
        self.assertEqual(sum(1 for x in cur.sql if x.strip().startswith("INSERT INTO plantillas")), 14)
        cur = Cur(3)
        self.assertEqual(pc.crear_tablas(cur), 0)
        self.assertFalse([x for x in cur.sql if x.strip().startswith("INSERT")])


if __name__ == "__main__":
    unittest.main()
