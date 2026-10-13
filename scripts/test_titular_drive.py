"""«Guardar en Drive» en la pestaña «Análisis de marca» de la ficha del titular (panel/static/titular.html).

Corre el código real de la página con Node (con un document de mentira):
- una marca: guarda el texto, llama a /api/analisis-marca/<acta>/drive y muestra el resultado con su link;
- si ya estaba subida, el mensaje dice que se actualizó; el link solo se arma si es https;
- si algo falla, se muestra el motivo y el botón vuelve a quedar como estaba;
- varias marcas: se prepara el lote (guardar + consultar INPI) y se manda al endpoint del lote, con
  «el mismo análisis» si corresponde;
- los dos botones del PDF con varias marcas (bajar / Drive) se prenden y apagan juntos, y el de Drive
  queda apagado si el servidor no tiene conectada la cuenta de Google.

Se saltea si no hay Node.
    cd scripts && python3 -m unittest test_titular_drive
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
  const asinc = html.slice(Math.max(0, ini - 6), ini) === "async " ? "async " : "";
  let i = html.indexOf("{", ini), prof = 0;
  for (; i < html.length; i++) {
    if (html[i] === "{") prof++;
    else if (html[i] === "}" && --prof === 0) break;
  }
  return asinc + html.slice(ini, i + 1);
}

function nuevoElemento(id) {
  return {
    id, textContent: "", className: "", disabled: false, hidden: false, value: "", children: [],
    appendChild(h) { this.children.push(h); },
  };
}

const salida = [];
for (const c of casos) {
  const elems = {};
  const el = (id) => elems[id] || (elems[id] = nuevoElemento(id));
  const llamadas = [];
  const cuerpo = [
    "const document = { getElementById: el, createElement: (t) => ({ tag: t }) };",
    "let anActa = " + JSON.stringify(c.acta || null) + ", anFilas = " + JSON.stringify(c.filas || []) + ";",
    "let anDriveDisponible = " + JSON.stringify(c.drive !== false) + ", anUnico = " + JSON.stringify(!!c.unico) + ";",
    "let anPresu = " + JSON.stringify(!!c.presu) + ";",
    "const AN_PRESU_CAMPOS = [\"honorarios_negociacion\", \"horas_incluidas\", \"hora_adicional\", \"honorarios_registro\", \"tasa_inpi\"];",
    "let anSel = new Set(" + JSON.stringify(c.sel || []) + ");",
    "const anGuardarAhora = async () => " + JSON.stringify(c.guardaBien !== false) + ";",
    "const anPrepararLote = async () => { llamadas.push(['preparar']); return " + JSON.stringify(c.preparar || { unico: false, sinVerificar: 0 }) + "; };",
    "const apiJson = async (ruta, metodo, body) => { llamadas.push([ruta, metodo, body]);" +
    "  if (" + JSON.stringify(c.falla || null) + ") throw new Error(" + JSON.stringify(c.falla || "") + "); return " + JSON.stringify(c.respuesta || {}) + "; };",
    "const anMsg = (t, ok = true) => { const e = el('an-estado'); e.textContent = t || ''; e.className = 'msg ' + (ok ? 'ok' : 'err'); };",
    "const anLoteMsg = (t, ok = true) => { const e = el('an-lote-estado'); e.textContent = t || ''; e.className = 'msg ' + (ok ? 'ok' : 'err'); };",
    "const anActasTildadas = () => anFilas.map(f => f.acta).filter(a => anSel.has(a));",
    "const anAvisoSinVerificar = (n) => n ? ' (' + n + ' sin verificar)' : '';",
    funcion("anPresuValores"), funcion("anCuerpoPdf"), funcion("anCuerpoLote"),
    funcion("anBotonesLote"), funcion("anMostrarDrive"), funcion("anGuardarEnDrive"), funcion("anLoteADrive"),
    "return (async () => { " + c.accion + " })();",
  ].join("\n");
  el("an-texto").value = c.texto || "";
  for (const [campo, valor] of Object.entries(c.montos || {})) el("an-p-" + campo).value = valor;
  const prom = new Function("el", "llamadas", cuerpo)(el, llamadas);
  salida.push(prom.then(() => {
    const v = (id) => { const e = elems[id]; return e ? { texto: e.textContent, clase: e.className, apagado: e.disabled, hijos: e.children } : null; };
    return { llamadas, estado: v("an-estado"), lote: v("an-lote-estado"), btn: v("an-drive"),
             lotePdf: v("an-lote-pdf"), loteDrive: v("an-lote-drive") };
  }));
}
Promise.all(salida).then(r => console.log(JSON.stringify(r)));
"""


