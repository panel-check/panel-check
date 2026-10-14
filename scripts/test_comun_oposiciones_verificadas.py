"""Popup «⚠ Ver oposición» de la ficha del titular (panel/static/comun.js),
cuando la marca ya tiene oposiciones VERIFICADAS en INPI desde «Análisis de
marca» (campo `oposiciones_verificadas` que arma `_completar_oposiciones_verificadas`
en app.py — ver ese docstring para el motivo del cambio, 14/10/2026).

Antes de este cambio, el popup siempre mostraba una sola oposición (la que
guarda a mano la tabla `marcas`), aunque «Análisis de marca» ya hubiera
verificado varias en el expediente real: las dos vistas quedaban
desincronizadas (caso real: acta 4778443, 1 vs. 3 oposiciones).

Corre el código real de comun.js con Node (sin DOM real: _bloqueOposicionVerificada
y _oposicionesVerificadas son funciones puras, no tocan document/window).
Se saltea si no hay Node.
    cd scripts && python3 -m unittest test_comun_oposiciones_verificadas
"""
import json
import pathlib
import shutil
import subprocess
import unittest

JS_FILE = pathlib.Path(__file__).resolve().parent.parent / "panel" / "static" / "comun.js"

JS = r"""
const fs = require("fs");
const js = fs.readFileSync(process.argv[2], "utf8");
const row = JSON.parse(process.argv[3]);

function funcion(nombre) {
  const ini = js.indexOf("function " + nombre + "(");
  if (ini < 0) throw new Error("no está " + nombre);
  let i = js.indexOf("{", ini), prof = 0;
  for (; i < js.length; i++) {
    if (js[i] === "{") prof++;
    else if (js[i] === "}" && --prof === 0) break;
  }
  return js.slice(ini, i + 1);
}

const cuerpo = [
  funcion("_escapeHtml"), funcion("_idSeguro"), funcion("_fmtFechaCorta"),
  funcion("_bloqueMarcaOpuestaVerificada"), funcion("_bloqueOposicionVerificada"),
  funcion("_oposicionesVerificadas"),
  "const verificadas = _oposicionesVerificadas(row);",
  "if (!verificadas) return { verificadas: null };",
  "return { verificadas: true, cantidad: verificadas.length,",
  "  html: verificadas.map((i, idx) => _bloqueOposicionVerificada(i, idx + 1)).join('') };",
].join("\n");
console.log(JSON.stringify(new Function("row", cuerpo)(row)));
"""


def correr(row):
    r = subprocess.run(["node", "-e", JS, "x", str(JS_FILE), json.dumps(row)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "no hay Node")
class PopupOposicionesVerificadas(unittest.TestCase):
    def test_sin_verificacion_no_hay_lista(self):
        # Caso de siempre: la marca solo tiene la única oposición de `marcas`
        # (sin `oposiciones_verificadas`) -- el popup sigue el camino viejo.
        r = correr({"acta": "4778443", "oponente_nombre": "COCHILOBO MATIAS"})
        self.assertIsNone(r["verificadas"])

    def test_tres_oposiciones_verificadas_salen_las_tres(self):
        # El caso real reportado el 14/10/2026: 3 oposiciones verificadas en
        # «Análisis de marca» (Cochilobo, Catena, ML Automotores) -- antes el
        # popup solo mostraba la primera.
        row = {
            "acta": "4778443",
            "oponente_nombre": "COCHILOBO MATIAS",  # el dato viejo sigue ahí, pero no se usa
            "oposiciones_verificadas": [
                {"oponente": "COCHILOBO MATIAS", "fundamento": "funda 1", "presentacion": "2026-10-01"},
                {"oponente": "CATENA, ERNESTO", "fundamento": "funda 2", "presentacion": "2026-10-08",
                 "marcas_opuestas": [{"acta": "3250845", "denominacion": "MARA", "clase": "33"}]},
                {"oponente": "ML AUTOMOTORES S.R.L.", "fundamento": "funda 3", "presentacion": "2026-10-09"},
            ],
            "oposiciones_verificadas_en": "2026-10-14T19:04:00+00:00",
        }
        r = correr(row)
        self.assertTrue(r["verificadas"])
        self.assertEqual(r["cantidad"], 3)
        html = r["html"]
        for quien in ("COCHILOBO MATIAS", "CATENA, ERNESTO", "ML AUTOMOTORES S.R.L."):
            self.assertIn(quien, html)
        self.assertIn("funda 1", html)
        self.assertIn("funda 2", html)
        self.assertIn("funda 3", html)
        # La marca del oponente citada (Catena) sale con link a su ficha.
        self.assertIn("MARA", html)
        self.assertIn("abrirActa('3250845')", html)

    def test_marca_opuesta_no_encontrada(self):
        row = {
            "acta": "4778443",
            "oposiciones_verificadas": [
                {"oponente": "X", "marcas_opuestas": [{"acta": "999", "estado": "no_existe"}]},
            ],
        }
        html = correr(row)["html"]
        self.assertIn("no se encontró el expediente", html)


if __name__ == "__main__":
    unittest.main()
