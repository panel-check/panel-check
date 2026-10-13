"""Pestaña «Análisis de marca» de la ficha del titular (panel/static/titular.html).

Corre el código real de la página con Node (con un document de mentira):
- la etiqueta de cada marca lleva «(con oposición)» solo si tuvo oposición;
- al abrir, queda elegida la marca con oposición (la primera si hay varias);
- ?acta=N (calendario) manda sobre la marca con oposición;
- sin oposiciones queda la primera de la lista.

Se saltea si no hay Node.
    cd scripts && python3 -m unittest test_titular_analisis
"""
import json
import pathlib
import shutil
import subprocess
import unittest

HTML = pathlib.Path(__file__).resolve().parent.parent / "panel" / "static" / "titular.html"

JS = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const casos = JSON.parse(process.argv[3]);

function funcion(nombre) {
  const ini = html.indexOf("function " + nombre + "(");
  if (ini < 0) throw new Error("no está " + nombre);
  let i = html.indexOf("{", ini), prof = 0;
  for (; i < html.length; i++) {
    if (html[i] === "{") prof++;
    else if (html[i] === "}" && --prof === 0) break;
  }
  return html.slice(ini, i + 1);
}

const salida = [];
for (const c of casos) {
  const elems = {};
  const el = (id) => elems[id] || (elems[id] = { id, value: "", innerHTML: "", hidden: false, addEventListener() {} });
  const cuerpo = [
    "const document = { getElementById: el };",
    "const window = { addEventListener() {} };",
    "const ACTA_PEDIDA = " + JSON.stringify(c.pedida || null) + ";",
    "let anFilas = [], anIniciado = false, anActa = null;",
    "const _escapeHtml = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');",
    "const sinNombre = () => '(sin nombre)';",
    "const anActualizarLote = () => {}, anCargar = () => {}, anExportarPdf = 0, anCopiarLink = 0, anAnularLink = 0,",
    "  anMejorar = 0, anExportarLote = 0, anUsarPropuesta = 0, anDescartarPropuesta = 0, anDeshacer = 0,",
    "  anGuardarEnDrive = 0, anLoteADrive = 0, anPresuAlternar = 0, anPresuGuardar = 0,",
    "  anGuardarAhora = 0, anMsg = () => {}, anSel = new Set(), anTocadas = new Set(), anPintarLote = () => {}, apiJson = async () => ({});",
    "let anUnico = false, anUnicoLeido = false;",
    "let anTimer, anGuardado;",
    funcion("mlNombreMarca"), funcion("mlEsOposicion"), "const mlEsc = (v) => _escapeHtml(v == null ? '' : String(v));",
    funcion("anEtiquetaMarca"), funcion("anIniciar"),
    "const sel = el('an-marca'); el('an-box'); el('an-vacio');",
    // el select de mentira: al asignar innerHTML no hay opciones reales, así que value queda como se asigne
    "anIniciar(" + JSON.stringify(c.filas) + ");",
    "return { elegida: sel.value, opciones: sel.innerHTML, etiqueta: filas => filas.map(anEtiquetaMarca) };",
  ].join("\n");
  const r = new Function("el", cuerpo)(el);
  salida.push({ elegida: r.elegida, opciones: r.opciones, etiquetas: r.etiqueta(c.filas) });
}
console.log(JSON.stringify(salida));
"""


def correr(casos):
    r = subprocess.run(["node", "-e", JS, "x", str(HTML), json.dumps(casos)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


def marca(acta, clase, opo=None, nombre="SEPIA"):
    return {"acta": acta, "clase": clase, "denominacion": nombre, "tipo": "D", "tuvo_oposicion": opo}


@unittest.skipUnless(shutil.which("node"), "no hay Node")
class AnalisisMarcaTitular(unittest.TestCase):
    def test_etiqueta_y_preseleccion(self):
        filas = [marca("4788268", 9, None), marca("4788267", 42, True),
                 marca("4788263", 9, None, "SAMARITAN"), marca("4788262", 42, False, "SAMARITAN")]
        a, b, c = correr([
            {"filas": filas},
            {"filas": filas, "pedida": "4788263"},
            {"filas": [marca("1", 9), marca("2", 42)]},
        ])
        self.assertEqual(a["elegida"], "4788267")
        self.assertEqual(a["etiquetas"][1], "SEPIA · acta 4788267 · clase 42 (con oposición)")
        self.assertEqual(a["etiquetas"][0], "SEPIA · acta 4788268 · clase 9")
        self.assertEqual(sum("(con oposición)" in e for e in a["etiquetas"]), 1)
        self.assertIn("(con oposición)", a["opciones"])
        self.assertEqual(b["elegida"], "4788263")      # ?acta=N manda
        self.assertNotEqual(c["elegida"], "2")         # sin oposición: no se fuerza ninguna


if __name__ == "__main__":
    unittest.main()
