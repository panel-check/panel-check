"""Borrador del formulario «Agendar llamada o reunión» (panel/static/calendario_comun.js).

Corre el código real del borrador con Node (con un document y un localStorage de mentira):
- lo que se escribe se guarda y se recupera igual en un formulario nuevo (título, fecha, hora, modalidad,
  actas, notas, mail, texto de la IA y las marcas de «lo puso la persona a mano»);
- si no hay nada escrito no se guarda borrador (y se borra el que hubiera);
- no se guarda al editar un evento, en un recordatorio, fuera del formulario embebido, ni antes de restaurar
  (si no, el formulario vacío pisaría el borrador);
- un borrador de más de 7 días se descarta y un día que ya pasó no se recupera (queda hoy);
- «Meet» no se recupera si no está disponible.

Se saltea si no hay Node.
    cd scripts && python3 -m unittest test_agenda_borrador
"""
import json
import pathlib
import shutil
import subprocess
import unittest

JS_FUENTE = pathlib.Path(__file__).resolve().parent.parent / "panel" / "static" / "calendario_comun.js"

JS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const casos = JSON.parse(process.argv[3]);

function funcion(nombre) {
  const ini = src.indexOf("function " + nombre + "(");
  if (ini < 0) throw new Error("no está " + nombre);
  let i = src.indexOf("{", ini), prof = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") prof++;
    else if (src[i] === "}" && --prof === 0) break;
  }
  return src.slice(ini, i + 1);
}

const IDS = ["mc-ia-texto", "mc-titulo", "mc-fecha", "mc-hasta", "mc-todo", "mc-hora", "mc-dur", "mc-lugar",
             "mc-actas", "mc-notas", "mc-avisar", "mc-email"];

function formulario(valores) {
  const el = {};
  for (const id of IDS) el[id] = { id, value: "", checked: false, dataset: {} };
  el["mc-dur"].value = "30";
  const radios = ["meet", "llamada", "presencial"].map((v) => ({ value: v, checked: false, disabled: false }));
  for (const [k, v] of Object.entries(valores || {})) {
    if (k === "mod") { radios.forEach((r) => { r.checked = r.value === v; }); continue; }
    if (k === "sinMeet") { radios[0].disabled = true; continue; }
    if (k.endsWith("Auto") || k.endsWith("Manual")) continue;
    if (typeof v === "boolean") el[k].checked = v; else el[k].value = v;
  }
  if (valores && valores.tituloAuto) el["mc-titulo"].dataset.auto = "1";
  if (valores && valores.avisarManual) el["mc-avisar"].dataset.manual = "1";
  if (valores && valores.emailManual) el["mc-email"].dataset.manual = "1";
  const document = {
    getElementById: (id) => el[id],
    querySelector: (sel) => {
      if (sel === 'input[name="mc-mod"]:checked') return radios.find((r) => r.checked) || null;
      const m = sel.match(/input\[name="mc-mod"\]\[value="(\w+)"\]/);
      return m ? radios.find((r) => r.value === m[1]) || null : null;
    },
  };
  return { el, radios, document };
}

