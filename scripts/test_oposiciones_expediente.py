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


def vista(contestacion=VACIA, fecha="2026-04-15", notif="2026-04-20", venc="2026-05-22"):
    """Fila de la tabla VISTAS con los campos reales del expediente."""
    def f(iso):
        return VACIA if not iso else "\\/Date(%d)\\/" % ms(iso)
    if contestacion != VACIA and contestacion:
        contestacion = f(contestacion)
    return ('[{"Fecha_Contestacion":"%s","Fecha_Vista":"%s","Fecha_Notificacion":"%s",'
            '"Fecha_Vencimiento":"%s","Tipo":"Administrativas","Cod_VistaExp":1,"Acta":1}]') % (
        contestacion, f(fecha), f(notif), f(venc))


class CasosReales(unittest.TestCase):
    """Los tres casos que dio el usuario el 05/10/2026."""

    def test_4688778_vista_vieja_contestada_y_oposicion_sin_notificar(self):
        # Contestó una vista en abril (antes de publicarse): no importa. La oposición
        # nueva (presentada el 23/09, sin notificar) NO fue contestada -> sirve.
        arch = [
            {"Indice": "Formulario", "Referencia": None, "Fecha": "24/09/2026"},
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "23/09/2026"},
            {"Indice": "Hoja Publicacion", "Referencia": "11120", "Fecha": "16/09/2026"},
            {"Indice": "Recibo de Ingreso", "Referencia": "Contestacion de vistas", "Fecha": "27/04/2026"},
            {"Indice": "Vista de Marcas", "Referencia": None, "Fecha": "15/04/2026"},
        ]
        html = pagina(opos=opo(pres=ms("2026-09-23")),
                      vistas=vista(contestacion="2026-04-27", fecha="2026-04-15", notif="2026-04-22", venc="2026-05-22"),
                      gestion=GESTION_PARTICULAR)
        r = clasificar_estado_oposicion(parsear_expediente(html), arch, "2026-09-16", HOY)
        self.assertEqual(r["estado"], "sin_notificar")
        self.assertTrue(r["sirve"])

    def test_4726688_igual_con_oponente_sin_agente(self):
        arch = [
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "17/09/2026"},
            {"Indice": "Hoja Publicacion", "Referencia": "11117", "Fecha": "16/09/2026"},
            {"Indice": "Otros", "Referencia": "Contestación de vista de fondo y documentación respaldatoria", "Fecha": "17/06/2026"},
            {"Indice": "Recibo de Ingreso", "Referencia": "Contestacion de vistas", "Fecha": "17/06/2026"},
            {"Indice": "Vista de Marcas", "Referencia": None, "Fecha": "11/06/2026"},
        ]
        html = pagina(opos=opo(pres=ms("2026-09-17"), agente=0),
                      vistas=vista(contestacion="2026-06-17", fecha="2026-06-11", notif="2026-06-16", venc="2026-08-01"),
                      gestion=GESTION_PARTICULAR)
        r = clasificar_estado_oposicion(parsear_expediente(html), arch, "2026-09-16", HOY)
        self.assertEqual(r["estado"], "sin_notificar")
        self.assertTrue(r["sirve"])

    def test_4748835_vista_sin_contestar_no_es_contestada(self):
        # Fecha_Contestacion vacía (01/01/0001): antes se leyó como "contestada".
        arch = [
            {"Indice": "Cédula de Notificación", "Referencia": None, "Fecha": "30/09/2026"},
            {"Indice": "Vista de Marcas", "Referencia": None, "Fecha": "23/09/2026"},
            {"Indice": "Hoja Publicacion", "Referencia": "11121", "Fecha": "16/09/2026"},
        ]
        html = pagina(vistas=vista(contestacion=VACIA, fecha="2026-09-10", notif="2026-09-30", venc="2026-10-15"),
                      gestion=GESTION_PARTICULAR)
        r = clasificar_estado_oposicion(parsear_expediente(html), arch, "2026-09-16", HOY)
        self.assertEqual(r["estado"], "vista_pendiente")
        self.assertTrue(r["sirve"])
        self.assertEqual(r["notificacion"], "2026-09-30")
        self.assertEqual(r["vencimiento"], "2026-10-15")
        self.assertIn("hasta el 15/10/2026", r["detalle"])

    def test_vista_ya_contestada_sin_oposicion_no_genera_alerta(self):
        arch = [{"Indice": "Vista de Marcas", "Referencia": None, "Fecha": "23/09/2026"}]
        html = pagina(vistas=vista(contestacion="2026-09-28", fecha="2026-09-10", notif="2026-09-24", venc="2026-10-15"),
                      gestion=GESTION_PARTICULAR)
        r = clasificar_estado_oposicion(parsear_expediente(html), arch, "2026-09-16", HOY)
        self.assertEqual(r["estado"], "sin_oposicion")

    def test_vista_vieja_sin_contestar_anterior_a_la_publicacion_se_ignora(self):
        html = pagina(vistas=vista(contestacion=VACIA, fecha="2026-04-15", notif="2026-04-20", venc="2026-05-22"),
                      gestion=GESTION_PARTICULAR)
        r = clasificar_estado_oposicion(parsear_expediente(html), [], "2026-09-16", HOY)
        self.assertEqual(r["estado"], "sin_oposicion")

    def test_contestacion_posterior_a_la_oposicion_via_tabla_de_vistas(self):
        html = pagina(opos=opo(pres=ms("2026-09-23")),
                      vistas=vista(contestacion="2026-10-02", fecha="2026-09-24", notif="2026-09-30", venc="2026-10-30"),
                      gestion=GESTION_PARTICULAR)
        r = clasificar_estado_oposicion(parsear_expediente(html), [], "2026-09-16", HOY)
        self.assertEqual(r["estado"], "contestada")
        self.assertFalse(r["sirve"])


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

    def test_poder_en_grilla_despues_de_notificar_al_titular_descarta_el_lead(self):
        # Un "Acompaña Poder" posterior a la notificación al titular es de su gestor.
        arch = [
            {"Indice": "Acompaña Poder", "Referencia": "ACOMPAÑA PODER", "Fecha": "02/10/2026"},
            {"Indice": "Cédula de Notificación", "Referencia": "-", "Fecha": "26/09/2026"},
            {"Indice": "Vista de Marcas", "Referencia": "-", "Fecha": "26/09/2026"},
        ]
        r = clasificar(pagina(opos=opo(), gestion=GESTION_PARTICULAR), arch)
        self.assertEqual(r["estado"], "con_apoderado")
        self.assertFalse(r["sirve"])
        self.assertTrue(r["representacion_confirmada"])
        self.assertFalse(r["posible_apoderado"])

    def test_poder_en_grilla_sin_notificacion_no_descarta(self):
        # Sin notificación conocida el poder es del oponente: manda el expediente.
        arch = [{"Indice": "Acompaña Poder", "Referencia": "ACOMPAÑA PODER", "Fecha": "22/09/2026"}]
        r = clasificar(pagina(opos=opo(), gestion=GESTION_PARTICULAR), arch)
        self.assertEqual(r["estado"], "sin_notificar")
        self.assertTrue(r["sirve"])
        self.assertFalse(r["representacion_confirmada"])

    def test_poder_en_grilla_con_notificacion_del_expediente(self):
        n, v = "\\/Date(%d)\\/" % ms("2026-09-26"), "\\/Date(%d)\\/" % ms("2026-10-26")
        arch = [{"Indice": "Ratifica Gestión", "Referencia": "-", "Fecha": "01/10/2026"}]
        r = clasificar(pagina(opos=opo(notif=n, venc=v), gestion=GESTION_PARTICULAR), arch)
        self.assertEqual(r["estado"], "con_apoderado")


