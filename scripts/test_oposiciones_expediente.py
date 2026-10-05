"""
Pruebas de oposiciones_expediente.py (sin red). Correr con:
    cd scripts && python3 -m unittest test_oposiciones_expediente -v

El HTML de ejemplo replica la estructura real del expediente de INPI
(acta 4764327, 05/10/2026): el portal arma las tablas con
`opos = JSON.parse('[...]')`; una fecha vacía viene como 01/01/0001.
"""

import datetime as dt
import unittest

from oposiciones_expediente import (
    clasificar_estado_oposicion,
    parsear_expediente,
)

VACIA = "\\/Date(-62135586000000)\\/"


def pagina(opos="", vistas="", gestion=""):
    return f"""
    <h4>GESTION DEL TRAMITE</h4></div><div class="x">{gestion}</div></div></div>
    <script>
    if( JSON.parse('{opos}' != '')) {{ opos = JSON.parse('{opos}'); }}else{{opos = null;}}
    if( JSON.parse('{vistas}' != '')) {{ vistas = JSON.parse('{vistas}'); }}else{{vistas = null;}}
    </script>"""


def opo(pres=1789488720000, notif=VACIA, venc=VACIA, lev=VACIA, agente=611):
    return ('[{"Oponente":"O\\\'BRIEN SA","Fecha_Presentacion":"\\/Date(%d)\\/",'
            '"Fecha_Notificacion":"%s","Fecha_Vencimiento":"%s","Numero":777700,'
            '"Agente":%d,"Caracter":"Apoderado","Fecha_Levantamiento":"%s",'
            '"Fundamento":"confundible con \\\'BALI STONE\\\' (Nro. 1)",'
            '"Motivo_Levantamiento":null}]') % (pres, notif, venc, agente, lev)


def ms(iso):
    d = dt.date.fromisoformat(iso)
    return int(dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.timezone.utc).timestamp() * 1000)


GESTION_PARTICULAR = '<label>AGENTE: <span> 0 PARTICULAR </span></label> <label class="input">CARACTER:<span class="text-danger"> </span></label>'
GESTION_APODERADO = '<label>AGENTE: <span> 1234 PEREZ </span></label> <label class="input">CARACTER:<span class="text-danger"> Apoderado </span></label>'
HOY = dt.date(2026, 10, 5)
PUB = "2026-09-09"


def clasificar(html, archivos=None):
    return clasificar_estado_oposicion(parsear_expediente(html), archivos or [], PUB, HOY)


class Parseo(unittest.TestCase):
    def test_lee_oposicion_con_apostrofe_y_fechas_vacias(self):
        exp = parsear_expediente(pagina(opos=opo()))
        self.assertFalse(exp["error_lectura"])
        o = exp["oposiciones"][0]
        self.assertEqual(o["presentacion"], "2026-09-15")
        self.assertIsNone(o["notificacion"])
        self.assertIsNone(o["vencimiento"])
        self.assertEqual(o["agente_oponente"], "611 (Apoderado)")

    def test_tablas_vacias(self):
        exp = parsear_expediente(pagina())
        self.assertEqual(exp["oposiciones"], [])
        self.assertFalse(exp["error_lectura"])


class Estados(unittest.TestCase):
    def test_sin_oposicion(self):
        r = clasificar(pagina(gestion=GESTION_PARTICULAR))
        self.assertEqual(r["estado"], "sin_oposicion")
        self.assertIsNone(r["sirve"])

    def test_sin_notificar_sirve(self):
        r = clasificar(pagina(opos=opo(), gestion=GESTION_PARTICULAR))
        self.assertEqual(r["estado"], "sin_notificar")
        self.assertTrue(r["sirve"])

    def test_notificada_en_plazo_sirve(self):
        n, v = "\\/Date(%d)\\/" % ms("2026-10-01"), "\\/Date(%d)\\/" % ms("2026-10-31")
        r = clasificar(pagina(opos=opo(notif=n, venc=v), gestion=GESTION_PARTICULAR))
        self.assertEqual(r["estado"], "notificada_en_plazo")
        self.assertEqual(r["vencimiento"], "2026-10-31")
        self.assertTrue(r["sirve"])

    def test_plazo_vencido_sirve(self):
        n, v = "\\/Date(%d)\\/" % ms("2026-08-20"), "\\/Date(%d)\\/" % ms("2026-09-20")
        r = clasificar(pagina(opos=opo(pres=ms("2026-09-10"), notif=n, venc=v), gestion=GESTION_PARTICULAR))
        self.assertEqual(r["estado"], "plazo_vencido")
        self.assertTrue(r["sirve"])

    def test_contestada_por_grilla_no_sirve(self):
        arch = [{"Indice": "Escrito", "Referencia": "Contesta Oposicion", "Fecha": "%s" % "20/09/2026"}]
        r = clasificar(pagina(opos=opo(), gestion=GESTION_PARTICULAR), arch)
        self.assertEqual(r["estado"], "contestada")
        self.assertFalse(r["sirve"])

    def test_contestacion_vieja_no_cuenta(self):
        arch = [{"Indice": "Escrito", "Referencia": "Contesta Vista", "Fecha": "01/03/2026"}]
        r = clasificar(pagina(opos=opo(), gestion=GESTION_PARTICULAR), arch)
        self.assertEqual(r["estado"], "sin_notificar")

    def test_levantada_no_sirve(self):
        lev = "\\/Date(%d)\\/" % ms("2026-09-30")
        r = clasificar(pagina(opos=opo(lev=lev), gestion=GESTION_PARTICULAR))
        self.assertEqual(r["estado"], "levantada")
        self.assertFalse(r["sirve"])

    def test_apoderado_del_titular_no_sirve(self):
        r = clasificar(pagina(opos=opo(), gestion=GESTION_APODERADO))
        self.assertEqual(r["estado"], "con_apoderado")
        self.assertFalse(r["sirve"])
        self.assertTrue(r["representacion_confirmada"])

    def test_poder_en_grilla_sin_agente_del_titular_es_ambiguo_y_sigue_sirviendo(self):
        # Caso típico: el abogado del OPONENTE acompaña su poder.
        arch = [{"Indice": "Escrito", "Referencia": "Acompaña Poder", "Fecha": "22/09/2026"}]
        r = clasificar(pagina(opos=opo(), gestion=GESTION_PARTICULAR), arch)
        self.assertEqual(r["estado"], "sin_notificar")
        self.assertTrue(r["sirve"])
        self.assertTrue(r["posible_apoderado"])
        self.assertFalse(r["representacion_confirmada"])

    def test_oposicion_en_grilla_antes_de_estar_en_el_expediente(self):
        arch = [{"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "20/09/2026"}]
        r = clasificar(pagina(gestion=GESTION_PARTICULAR), arch)
        self.assertEqual(r["estado"], "oposicion_sin_detalle")
        self.assertTrue(r["sirve"])

    def test_oposicion_vieja_anterior_a_la_publicacion_se_ignora(self):
        r = clasificar(pagina(opos=opo(pres=ms("2026-03-01")), gestion=GESTION_PARTICULAR))
        self.assertEqual(r["estado"], "sin_oposicion")

    def test_solo_vista_de_oficio(self):
        arch = [{"Indice": "Vista de Marcas", "Referencia": "Vista", "Fecha": "20/09/2026"}]
        r = clasificar(pagina(gestion=GESTION_PARTICULAR), arch)
        self.assertEqual(r["estado"], "vista_pendiente")
        self.assertTrue(r["sirve"])


if __name__ == "__main__":
    unittest.main()
