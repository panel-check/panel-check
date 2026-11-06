"""
Pruebas de la búsqueda por texto del listado (lupa de la barra): marca y titular sin acentos,
sin mayúsculas, sin espacios ni signos, y con las palabras en cualquier orden.

Se toma el bloque de panel/app.py sin importar el panel entero (que necesita FastAPI y la base).

    cd scripts && python3 -m unittest test_busqueda_texto
"""

import pathlib
import re
import unittest

_SRC = (pathlib.Path(__file__).resolve().parent.parent / "panel" / "app.py").read_text(encoding="utf-8")
_NS = {"re": re}
exec(_SRC[_SRC.index("_BUSQ_CON_ACENTO ="):_SRC.index("def _cruce_clases_sql")], _NS)
texto_comparable = _NS["texto_comparable"]
condicion_busqueda_texto = _NS["condicion_busqueda_texto"]


class TextoComparable(unittest.TestCase):
    def test_sin_acentos_mayusculas_ni_signos(self):
        for t in ("CINE CATÁSTROFE", "cine catastrofe", "Cine-Catástrofe!", "CINECATASTROFE"):
            self.assertEqual(texto_comparable(t), "CINECATASTROFE")
        self.assertEqual(texto_comparable("Ñandú & Cía."), "NANDUCIA")
        self.assertEqual(texto_comparable(None), "")

    def test_la_tabla_sql_y_python_coinciden(self):
        con, sin = _NS["_BUSQ_CON_ACENTO"], _NS["_BUSQ_SIN_ACENTO"]
        self.assertEqual(len(con), len(sin))
        for a, b in zip(con, sin):
            self.assertEqual(texto_comparable(a), b.upper(), a)


class Condicion(unittest.TestCase):
    def test_marca_titular_y_datos_crudos(self):
        sql, valores = condicion_busqueda_texto("cine catástrofe")
        self.assertEqual(sql.count("%s"), len(valores))
        self.assertIn("%CINECATASTROFE%", valores)
        self.assertIn("%cine catástrofe%", valores)            # mail, CUIT y acta, tal cual
        self.assertIn("%CINE%", valores)
        self.assertIn("%CATASTROFE%", valores)                  # palabras en cualquier orden

    def test_una_palabra_no_arma_el_y_de_palabras(self):
        sql, valores = condicion_busqueda_texto("catastrofe")
        self.assertEqual(sql.count("%s"), len(valores))
        self.assertNotIn(" AND ", sql)

    def test_cuit_con_guiones(self):
        sql, valores = condicion_busqueda_texto("20-12345678-9")
        self.assertEqual(sql.count("%s"), len(valores))
        self.assertIn("%20123456789%", valores)

    def test_solo_signos_busca_crudo(self):
        sql, valores = condicion_busqueda_texto("@@")
        self.assertEqual(sql.count("%s"), len(valores))
        self.assertNotIn("denominacion", sql)


if __name__ == "__main__":
    unittest.main()