def correr(casos):
    r = subprocess.run(["node", "-e", JS, "x", str(HTML), json.dumps(casos)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


NUEVO = {"ok": True, "reemplazo": False, "carpeta": "Análisis de marca", "url": "https://drive.google.com/file/d/F1/view"}


@unittest.skipUnless(shutil.which("node"), "no hay Node")
class GuardarEnDrive(unittest.TestCase):
    def test_una_marca_llama_al_endpoint_con_el_texto_y_muestra_el_link(self):
        r = correr([{"acta": "4782038", "texto": "Mi análisis", "respuesta": NUEVO, "accion": "await anGuardarEnDrive();"}])[0]
        self.assertEqual(r["llamadas"], [["/api/analisis-marca/4782038/drive", "POST", {"texto": "Mi análisis"}]])
        self.assertIn("Guardado en Drive ✓", r["estado"]["texto"])
        self.assertIn("Análisis de marca", r["estado"]["texto"])
        self.assertEqual(r["estado"]["clase"], "msg ok")
        self.assertEqual(len(r["estado"]["hijos"]), 1)
        self.assertEqual(r["estado"]["hijos"][0]["href"], NUEVO["url"])
        self.assertEqual(r["estado"]["hijos"][0]["rel"], "noopener")
        self.assertFalse(r["btn"]["apagado"])

    def test_con_presupuesto_manda_los_montos_y_no_el_texto_ni_lo_guarda(self):
        montos = {"honorarios_negociacion": "800.000", "horas_incluidas": "3", "hora_adicional": "200.000"}
        r = correr([{"acta": "4782038", "texto": "Mi análisis", "presu": True, "montos": montos, "guardaBien": False,
                     "respuesta": NUEVO, "accion": "await anGuardarEnDrive();"}])[0]
        # guardaBien=False: si intentara guardar el texto del análisis fallaría; en modo presupuesto no hace falta
        self.assertEqual(r["llamadas"], [["/api/analisis-marca/4782038/drive", "POST", {"presupuesto": montos}]])
        self.assertIn("Guardado en Drive ✓", r["estado"]["texto"])

    def test_con_presupuesto_los_campos_vacios_no_se_mandan(self):
        r = correr([{"acta": "4782038", "presu": True, "montos": {"tasa_inpi": "  ", "hora_adicional": "190.000"},
                     "respuesta": NUEVO, "accion": "await anGuardarEnDrive();"}])[0]
        self.assertEqual(r["llamadas"][0][2], {"presupuesto": {"hora_adicional": "190.000"}})

    def test_si_ya_estaba_subida_dice_que_se_actualizo(self):
        r = correr([{"acta": "4782038", "respuesta": {**NUEVO, "reemplazo": True}, "accion": "await anGuardarEnDrive();"}])[0]
        self.assertIn("Actualizado en Drive ✓", r["estado"]["texto"])

    def test_el_link_solo_se_arma_si_es_https(self):
        for url in (None, "", "javascript:alert(1)", "http://drive.google.com/x"):
            r = correr([{"acta": "4782038", "respuesta": {**NUEVO, "url": url}, "accion": "await anGuardarEnDrive();"}])[0]
            self.assertEqual(r["estado"]["hijos"], [], url)

    def test_si_falla_muestra_el_motivo_y_vuelve_a_prender_el_boton(self):
        motivo = "Google no dejó guardar en Drive con la conexión actual."
        r = correr([{"acta": "4782038", "falla": motivo, "accion": "await anGuardarEnDrive();"}])[0]
        self.assertEqual(r["estado"]["texto"], "No se pudo guardar en Drive: " + motivo)
        self.assertEqual(r["estado"]["clase"], "msg err")
        self.assertFalse(r["btn"]["apagado"])

    def test_si_no_se_pudo_guardar_el_texto_no_sube_nada(self):
        r = correr([{"acta": "4782038", "guardaBien": False, "accion": "await anGuardarEnDrive();"}])[0]
        self.assertEqual(r["llamadas"], [])
        self.assertIn("no se pudo guardar el texto", r["estado"]["texto"])

    def test_sin_marca_abierta_no_hace_nada(self):
        r = correr([{"acta": None, "accion": "await anGuardarEnDrive();"}])[0]
        self.assertEqual(r["llamadas"], [])


@unittest.skipUnless(shutil.which("node"), "no hay Node")
class LoteADrive(unittest.TestCase):
    FILAS = [{"acta": "111111"}, {"acta": "222222"}, {"acta": "333333"}]

    def test_prepara_el_lote_y_manda_las_actas_tildadas_en_orden(self):
        r = correr([{"acta": "222222", "filas": self.FILAS, "sel": ["333333", "111111"], "respuesta": NUEVO,
                     "accion": "await anLoteADrive();"}])[0]
        self.assertEqual(r["llamadas"], [["preparar"], ["/api/analisis-marca-lote/drive", "POST", {"actas": ["111111", "333333"]}]])
        self.assertIn("Guardado en Drive ✓", r["lote"]["texto"])
        self.assertEqual(r["lote"]["hijos"][0]["href"], NUEVO["url"])

    def test_con_el_mismo_analisis_manda_de_cual_marca_es(self):
        r = correr([{"acta": "222222", "filas": self.FILAS, "sel": ["111111", "222222"], "respuesta": NUEVO,
                     "preparar": {"unico": True, "sinVerificar": 0}, "accion": "await anLoteADrive();"}])[0]
        self.assertEqual(r["llamadas"][1][2], {"actas": ["111111", "222222"], "analisis_comun_de": "222222"})

    def test_con_presupuesto_manda_los_montos_y_nunca_el_mismo_analisis(self):
        r = correr([{"acta": "222222", "filas": self.FILAS, "sel": ["111111", "222222"], "respuesta": NUEVO, "presu": True,
                     "unico": True, "montos": {"tasa_inpi": "41.259"},
                     "preparar": {"unico": False, "sinVerificar": 0}, "accion": "await anLoteADrive();"}])[0]
        self.assertEqual(r["llamadas"][1], ["/api/analisis-marca-lote/drive", "POST",
                                            {"actas": ["111111", "222222"], "presupuesto": {"tasa_inpi": "41.259"}}])

    def test_avisa_si_inpi_no_respondio_en_alguna_marca(self):
        r = correr([{"acta": "111111", "filas": self.FILAS, "sel": ["111111"], "respuesta": NUEVO,
                     "preparar": {"unico": False, "sinVerificar": 2}, "accion": "await anLoteADrive();"}])[0]
        self.assertIn("2 sin verificar", r["lote"]["texto"])

    def test_si_falla_muestra_el_motivo_y_deja_los_botones_como_estaban(self):
        r = correr([{"acta": "111111", "filas": self.FILAS, "sel": ["111111", "222222"], "falla": "La API de Google Drive no está activada",
                     "accion": "await anLoteADrive();"}])[0]
        self.assertEqual(r["lote"]["texto"], "No se pudo guardar en Drive: La API de Google Drive no está activada")
        self.assertEqual(r["lote"]["clase"], "msg err")
        self.assertFalse(r["lotePdf"]["apagado"])
        self.assertFalse(r["loteDrive"]["apagado"])

    def test_sin_marcas_tildadas_no_hace_nada(self):
        r = correr([{"acta": "111111", "filas": self.FILAS, "sel": [], "accion": "await anLoteADrive();"}])[0]
        self.assertEqual(r["llamadas"], [])


@unittest.skipUnless(shutil.which("node"), "no hay Node")
class BotonesDelLote(unittest.TestCase):
    FILAS = [{"acta": "111111"}, {"acta": "222222"}]

    def _botones(self, sel, drive=True, ocupado=False):
        r = correr([{"acta": "111111", "filas": self.FILAS, "sel": sel, "drive": drive,
                     "accion": f"anBotonesLote({str(ocupado).lower()});"}])[0]
        return r["lotePdf"], r["loteDrive"]

    def test_sin_marcas_tildadas_los_dos_apagados(self):
        pdf, drive = self._botones([])
        self.assertTrue(pdf["apagado"] and drive["apagado"])
        self.assertEqual(pdf["texto"], "Exportar PDF con las marcas tildadas")
        self.assertEqual(drive["texto"], "📁 Guardar en Drive las marcas tildadas")

    def test_con_marcas_tildadas_los_dos_prendidos_y_cuentan(self):
        pdf, drive = self._botones(["111111"])
        self.assertFalse(pdf["apagado"] or drive["apagado"])
        self.assertEqual(pdf["texto"], "Exportar PDF con 1 marca")
        self.assertEqual(drive["texto"], "📁 Guardar en Drive 1 marca")
        pdf, drive = self._botones(["111111", "222222"])
        self.assertEqual(pdf["texto"], "Exportar PDF con 2 marcas")
        self.assertEqual(drive["texto"], "📁 Guardar en Drive 2 marcas")

    def test_sin_drive_conectado_solo_el_de_drive_queda_apagado(self):
        pdf, drive = self._botones(["111111"], drive=False)
        self.assertFalse(pdf["apagado"])
        self.assertTrue(drive["apagado"])

    def test_mientras_trabaja_los_dos_apagados(self):
        pdf, drive = self._botones(["111111"], ocupado=True)
        self.assertTrue(pdf["apagado"] and drive["apagado"])


if __name__ == "__main__":
    unittest.main()