class ReglasGrilla(unittest.TestCase):
    """Combinaciones exactas de la Grilla Digital (regla del 08/10/2026)."""

    def r(self, arch, gestion=GESTION_PARTICULAR, opos=None):
        html = pagina(opos=opo() if opos is None else opos, gestion=gestion)
        return clasificar(html, arch)

    def test_notificacion_efectiva_vista_seguida_de_cedula(self):
        arch = [
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "20/09/2026"},
            {"Indice": "Vista de Marcas", "Referencia": None, "Fecha": "25/09/2026"},
            {"Indice": "Cédula de Notificación", "Referencia": None, "Fecha": "26/09/2026"},
        ]
        r = self.r(arch)
        self.assertEqual(r["estado"], "notificada_en_plazo")
        self.assertTrue(r["sirve"])
        self.assertEqual(r["notificacion"], "2026-09-26")

    def test_orden_real_de_la_grilla_cedula_antes_que_vista(self):
        # La Grilla viene con lo más nuevo arriba (captura del 08/10/2026): la cédula
        # aparece ANTES que su vista, y el texto puede traer algo extra.
        arch = [
            {"Indice": "Cédula de Notificación", "Referencia": "-", "Fecha": "30/09/2026"},
            {"Indice": "Vista de Marcas", "Referencia": "-", "Fecha": "30/09/2026"},
            {"Indice": "Formulario", "Referencia": "-", "Fecha": "25/09/2026"},
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "25/09/2026"},
        ]
        r = clasificar(pagina(opos=opo(pres=ms("2026-09-25")), gestion=GESTION_PARTICULAR), arch)
        self.assertEqual(r["estado"], "notificada_en_plazo")
        self.assertEqual(r["notificacion"], "2026-09-30")

    def test_texto_extra_en_la_celda_igual_matchea(self):
        arch = [
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "20/09/2026"},
            {"Indice": "Recibo de Ingreso (digital)", "Referencia": "Escritos de Marcas - Nº 123", "Fecha": "28/09/2026"},
        ]
        self.assertEqual(self.r(arch)["estado"], "contestada")

    def test_cedula_no_consecutiva_no_notifica(self):
        arch = [
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "20/09/2026"},
            {"Indice": "Vista de Marcas", "Referencia": None, "Fecha": "25/09/2026"},
            {"Indice": "Otro", "Referencia": None, "Fecha": "25/09/2026"},
            {"Indice": "Cédula de Notificación", "Referencia": None, "Fecha": "26/09/2026"},
        ]
        self.assertEqual(self.r(arch)["estado"], "sin_notificar")

    def test_par_anterior_a_la_presentacion_no_notifica(self):
        arch = [
            {"Indice": "Vista de Marcas", "Referencia": None, "Fecha": "10/09/2026"},
            {"Indice": "Cédula de Notificación", "Referencia": None, "Fecha": "11/09/2026"},
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "20/09/2026"},
        ]
        self.assertEqual(self.r(arch)["estado"], "sin_notificar")

    def test_recibo_mas_escritos_descarta_como_atendida(self):
        arch = [
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "20/09/2026"},
            {"Indice": "Recibo de Ingreso", "Referencia": "Escritos de Marcas", "Fecha": "28/09/2026"},
        ]
        r = self.r(arch)
        self.assertEqual(r["estado"], "contestada")
        self.assertFalse(r["sirve"])

    def test_recibo_solo_no_descarta(self):
        arch = [
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "20/09/2026"},
            {"Indice": "Recibo de Ingreso", "Referencia": "Otra cosa", "Fecha": "28/09/2026"},
        ]
        self.assertEqual(self.r(arch)["estado"], "sin_notificar")

    def test_formula_desistimiento_es_levantada(self):
        arch = [
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "20/09/2026"},
            {"Indice": "Formula Desistimiento", "Referencia": "FORMULA DESISTIMIENTO", "Fecha": "28/09/2026"},
        ]
        r = self.r(arch)
        self.assertEqual(r["estado"], "levantada")
        self.assertFalse(r["sirve"])

    def test_palabras_sueltas_no_son_desistimiento_ni_poder(self):
        arch = [
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "20/09/2026"},
            {"Indice": "Escrito", "Referencia": "Levantamiento de vista", "Fecha": "22/09/2026"},
            {"Indice": "Escrito", "Referencia": "Poder del abogado", "Fecha": "23/09/2026"},
        ]
        self.assertEqual(self.r(arch)["estado"], "sin_notificar")

    # Notificación al titular (Grilla, más nuevo arriba), anterior a los poderes de abajo
    NOTIFICADA = [
        {"Indice": "Cédula de Notificación", "Referencia": "-", "Fecha": "22/09/2026"},
        {"Indice": "Vista de Marcas", "Referencia": "-", "Fecha": "22/09/2026"},
    ]

    def test_ratifica_gestion_en_oposicion_descarta(self):
        arch = [
            {"Indice": "Ratifica Gestión en Oposición", "Referencia": "RATIFICA GESTIÓN EN OPOSICIÓN", "Fecha": "24/09/2026"},
        ] + self.NOTIFICADA
        r = self.r(arch)
        self.assertEqual(r["estado"], "con_apoderado")
        self.assertFalse(r["sirve"])

    def test_ratifica_gestion_descarta(self):
        arch = [{"Indice": "Ratifica Gestión", "Referencia": None, "Fecha": "24/09/2026"}] + self.NOTIFICADA
        self.assertEqual(self.r(arch)["estado"], "con_apoderado")

    def test_poder_antes_de_la_notificacion_es_del_oponente(self):
        # Mismo día que la oposición, antes de que se notifique al titular.
        arch = [
            {"Indice": "Acompaña Poder", "Referencia": "ACOMPAÑA PODER", "Fecha": "20/09/2026"},
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "20/09/2026"},
        ] + self.NOTIFICADA
        r = self.r(arch)
        self.assertEqual(r["estado"], "notificada_en_plazo")
        self.assertTrue(r["sirve"])

    def test_poder_del_dia_de_una_oposicion_posterior_a_la_notificacion_no_descarta(self):
        # Un tercer oponente se presenta después de notificado el titular y acompaña su
        # poder el mismo día: sigue siendo poder de un oponente.
        arch = [
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "28/09/2026"},
            {"Indice": "Acompaña Poder", "Referencia": "ACOMPAÑA PODER", "Fecha": "28/09/2026"},
            {"Indice": "Cédula de Notificación", "Referencia": "-", "Fecha": "22/09/2026"},
            {"Indice": "Vista de Marcas", "Referencia": "-", "Fecha": "22/09/2026"},
        ]
        dos = "[" + opo(pres=ms("2026-09-15"))[1:-1] + "," + opo(pres=ms("2026-09-28"))[1:-1] + "]"
        r = clasificar(pagina(opos=dos, gestion=GESTION_PARTICULAR), arch)
        self.assertEqual(r["estado"], "notificada_en_plazo")
        self.assertFalse(r["representacion_confirmada"])

    def test_acta_4759596_poder_de_un_oponente_no_descarta(self):
        # Caso real (08/10/2026): dos oposiciones (Paladini 11/09, Bruzzi 25/09), cada una
        # con su agente. El "Acompaña Poder" del 11/09 es de Paladini, que se opone.
        # La vista/cédula del 30/09 notificó la oposición al titular (sin agente).
        arch = [
            {"Indice": "Cédula de Notificación", "Referencia": "-", "Fecha": "30/09/2026"},
            {"Indice": "Vista de Marcas", "Referencia": "-", "Fecha": "30/09/2026"},
            {"Indice": "Formulario", "Referencia": "-", "Fecha": "25/09/2026"},
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "25/09/2026"},
            {"Indice": "Formulario", "Referencia": "-", "Fecha": "11/09/2026"},
            {"Indice": "Acompaña Poder", "Referencia": "ACOMPAÑA PODER", "Fecha": "11/09/2026"},
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "11/09/2026"},
            {"Indice": "Hoja Publicacion", "Referencia": "11104", "Fecha": "26/08/2026"},
            {"Indice": "Orden de Publicación", "Referencia": "-", "Fecha": "19/08/2026"},
            {"Indice": "Formulario", "Referencia": "-", "Fecha": "03/08/2026"},
        ]
        dos = "[" + opo(pres=ms("2026-09-11"), agente=1287)[1:-1] + "," + opo(pres=ms("2026-09-25"), agente=2469)[1:-1] + "]"
        html = pagina(opos=dos, vistas=vista(fecha="2026-09-30", notif="2026-09-30", venc=None),
                      gestion=GESTION_PARTICULAR)
        r = clasificar_estado_oposicion(parsear_expediente(html), arch, "2026-08-26", HOY)
        self.assertEqual(r["estado"], "notificada_en_plazo")
        self.assertTrue(r["sirve"])
        self.assertFalse(r["representacion_confirmada"])
        self.assertEqual(r["notificacion"], "2026-09-30")

    def test_poder_anterior_a_la_oposicion_no_descarta(self):
        arch = [
            {"Indice": "Escrito", "Referencia": "Acompaña Poder", "Fecha": "01/09/2026"},
            {"Indice": "Recibo de Ingreso", "Referencia": "Opo. de Marcas", "Fecha": "20/09/2026"},
        ]
        self.assertEqual(self.r(arch)["estado"], "sin_notificar")

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
