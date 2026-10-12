"""Pruebas de la unión de clientes (cartera.unir_clientes) con una base falsa.

    cd scripts && python3 -m unittest test_unir_clientes
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))

try:
    import psycopg2  # noqa: F401
except ImportError:
    import types
    _pg = types.ModuleType("psycopg2")
    _pg.extras = types.ModuleType("psycopg2.extras")
    _pg.extras.RealDictCursor = object
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extras"] = _pg.extras

import cartera  # noqa: E402


class CursorFalso:
    """Responde lo justo para unir_clientes y anota los UPDATE/DELETE."""

    def __init__(self, clientes):
        self.clientes, self.sql, self._res = clientes, [], None
        self.description = None

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        if sql.startswith("SELECT * FROM clientes"):
            self._res = [c for c in self.clientes if c["id"] in params[0]]
        elif "to_regclass" in sql:
            self._res = [{"ok": True}]
        elif sql.startswith("SELECT COUNT"):
            self._res = [{"n": 2}]
        else:
            self._res = []

    def fetchall(self):
        return self._res

    def fetchone(self):
        return self._res[0] if self._res else None


def cli(i, cuit=None, **kw):
    base = {"id": i, "cuit": cuit, "email": None, "telefono": None, "contacto": None, "notas": None}
    base.update(kw)
    return base


class TestUnirClientes(unittest.TestCase):
    def test_normalizar_nombre(self):
        self.assertEqual(cartera.normalizar_nombre("  Komunikación, S.A. "), "KOMUNIKACION S A")
        self.assertEqual(cartera.normalizar_nombre(None), "")

    def test_mismo_cliente(self):
        with self.assertRaises(cartera.UnionError):
            cartera.unir_clientes(CursorFalso([cli(1)]), 1, 1)

    def test_no_existe(self):
        with self.assertRaises(cartera.UnionError):
            cartera.unir_clientes(CursorFalso([cli(1)]), 2, 1)

    def test_cuit_distinto_pide_forzar(self):
        cur = CursorFalso([cli(1, "20111111111"), cli(2, "20222222222")])
        with self.assertRaises(cartera.UnionError) as e:
            cartera.unir_clientes(cur, 2, 1)
        self.assertIn("CUIT distinto", str(e.exception))
        r = cartera.unir_clientes(cur, 2, 1, forzar=True)
        self.assertTrue(r["movidas"])

    def test_simular_no_escribe(self):
        cur = CursorFalso([cli(1), cli(2, email="a@b.c")])
        r = cartera.unir_clientes(cur, 2, 1, simular=True)
        self.assertEqual(r["completados"], ["email"])
        self.assertFalse([s for s, _ in cur.sql if s.startswith(("UPDATE", "DELETE"))])

    def test_une_completa_y_borra(self):
        cur = CursorFalso([cli(1, notas="uno"), cli(2, "20222222222", telefono="11", notas="dos")])
        r = cartera.unir_clientes(cur, 2, 1)
        self.assertEqual(set(r["completados"]), {"cuit", "telefono"})
        sqls = [s for s, _ in cur.sql]
        self.assertTrue(any(s.startswith("UPDATE cartera_marcas") for s in sqls))
        self.assertTrue(sqls[-1].startswith("DELETE FROM clientes"))
        notas = [p for s, p in cur.sql if s.startswith("UPDATE clientes SET notas")][0]
        self.assertEqual(notas[0], "uno\n\ndos")


if __name__ == "__main__":
    unittest.main()
