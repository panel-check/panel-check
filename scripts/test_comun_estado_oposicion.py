"""Etiquetas de estado de la oposición del panel (panel/static/comun.js):
«sin notificar», «ya notificada» y «recién ingresada» se calculan al mostrar,
con la fecha de hoy, en vez de quedar con lo que guardó la última revisión.

Reglas (pedido del 24/10/2026):
  - Cierre del plazo = fecha de publicación (Hoja Publicación) + 30 días corridos.
  - INPI notifica los miércoles: la notificación es el primer miércoles desde el
    cierre (si el cierre cae miércoles, ese día). Antes de esa fecha: «sin
    notificar» con la fecha en que se notificará; desde esa fecha: «ya notificada».
  - «Recién ingresada» solo los primeros 3 días desde la presentación.

Corre el código real de comun.js con Node. Se saltea si no hay Node.
    cd scripts && python3 -m unittest test_comun_estado_oposicion
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
const hoy = process.argv[4];

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
function constante(nombre) {
  const m = new RegExp("^const " + nombre + " = .*?;\\s*$", "ms").exec(js);
  if (!m) throw new Error("no está " + nombre);
  return m[0];
}

const cuerpo = [
  constante("ESTADOS_OPOSICION"),
  constante("DIAS_PLAZO_OPOSICION"), constante("DIAS_RECIEN_INGRESADA"), constante("_MS_DIA"),
  constante("_DIAS_SEMANA"),
  funcion("_isoAMs"), funcion("_msAFecha"), funcion("_hoyArgentinaIso"), funcion("plazoOposicion"),
  funcion("_fmtFechaCorta"), funcion("_presentacionOposicionIso"), funcion("estadoOposicionEfectivo"),
  "const e = estadoOposicionEfectivo(row, hoy);",
  "return e && { clave: e.clave, texto: e.texto, sirve: e.sirve, detalle: e.detalle,",
  "  cierre: e.plazo && _msAFecha(e.plazo.cierre), notificacion: e.plazo && _msAFecha(e.plazo.notificacion) };",
].join("\n");
console.log(JSON.stringify(new Function("row", "hoy", cuerpo)(row, hoy)));
"""


def correr(row, hoy):
    r = subprocess.run(["node", "-e", JS, "x", str(JS_FILE), json.dumps(row), hoy],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


def fila(estado, **extra):
    # Boletín publicado el miércoles 30/09/2026: cierra el viernes 30/10, se notifica el miércoles 04/11.
    return {"estado_oposicion": estado, "estado_oposicion_detalle": "detalle de la base",
            "fecha_publicacion": "2026-09-30", **extra}


@unittest.skipUnless(shutil.which("node"), "no hay Node")
class EstadoOposicionEfectivo(unittest.TestCase):
    def test_sin_notificar_dice_cuando_se_notifica(self):
        r = correr(fila("sin_notificar"), "2026-10-24")
        self.assertEqual(r["clave"], "sin_notificar")
        self.assertEqual(r["cierre"], "30/10/2026")
        self.assertEqual(r["notificacion"], "04/11/2026")
        self.assertIn("miércoles 04/11/2026", r["detalle"])
        self.assertIn("viernes 30/10/2026", r["detalle"])  # el plazo todavía no cerró

    def test_sigue_sin_notificar_hasta_el_dia_anterior_al_miercoles(self):
        self.assertEqual(correr(fila("sin_notificar"), "2026-11-03")["clave"], "sin_notificar")

    def test_pasado_el_miercoles_pasa_a_ya_notificada(self):
        for hoy in ("2026-11-04", "2026-11-10"):
            r = correr(fila("sin_notificar"), hoy)
            self.assertEqual(r["clave"], "ya_notificada", hoy)
            self.assertEqual(r["texto"], "ya notificada")
            self.assertTrue(r["sirve"])  # el titular recién empieza a correr su plazo: sigue sirviendo

    def test_plazo_que_ya_cerro_pero_falta_el_miercoles(self):
        r = correr(fila("sin_notificar"), "2026-11-02")
        self.assertEqual(r["clave"], "sin_notificar")
        self.assertIn("cerró el 30/10/2026", r["detalle"])

    def test_cierre_en_miercoles_se_notifica_ese_dia(self):
        # Publicada el lunes 05/10/2026 + 30 días = miércoles 04/11/2026: cierra y se notifica el mismo día.
        fila_miercoles = fila("sin_notificar", fecha_publicacion="2026-10-05")
        r = correr(fila_miercoles, "2026-11-03")
        self.assertEqual((r["cierre"], r["notificacion"]), ("04/11/2026", "04/11/2026"))
        self.assertEqual(r["clave"], "sin_notificar")
        self.assertEqual(correr(fila_miercoles, "2026-11-04")["clave"], "ya_notificada")

    def test_sin_fecha_de_publicacion_queda_lo_que_dice_la_base(self):
        r = correr({"estado_oposicion": "sin_notificar", "estado_oposicion_detalle": "detalle de la base"}, "2026-10-24")
        self.assertEqual(r["clave"], "sin_notificar")
        self.assertEqual(r["detalle"], "detalle de la base")
        self.assertIsNone(r["cierre"])

    def test_recien_ingresada_dentro_de_los_tres_dias(self):
        row = fila("oposicion_sin_detalle", oposicion_fecha_presentacion="2026-10-22")
        for hoy in ("2026-10-22", "2026-10-25"):
            r = correr(row, hoy)
            self.assertEqual(r["clave"], "oposicion_sin_detalle", hoy)
            self.assertEqual(r["texto"], "recién ingresada")

    def test_recien_ingresada_desaparece_despues_de_tres_dias(self):
        row = fila("oposicion_sin_detalle", oposicion_fecha_presentacion="2026-10-20")
        r = correr(row, "2026-10-24")
        self.assertEqual(r["clave"], "sin_notificar")  # la etiqueta «recién ingresada» ya no está
        self.assertIn("miércoles 04/11/2026", r["detalle"])
        self.assertEqual(correr(row, "2026-11-05")["clave"], "ya_notificada")

    def test_recien_ingresada_toma_la_fecha_de_la_grilla(self):
        # 24/10/2026 00:00 en Argentina = 03:00 UTC = 1792810800000 ms
        row = fila("oposicion_sin_detalle", detalle_oposicion="/Date(1792810800000)/ - Opo. de Marcas - OPOSICION")
        self.assertEqual(correr(row, "2026-10-25")["clave"], "oposicion_sin_detalle")
        self.assertEqual(correr(row, "2026-10-28")["clave"], "sin_notificar")

    def test_recien_ingresada_sin_fecha_no_se_muestra(self):
        self.assertIsNone(correr(fila("oposicion_sin_detalle"), "2026-10-24"))

    def test_otros_estados_no_cambian(self):
        r = correr(fila("notificada_en_plazo", oposicion_fecha_vencimiento="2026-11-20"), "2026-11-10")
        self.assertEqual(r["clave"], "notificada_en_plazo")
        self.assertEqual(r["detalle"], "detalle de la base")
        self.assertEqual(correr(fila("contestada"), "2026-11-10")["clave"], "contestada")
        self.assertIsNone(correr({"estado_oposicion": None}, "2026-11-10"))


if __name__ == "__main__":
    unittest.main()
