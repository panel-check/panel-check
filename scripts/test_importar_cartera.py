"""Pruebas de las funciones puras de importar_cartera.py (sin base ni INPI).

    cd scripts && python3 -m unittest test_importar_cartera
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))

# importar_cartera importa psycopg2; si no está instalado (entorno sin base) se usa un reemplazo
# vacío: estas pruebas no tocan la base.
try:
    import psycopg2  # noqa: F401
except ImportError:
    import types
    _pg = types.ModuleType("psycopg2")
    _pg.extras = types.ModuleType("psycopg2.extras")
    _pg.extras.RealDictCursor = object
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extras"] = _pg.extras

try:
    import importar_cartera as ic
except ImportError as e:  # pragma: no cover
    ic = None
    _motivo = str(e)


@unittest.skipIf(ic is None, "faltan librerías para importar importar_cartera")
class PruebasImportarCartera(unittest.TestCase):
    def test_actas_con_puntos_y_repetidas(self):
        texto = "4.545.450, 4545450\n4775001; 3.877.261  abc 12"
        self.assertEqual(ic.parsear_actas(texto), ["4545450", "4775001", "3877261"])

    def test_actas_vacio(self):
        self.assertEqual(ic.parsear_actas(""), [])
        self.assertEqual(ic.parsear_actas(None), [])

    def test_cuit(self):
        self.assertEqual(ic.cuit_valido("30-71234567-8"), "30712345678")
        self.assertIsNone(ic.cuit_valido("1234"))
        self.assertIsNone(ic.cuit_valido(None))

    def test_nombre_normalizado(self):
        self.assertEqual(ic.nombre_normalizado("  Chef   al Paso S.A. "), "CHEF AL PASO S A")
        self.assertEqual(ic.nombre_normalizado("Tolosa Mudanzás"), "TOLOSA MUDANZAS")

    def test_cache_evita_clientes_duplicados(self):
        cache = {}
        ic.recordar(cache, "30712345678", "Chef al Paso SA", 7)
        # mismo CUIT: lo resuelve el cache sin tocar la base
        self.assertEqual(ic.buscar_cliente(None, "30712345678", "otro nombre", cache), 7)
        # sin CUIT, mismo nombre con otra escritura: también
        ic.recordar(cache, None, "Fuego Eterno S.R.L.", 8)
        self.assertEqual(ic.buscar_cliente(None, None, "fuego eterno s.r.l.", cache), 8)


if __name__ == "__main__":
    unittest.main()