const salida = [];
for (const c of casos) {
  const almacen = {};
  if (c.previo !== undefined) almacen["kom-agenda-borrador"] = JSON.stringify(c.previo);
  const localStorage = {
    getItem: (k) => (k in almacen ? almacen[k] : null),
    setItem: (k, v) => { almacen[k] = String(v); },
    removeItem: (k) => { delete almacen[k]; },
  };
  const origen = formulario(c.escrito);
  const destino = formulario(c.destino);
  let doc = origen.document;
  const estado = {
    ctx: c.ctx === undefined ? { embebido: true, evento: null, recordatorio: false } : c.ctx,
    listo: c.listo !== false,
    marca: c.marca || null,
    nombreTexto: c.nombreTexto || null,
  };
  const cuerpo = [
    "const _CAL_BORRADOR_CLAVE = 'kom-agenda-borrador';",
    "const _CAL_BORRADOR_VIDA_MS = 7 * 24 * 3600 * 1000;",
    "let _calBorradorListo = est.listo; let _calModalCtx = est.ctx;",
    "let _calMarcaTitulo = est.marca, _calNombreTexto = est.nombreTexto;",
    "const calHoy = () => '2026-10-13';",
    "const _calDuracion = (min) => { document.getElementById('mc-dur').value = String(min); };",
    "const _doc = { actual: null };",
    funcion("_calBorradorLeer"), funcion("_calBorradorBorrar"), funcion("_calBorradorActivo"),
    funcion("_calBorradorGuardar"), funcion("_calBorradorRestaurar"),
    "return { guardar: _calBorradorGuardar, restaurar: _calBorradorRestaurar, marca: () => [_calMarcaTitulo, _calNombreTexto] };",
  ].join("\n");
  // El formulario «actual» (document) se cambia entre guardar (origen) y restaurar (destino).
  const f = new Function("est", "localStorage", "document", cuerpo);
  const api = f(estado, localStorage, { getElementById: (id) => doc.getElementById(id), querySelector: (s) => doc.querySelector(s) });
  api.guardar();
  const guardado = almacen["kom-agenda-borrador"] ? JSON.parse(almacen["kom-agenda-borrador"]) : null;
  doc = destino.document;
  const hubo = api.restaurar();
  const v = (id) => destino.el[id];
  salida.push({
    guardado: guardado ? { titulo: guardado.titulo, ts_ok: typeof guardado.ts === "number" } : null,
    hubo,
    titulo: v("mc-titulo").value, tituloAuto: !!v("mc-titulo").dataset.auto,
    fecha: v("mc-fecha").value, hasta: v("mc-hasta").value, todo: v("mc-todo").checked, hora: v("mc-hora").value,
    dur: v("mc-dur").value, lugar: v("mc-lugar").value, actas: v("mc-actas").value, notas: v("mc-notas").value,
    avisar: v("mc-avisar").checked, avisarManual: !!v("mc-avisar").dataset.manual,
    email: v("mc-email").value, emailManual: !!v("mc-email").dataset.manual, ia: v("mc-ia-texto").value,
    mod: (destino.radios.find((r) => r.checked) || {}).value || "",
    marcas: api.marca(),
    quedaEnAlmacen: !!almacen["kom-agenda-borrador"],
  });
}
console.log(JSON.stringify(salida));
"""


def correr(casos):
    r = subprocess.run(["node", "-e", JS, "x", str(JS_FUENTE), json.dumps(casos)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


ESCRITO = {
    "mc-ia-texto": "BUGGY'S, acta 4688297, 18 de octubre 9am", "mc-titulo": "Reunión virtual (BUGGY'S)", "tituloAuto": True,
    "mc-fecha": "2026-10-18", "mc-hasta": "2026-10-18", "mc-hora": "09:00", "mc-dur": "45", "mod": "llamada",
    "mc-lugar": "11 5555-1234", "mc-actas": "4688297", "mc-notas": "Traer el poder", "mc-avisar": True,
    "mc-email": "persona@mail.com", "emailManual": True,
}


@unittest.skipUnless(shutil.which("node"), "no hay Node")
class BorradorAgendar(unittest.TestCase):
    def test_se_guarda_y_se_recupera(self):
        (r,) = correr([{"escrito": ESCRITO}])
        self.assertTrue(r["guardado"] and r["guardado"]["ts_ok"])
        self.assertTrue(r["hubo"])
        self.assertEqual((r["titulo"], r["tituloAuto"]), ("Reunión virtual (BUGGY'S)", True))
        self.assertEqual((r["fecha"], r["hora"], r["dur"], r["mod"]), ("2026-10-18", "09:00", "45", "llamada"))
        self.assertEqual((r["actas"], r["notas"], r["lugar"]), ("4688297", "Traer el poder", "11 5555-1234"))
        self.assertEqual((r["email"], r["emailManual"], r["avisar"]), ("persona@mail.com", True, True))
        self.assertIn("BUGGY'S", r["ia"])

    def test_la_marca_del_titulo_automatico_se_recupera(self):
        (r,) = correr([{"escrito": ESCRITO, "marca": "BUGGY'S CALIDAD Y SABOR", "nombreTexto": "BUGGY'S"}])
        self.assertEqual(r["marcas"], ["BUGGY'S CALIDAD Y SABOR", "BUGGY'S"])

    def test_sin_nada_escrito_no_hay_borrador_y_se_borra_el_viejo(self):
        vacio = {"mc-fecha": "2026-10-13", "mc-hasta": "2026-10-13", "mc-avisar": True}
        viejo = {"ts": 10**13, "titulo": "viejo"}
        a, b = correr([{"escrito": vacio}, {"escrito": vacio, "previo": viejo}])
        self.assertIsNone(a["guardado"])
        self.assertFalse(a["hubo"])
        self.assertFalse(b["quedaEnAlmacen"])   # lo escrito en blanco reemplaza al borrador viejo

    def test_solo_el_formulario_embebido_de_uno_nuevo(self):
        casos = [
            {"escrito": ESCRITO, "ctx": {"embebido": False, "evento": None, "recordatorio": False}},
            {"escrito": ESCRITO, "ctx": {"embebido": True, "evento": {"id": 1}, "recordatorio": False}},
            {"escrito": ESCRITO, "ctx": {"embebido": True, "evento": None, "recordatorio": True}},
            {"escrito": ESCRITO, "ctx": None},
        ]
        for r in correr(casos):
            self.assertIsNone(r["guardado"])
            self.assertFalse(r["hubo"])

    def test_antes_de_restaurar_no_se_pisa_el_borrador(self):
        previo = {"ts": 10**13, "titulo": "Llamar a Pérez", "dur": "30", "fecha": "2026-10-20", "hasta": "2026-10-20"}
        (r,) = correr([{"escrito": {}, "listo": False, "previo": previo}])
        self.assertTrue(r["quedaEnAlmacen"])      # no se tocó
        self.assertTrue(r["hubo"])
        self.assertEqual(r["titulo"], "Llamar a Pérez")

    def test_borrador_viejo_se_descarta(self):
        viejo = {"ts": 1, "titulo": "de hace mucho"}
        (r,) = correr([{"escrito": {}, "listo": False, "previo": viejo}])
        self.assertFalse(r["hubo"])
        self.assertFalse(r["quedaEnAlmacen"])

    def test_un_dia_que_ya_paso_no_se_recupera(self):
        (r,) = correr([{"escrito": dict(ESCRITO, **{"mc-fecha": "2026-10-01", "mc-hasta": "2026-10-01"}),
                        "destino": {"mc-fecha": "2026-10-13", "mc-hasta": "2026-10-13"}}])
        self.assertEqual(r["titulo"], "Reunión virtual (BUGGY'S)")
        self.assertEqual((r["fecha"], r["hasta"]), ("2026-10-13", "2026-10-13"))   # queda hoy

    def test_meet_no_disponible_no_se_fuerza(self):
        (r,) = correr([{"escrito": dict(ESCRITO, mod="meet"), "destino": {"sinMeet": True, "mod": "llamada"}}])
        self.assertEqual(r["mod"], "llamada")


if __name__ == "__main__":
    unittest.main()
