"""
Pruebas de «pasar a la siguiente ficha después de mandar el mail de oposición»
(panel/static/titular.html). Corren el código real de la página con Node, sin navegador
ni servidor; si Node no está instalado se saltean. Correr con:
    cd scripts && python3 -m unittest test_titular_siguiente -v
"""

import json
import os
import shutil
import subprocess
import textwrap
import unittest

HTML = os.path.join(os.path.dirname(__file__), "..", "panel", "static", "titular.html")
NODE = shutil.which("node")

GUION = textwrap.dedent("""
    const vm = require("vm");
    const entrada = JSON.parse(require("fs").readFileSync(0, "utf8"));
    const avisos = [];
    let temporizador = null;
    const ctx = {
      mlFilas: entrada.filas,
      mostrarAviso: (texto, tipo) => avisos.push({ texto, tipo: tipo || "ok" }),
      sessionStorage: { getItem: () => (entrada.cola === null ? null : JSON.stringify(entrada.cola)) },
      location: { pathname: entrada.ruta, href: "" },
      setTimeout: (f, ms) => { temporizador = { f, ms }; },
      decodeURIComponent,
    };
    vm.createContext(ctx);
    vm.runInContext(entrada.codigo, ctx);
    ctx.pasarAlSiguienteTitular();
    const hrefAntes = ctx.location.href;
    if (temporizador) temporizador.f();
    console.log(JSON.stringify({ avisos, hrefAntes, hrefDespues: ctx.location.href }));
""")


def codigo_pagina() -> str:
    with open(HTML, encoding="utf-8") as f:
        s = f.read()
    return s[s.index("function mlOposicionVigente"):s.index("// «✓ Ya se le mandó el")]


def marca(**kw):
    """Una marca del titular con una oposición pendiente, salvo que se pise."""
    base = {"tuvo_oposicion": True, "es_lead": True, "oposicion_atendida": None, "oposicion_sirve": True}
    base.update(kw)
    return base


COLA = {"links": ["/titular/111", "/titular/222", "/titular/333"], "desde": "/"}


@unittest.skipUnless(NODE, "Node no está instalado")
class PasarAlSiguiente(unittest.TestCase):
    def correr(self, filas, cola=COLA, ruta="/titular/222"):
        r = subprocess.run(
            [NODE, "-e", GUION],
            input=json.dumps({"filas": filas, "cola": cola, "ruta": ruta, "codigo": codigo_pagina()}),
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_una_marca_con_oposicion_pasa_a_la_siguiente(self):
        res = self.correr([marca(), marca(tuvo_oposicion=False)])
        self.assertEqual(res["hrefAntes"], "")  # primero se ve el aviso, después navega
        self.assertEqual(res["hrefDespues"], "/titular/333")
        self.assertEqual(len(res["avisos"]), 1)
        self.assertEqual(res["avisos"][0]["tipo"], "ok")

    def test_varias_marcas_con_oposicion_no_pasa_sola(self):
        res = self.correr([marca(), marca(), marca(tuvo_oposicion=False)])
        self.assertEqual(res["hrefDespues"], "")
        self.assertEqual(res["avisos"][0]["tipo"], "aviso")
        self.assertIn("2 marcas con oposición", res["avisos"][0]["texto"])

    def test_la_oposicion_ya_atendida_no_cuenta(self):
        res = self.correr([marca(), marca(oposicion_atendida=True)])
        self.assertEqual(res["hrefDespues"], "/titular/333")

    def test_la_oposicion_que_no_sirve_no_cuenta(self):
        res = self.correr([marca(), marca(oposicion_sirve=False)])
        self.assertEqual(res["hrefDespues"], "/titular/333")

    def test_la_marca_que_ya_no_es_lead_no_cuenta(self):
        res = self.correr([marca(), marca(es_lead=False)])
        self.assertEqual(res["hrefDespues"], "/titular/333")

    def test_tres_marcas_con_oposicion_tampoco_pasa(self):
        res = self.correr([marca(), marca(), marca()])
        self.assertEqual(res["hrefDespues"], "")
        self.assertIn("3 marcas con oposición", res["avisos"][0]["texto"])

    def test_la_ultima_de_la_lista_se_queda_y_avisa(self):
        res = self.correr([marca()], ruta="/titular/333")
        self.assertEqual(res["hrefDespues"], "")
        self.assertIn("última", res["avisos"][0]["texto"])

    def test_sin_lista_no_hace_nada(self):
        res = self.correr([marca()], cola=None)
        self.assertEqual(res["hrefDespues"], "")
        self.assertEqual(res["avisos"], [])

    def test_ficha_que_no_esta_en_la_lista_no_hace_nada(self):
        res = self.correr([marca()], ruta="/titular/999")
        self.assertEqual(res["hrefDespues"], "")
        self.assertEqual(res["avisos"], [])

    def test_ruta_con_caracteres_codificados(self):
        cola = {"links": ["/titular/PEREZ%20JUAN", "/titular/GOMEZ%20ANA"], "desde": "/"}
        res = self.correr([marca()], cola=cola, ruta="/titular/PEREZ JUAN")
        self.assertEqual(res["hrefDespues"], "/titular/GOMEZ%20ANA")


if __name__ == "__main__":
    unittest.main()
