"""Pruebas de la presentación de oposición por acta (panel/presentacion_oposicion.py), sin red ni base.
Correr: cd scripts && python3 -m unittest test_presentacion_oposicion"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))

try:
    import presentacion_oposicion as po  # noqa: E402
except ImportError as e:  # sin las dependencias del panel (requests, bs4, PIL) no se puede probar
    po = None
    MOTIVO = str(e)


HTML = """
<div><h4>GESTION DEL TRAMITE</h4></div><div><label>AGENTE: 0 PARTICULAR</label></div></div></div>
<label class="input">DENOMINACION: PUNTO MEDIO</label>
<label class="input">TIPO DE MARCA: Denominativa</label>
<label class="input">CLASE: 41</label>
<label class="input">LIMITACION: actuaciones musicales ;actuaciones en directo ;</label>
<div id="collapse-six"><label class="input">FECHA: 07/10/2026</label><label class="input">NUMERO: 11132</label></div>
<script>
var opos = JSON.parse('[{"Oponente":"BUSSO, DAVID","Fecha_Presentacion":"/Date(1793836800000)/","Fecha_Notificacion":"01/01/0001","Fecha_Vencimiento":"01/01/0001","Fecha_Levantamiento":"01/01/0001","Agente":"1085","Caracter":"Apoderado","Fundamento":"Se opone con su marca Acta N\\u00b0 3966480 clase 41"}]');
var vistas = JSON.parse('[{"Tipo":"Oposiciones","Fecha_Vista":"/Date(1794787200000)/","Fecha_Notificacion":"/Date(1794787200000)/","Fecha_Vencimiento":"/Date(1802563200000)/"},{"Tipo":"Administrativas","Fecha_Vista":"/Date(1790000000000)/"}]');
</script>
"""


@unittest.skipIf(po is None, "faltan dependencias del panel")
class Fechas(unittest.TestCase):
    def test_cierre_es_publicacion_mas_30_dias(self):
        self.assertEqual(po.cierre_oposiciones("2026-10-07"), "2026-11-06")   # caso PUNTO MEDIO
        self.assertIsNone(po.cierre_oposiciones(None))

    def test_tres_meses_respeta_fin_de_mes(self):
        self.assertEqual(po.sumar_meses(dt.date(2026, 11, 11), 3), dt.date(2027, 2, 11))
        self.assertEqual(po.sumar_meses(dt.date(2026, 11, 30), 3), dt.date(2027, 2, 28))
        self.assertEqual(po.sumar_meses(dt.date(2026, 10, 14), 3), dt.date(2027, 1, 14))

    def test_acta(self):
        self.assertEqual(po.validar_acta(" 4.793.580 "), "4793580")
        with self.assertRaises(po.ActaInvalida):
            po.validar_acta("abc")


@unittest.skipIf(po is None, "faltan dependencias del panel")
class Expediente(unittest.TestCase):
    def test_lee_marca_oposiciones_y_vistas(self):
        e = po.leer_expediente(HTML, "4793580")
        self.assertEqual(e["denominacion"], "PUNTO MEDIO")
        self.assertEqual(e["clase"], "41")
        self.assertEqual(e["publicaciones"][0]["fecha"], "2026-10-07")
        self.assertEqual(len(e["oposiciones"]), 1)
        self.assertEqual(len(e["vistas_oposicion"]), 1)   # la vista «Administrativas» no cuenta

    def test_notificacion_sale_de_vistas_y_vencimiento_de_inpi(self):
        e = po.leer_expediente(HTML, "4793580")
        ops = po.armar_oposiciones(e["oposiciones"], "4793580", {}, e["vistas_oposicion"])
        self.assertEqual(ops[0]["marcas"][0]["acta"], "3966480")
        self.assertIsNotNone(ops[0]["notificacion"])
        p = po.plazos(e["publicaciones"], ops, e["vistas_oposicion"])
        self.assertEqual(p["cierre_oposiciones"], "2026-11-06")
        self.assertEqual(p["vencimiento_negociacion"], ops[0]["vencimiento"])
        self.assertEqual(p["oposiciones_vigentes"], 1)

    def test_sin_vencimiento_se_calcula_tres_meses(self):
        ops = [{"notificacion": "2026-11-11", "vencimiento": None, "levantamiento": None}]
        self.assertEqual(po.plazos([], ops, [])["vencimiento_negociacion"], "2027-02-11")

    def test_levantadas_no_cuentan(self):
        ops = [{"notificacion": "2026-11-11", "vencimiento": "2027-02-11", "levantamiento": "2026-12-01"}]
        p = po.plazos([], ops, [])
        self.assertEqual(p["oposiciones_vigentes"], 0)
        self.assertIsNone(p["vencimiento_negociacion"])

    def test_marca_opuesta_inexistente_no_se_muestra(self):
        items = [{"oponente": "X", "fundamento": "Acta N° 1234567"}]
        ops = po.armar_oposiciones(items, "999", {"1234567": {"estado": "no_existe"}}, [])
        self.assertEqual(ops[0]["marcas"], [])


@unittest.skipIf(po is None, "faltan dependencias del panel")
class Informe(unittest.TestCase):
    def test_resumen_sin_costos(self):
        d = {"fecha_analisis": "10/11/2026", "chances": {"nivel": "Medias a altas", "motivo": "Especialidad"},
             "resumen_express": {"camino_sugerido": "Carta compromiso"},
             "palancas": [{"palanca": "Limitación", "detalle": "No se tocan"}],
             "caminos": [{"titulo": "Negociar", "detalle": "Coexistencia", "costo": "$350.000", "recomendado": True}]}
        r = po.resumen_informe(d)
        self.assertEqual(r["chances"], "medias a altas")
        self.assertEqual(r["caminos"][0], {"titulo": "Negociar", "detalle": "Coexistencia", "recomendado": True})
        self.assertNotIn("350", str(r))


if __name__ == "__main__":
    unittest.main()
