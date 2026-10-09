"""
Pruebas del atajo «🗂 Ver Grilla Digital» de la cabecera de la ficha del lead (panel/static/crm.js).
Corren el código real con Node, sin navegador ni servidor; si Node no está instalado se
saltean. Correr con:
    cd scripts && python3 -m unittest test_crm_grilla -v
"""

import json
import os
import re
import shutil
import subprocess
import textwrap
import unittest

CRM_JS = os.path.join(os.path.dirname(__file__), "..", "panel", "static", "crm.js")
NODE = shutil.which("node")

GUION = textwrap.dedent("""
    const vm = require("vm"), fs = require("fs");
    const entrada = JSON.parse(fs.readFileSync(0, "utf8"));
    const ctx = { document: { addEventListener() {}, getElementById() { return null; }, querySelectorAll() { return []; } },
      console, sessionStorage: { getItem() { return null; } }, location: { pathname: "/titular/1" } };
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(entrada.ruta, "utf8"), ctx);
    const html = ctx._crmHtmlCabecera(entrada.datos, { partes: {}, mailOposicion() {} });
    console.log(JSON.stringify(html));
""")


def marca(acta, **kw):
    base = {"acta": acta, "tuvo_oposicion": True, "es_lead": True, "oposicion_atendida": None,
            "oposicion_sirve": True, "email": "lead@ejemplo.com"}
    base.update(kw)
    return base


def datos(marcas, lead=None, cliente=None):
    l = {"es_lead": True, "oposicion_sin_apoderado": True, "etapa": "nuevo", "clases": []}
    l.update(lead or {})
    return {"clave": "111", "lead": l, "marcas": marcas, "plazos": [], "cliente": cliente}


@unittest.skipUnless(NODE, "Node no está instalado")
class AtajoGrilla(unittest.TestCase):
    def botones(self, d):
        r = subprocess.run([NODE, "-e", GUION], input=json.dumps({"ruta": CRM_JS, "datos": d}),
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        html = json.loads(r.stdout)
        out = []
        for m in re.finditer(r'data-accion="([a-z-]+)"(?: data-acta-opo="(\d+)")?', html):
            if m.group(1) in ("ver-grilla-opo", "ver-acta-opo", "mail-oposicion"):
                out.append(m.group(1) + (":" + m.group(2) if m.group(2) else ""))
        return out, html

    def test_lead_pendiente_tiene_los_tres_atajos_como_siempre(self):
        b, _ = self.botones(datos([marca("1")]))
        self.assertEqual(b, ["ver-acta-opo:1", "ver-grilla-opo:1", "mail-oposicion:1"])

    def test_con_la_oposicion_atendida_queda_la_grilla(self):
        b, _ = self.botones(datos([marca("1", oposicion_atendida=True)],
                                  lead={"oposicion_sin_apoderado": False, "oposicion_atendida": True}))
        self.assertEqual(b, ["ver-grilla-opo:1"])

    def test_con_gestor_o_apoderado_queda_la_grilla(self):
        b, _ = self.botones(datos([marca("1", es_lead=False)],
                                  lead={"es_lead": False, "oposicion_sin_apoderado": False, "con_gestor_manual": True}))
        self.assertEqual(b, ["ver-grilla-opo:1"])

    def test_si_ya_es_cliente_queda_la_grilla(self):
        b, _ = self.botones(datos([marca("1")], cliente={"id": 1, "nombre": "Cliente SA"}))
        self.assertEqual(b, ["ver-grilla-opo:1"])

    def test_sin_ninguna_oposicion_no_hay_grilla(self):
        b, _ = self.botones(datos([marca("1", tuvo_oposicion=False)], lead={"oposicion_sin_apoderado": False}))
        self.assertEqual(b, [])

    def test_con_varias_marcas_con_oposicion_hay_un_boton_por_marca(self):
        b, html = self.botones(datos([marca("1", email=""), marca("2")]))
        # primero la marca que se está atendiendo (la que tiene mail), después las demás
        self.assertEqual(b, ["ver-acta-opo:2", "ver-grilla-opo:2", "ver-grilla-opo:1", "mail-oposicion:2"])
        self.assertIn("Ver Grilla Digital · acta 2", html)
        self.assertIn("Ver Grilla Digital · acta 1", html)

    def test_con_una_sola_marca_el_texto_no_lleva_el_numero(self):
        _, html = self.botones(datos([marca("1")]))
        self.assertIn("🗂 Ver Grilla Digital</button>", html)

    def test_no_mezcla_marcas_sin_oposicion(self):
        b, _ = self.botones(datos([marca("1"), marca("2", tuvo_oposicion=False)]))
        self.assertEqual([x for x in b if x.startswith("ver-grilla")], ["ver-grilla-opo:1"])


if __name__ == "__main__":
    unittest.main()
