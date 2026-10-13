"""
Pruebas de los recordatorios (seguimientos), la disponibilidad y el texto de WhatsApp de calendario_core.py.
    cd scripts && python3 -m unittest test_seguimientos_disponibilidad -v
"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import test_calendario as base  # noqa: E402  (deja lista la base falsa de psycopg2)

cc = base.cc
HOY = dt.date(2026, 10, 8)   # jueves


def conexion_falsa(filas_fetchone=None, ejecutadas=None, filas_fetchall=None):
    filas = list(filas_fetchone or [])

    class Cur:
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def execute(s, sql, params=None):
            if ejecutadas is not None:
                ejecutadas.append((sql, params))
        def fetchone(s): return filas.pop(0) if filas else None
        def fetchall(s): return list(filas_fetchall or [])
    class Conn:
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def cursor(s, **kw): return Cur()
        def commit(s): pass
    return lambda: Conn()


class Interprete(unittest.TestCase):
    def interpretar(self, texto):
        return cc.interpretar_disponibilidad(texto, HOY)

    def test_proximo_martes_de_8_a_15(self):
        r = self.interpretar("próximo martes libre de 8 a 15")
        self.assertEqual(len(r["ventanas"]), 1)
        v = r["ventanas"][0]
        self.assertEqual((v["fecha"], v["desde"], v["hasta"]), ("2026-10-13", "08:00", "15:00"))

    def test_varios_dias_y_formatos(self):
        r = self.interpretar("martes de 8 a 12 y de 14 a 16:30, el 20/10 de 9 a 11")
        todas = {(v["fecha"], v["desde"], v["hasta"]) for v in r["ventanas"]}
        self.assertIn(("2026-10-13", "08:00", "12:00"), todas)
        self.assertIn(("2026-10-13", "14:00", "16:30"), todas)
        self.assertIn(("2026-10-20", "09:00", "11:00"), todas)

    def test_sin_horario_no_inventa(self):
        r = self.interpretar("martes")
        self.assertEqual(r["ventanas"], [])


class DiasSinReuniones(unittest.TestCase):
    def interpretar(self, texto):
        return cc.interpretar_disponibilidad(texto, HOY)

    def test_feriado_y_cumple_con_su_motivo(self):
        r = self.interpretar("lunes 12/10 feriado, no hay reuniones . miercoles 14 cumple de pame, sin reuniones")
        self.assertEqual(r["ventanas"], [])
        self.assertEqual(r["advertencias"], [])
        self.assertEqual([(c["fecha"], c["motivo"]) for c in r["cierres"]],
                         [("2026-10-12", "Feriado"), ("2026-10-14", "Cumple de pame")])

    def test_mezcla_horarios_y_dias_cerrados(self):
        r = self.interpretar("martes 8 a 15, miércoles sin reuniones por médico")
        self.assertEqual([v["fecha"] for v in r["ventanas"]], ["2026-10-13"])
        self.assertEqual([(c["fecha"], c["motivo"]) for c in r["cierres"]], [("2026-10-14", "Médico")])

    def test_el_cierre_puede_ir_antes_de_los_dias(self):
        r = self.interpretar("sin reuniones el lunes y el viernes 16")
        self.assertEqual([c["fecha"] for c in r["cierres"]], ["2026-10-12", "2026-10-16"])

    def test_libre_sigue_siendo_disponibilidad(self):
        r = self.interpretar("próximo martes libre de 8 a 15")
        self.assertEqual((len(r["ventanas"]), r["cierres"]), (1, []))

    def test_dia_con_numero_avisa_si_no_coincide(self):
        r = self.interpretar("martes 14 sin reuniones")
        self.assertTrue(any("cae miércoles" in a for a in r["advertencias"]))

    def test_se_guardan_y_se_repiten(self):
        ejecutadas = []
        r = cc.guardar_disponibilidad(conexion_falsa([(1,), (2,)], ejecutadas), [], "marcos", 1, hoy=HOY,
                                      cierres=[{"fecha": "2026-10-12", "motivo": "Feriado"}])
        self.assertEqual([p[0].isoformat() for _, p in ejecutadas], ["2026-10-12", "2026-10-19"])
        self.assertEqual((r["cerrados"], r["cerrados_repetidos"]), (2, 0))

    def test_cierre_en_el_pasado_falla(self):
        with self.assertRaises(cc.CalendarioError):
            cc.guardar_disponibilidad(conexion_falsa(), [], "m", 0, hoy=HOY, cierres=[{"fecha": "2026-10-01"}])


class VistaConDiaCerrado(unittest.TestCase):
    def test_un_dia_cerrado_no_ofrece_horarios_y_avisa_de_lo_agendado(self):
        class Cur:
            def execute(s, sql, params=None): s.sql = sql
            def fetchall(s):
                if "calendario_dias_cerrados" in s.sql:
                    return [{"id": 5, "fecha": dt.date(2026, 10, 12), "motivo": "Feriado"}]
                return [{"id": 1, "fecha": dt.date(2026, 10, 12), "desde": dt.time(8), "hasta": dt.time(15)},
                        {"id": 2, "fecha": dt.date(2026, 10, 13), "desde": dt.time(8), "hasta": dt.time(15)}]
        orig = cc.listar_eventos
        cc.listar_eventos = lambda cur, d0, d1: [{"id": 9, "titulo": "Reunión", "fecha": "2026-10-12", "fecha_fin": "2026-10-12",
                                                  "hora": "10:00", "hora_fin": "10:30", "todo_el_dia": False, "tipo": "evento"}]
        try:
            dias = cc.disponibilidad_del_rango(Cur(), dt.date(2026, 10, 8), dt.date(2026, 11, 1), ahora=dt.datetime(2026, 10, 8, 9, tzinfo=cc.TZ))
        finally:
            cc.listar_eventos = orig
        lunes, martes = dias
        self.assertEqual((lunes["cerrado"]["motivo"], lunes["libres"], lunes["capacidad"], len(lunes["reuniones"])), ("Feriado", [], 0, 1))
        self.assertIn("sin reuniones (feriado)", lunes["texto"])
        self.assertIsNone(martes["cerrado"])
        self.assertEqual(martes["capacidad"], 9)


class ListarDiasCerrados(unittest.TestCase):
    def test_devuelve_fecha_y_motivo(self):
        class Cur:
            def execute(s, sql, params=None): s.params = params
            def fetchall(s): return [{"id": 5, "fecha": dt.date(2026, 10, 12), "motivo": "Feriado"}, {"id": 6, "fecha": dt.date(2026, 10, 14), "motivo": None}]
        r = cc.listar_dias_cerrados(Cur(), dt.date(2026, 10, 1), dt.date(2026, 11, 1))
        self.assertEqual(r, [{"id": 5, "fecha": "2026-10-12", "motivo": "Feriado"}, {"id": 6, "fecha": "2026-10-14", "motivo": ""}])


class Calculo(unittest.TestCase):
    def test_ventana_vacia_8_a_15_entran_9(self):
        r = cc.calcular_dia([(480, 900)], [], 30, 15)
        self.assertEqual(r["capacidad"], 9)
        self.assertEqual(len(r["libres"]), 9)
        self.assertEqual(r["libres"][:3], [480, 525, 570])

    def test_una_reunion_deja_margen_de_cada_lado(self):
        r = cc.calcular_dia([(480, 900)], [(600, 630)], 30, 15)
        libres = [cc._hora_corta(m) for m in r["libres"]]
        self.assertEqual(libres, ["8:00", "8:45", "10:45", "11:30", "12:15", "13:00", "13:45", "14:30"])
        self.assertEqual(r["agendadas"], 1)

    def test_hoy_no_ofrece_lo_que_ya_paso(self):
        r = cc.calcular_dia([(480, 900)], [], 30, 15, no_antes=720)
        self.assertTrue(all(m >= 720 for m in r["libres"]))

    def test_ventanas_que_se_tocan_se_unen(self):
        self.assertEqual(cc.unir_ventanas([(480, 600), (600, 720), (800, 900)]), [(480, 720), (800, 900)])


class Recordatorios(unittest.TestCase):
    def test_cuerpo_para_google(self):
        c = cc._cuerpo_seguimiento({"titulo": "Llamar a Pérez", "fecha": "2026-10-12", "actas": ["1234567"]}, "marcos")
        self.assertEqual(c["summary"], "🔔 Llamar a Pérez")
        self.assertEqual(c["colorId"], cc.COLOR_SEGUIMIENTO)
        self.assertEqual(c["start"].get("date"), "2026-10-12")
        self.assertEqual(c["extendedProperties"]["private"]["tipo"], "seguimiento")
        self.assertNotIn("hecho", c["extendedProperties"]["private"])
        h = cc._cuerpo_seguimiento({"titulo": "Llamar", "fecha": "2026-10-12"}, "marcos", hecho=True)
        self.assertEqual(h["colorId"], cc.COLOR_SEGUIMIENTO_HECHO)
        self.assertEqual(h["extendedProperties"]["private"]["hecho"], "1")

    def test_sin_titulo_falla(self):
        with self.assertRaises(cc.CalendarioError):
            cc._cuerpo_seguimiento({"titulo": "🔔  ", "fecha": "2026-10-12"}, "m")

    def test_tipo_seguimiento_va_por_cuerpo_evento(self):
        c = cc._cuerpo_evento({"titulo": "x", "fecha": "2026-10-12", "tipo": "seguimiento", "modalidad": "meet"}, "m")
        self.assertEqual(c["summary"], "🔔 x")
        self.assertNotIn("modalidad", c["extendedProperties"]["private"])

    def test_google_a_fila_saca_la_campanita_y_lee_el_estado(self):
        e = base.ev("s1", "🔔 Llamar a Pérez")
        e["start"], e["end"] = {"date": "2026-10-12"}, {"date": "2026-10-13"}
        e["extendedProperties"] = {"private": {"panel": "1", "tipo": "seguimiento", "hecho": "1"}}
        f = cc.evento_a_fila(e, "c")
        self.assertEqual((f["tipo"], f["hecho"], f["titulo"]), ("seguimiento", True, "Llamar a Pérez"))
        self.assertIsNone(f["modalidad"])
        normal = cc.evento_a_fila(base.ev("a"), "c")
        self.assertEqual((normal["tipo"], normal["hecho"]), ("evento", False))


class MarcarHecho(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ["GOOGLE_CALENDAR_ID"] = "estudio@gmail.com"
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = '{"client_email": "sa@x.iam", "private_key": "k"}'
        self.pedidos = []
        self._pedir, self._guardar = cc._pedir, cc._guardar_resultado
        cc._pedir = lambda metodo, ruta, **kw: self.pedidos.append((metodo, ruta, kw.get("json"))) or {"id": "g1"}
        cc._guardar_resultado = lambda conexion, recurso: 7

    def tearDown(self):
        cc._pedir, cc._guardar_resultado = self._pedir, self._guardar
        os.environ.clear(); os.environ.update(self.env)

    def test_marca_y_reabre(self):
        cc.marcar_hecho(conexion_falsa([("g1", "seguimiento")]), 7, True)
        cuerpo = self.pedidos[-1][2]
        self.assertEqual(cuerpo["colorId"], cc.COLOR_SEGUIMIENTO_HECHO)
        self.assertEqual(cuerpo["extendedProperties"]["private"]["hecho"], "1")
        cc.marcar_hecho(conexion_falsa([("g1", "seguimiento")]), 7, False)
        cuerpo = self.pedidos[-1][2]
        self.assertEqual(cuerpo["colorId"], cc.COLOR_SEGUIMIENTO)
        self.assertIsNone(cuerpo["extendedProperties"]["private"]["hecho"])

    def test_un_evento_comun_no_se_marca(self):
        with self.assertRaises(cc.CalendarioError):
            cc.marcar_hecho(conexion_falsa([("g1", "evento")]), 7)
        with self.assertRaises(cc.CalendarioError):
            cc.marcar_hecho(conexion_falsa([]), 7)
        self.assertEqual(self.pedidos, [])


def _ts(y, m, d):
    """Un recordatorio de todo el día: empieza a las 00:00 de Argentina."""
    return dt.datetime(y, m, d, tzinfo=cc.TZ)


class DiasHabiles(unittest.TestCase):
    MAR, VIE, SAB, DOM, LUN = dt.date(2026, 10, 13), dt.date(2026, 10, 16), dt.date(2026, 10, 17), dt.date(2026, 10, 18), dt.date(2026, 10, 19)

    def test_proximo_dia_habil(self):
        self.assertEqual(cc.proximo_dia_habil(self.MAR), dt.date(2026, 10, 14))   # martes → miércoles
        self.assertEqual(cc.proximo_dia_habil(self.VIE), self.LUN)               # viernes → lunes
        self.assertEqual(cc.proximo_dia_habil(self.SAB), self.LUN)
        self.assertEqual(cc.proximo_dia_habil(self.DOM), self.LUN)               # domingo → lunes (mañana)

    def test_primer_dia_habil(self):
        self.assertEqual(cc.primer_dia_habil(self.VIE), self.VIE)
        self.assertEqual(cc.primer_dia_habil(self.SAB), self.LUN)
        self.assertEqual(cc.primer_dia_habil(self.DOM), self.LUN)

    def test_postergar_a_mano(self):
        self.assertEqual(cc.fecha_postergada(self.MAR, self.MAR), dt.date(2026, 10, 14))
        self.assertEqual(cc.fecha_postergada(self.VIE, self.VIE), self.LUN)
        # atrasado: nunca queda en el pasado, va al día hábil siguiente a hoy
        self.assertEqual(cc.fecha_postergada(dt.date(2026, 10, 12), self.MAR), dt.date(2026, 10, 14))
        # a futuro: el día hábil siguiente a su propia fecha
        self.assertEqual(cc.fecha_postergada(self.VIE, self.MAR), self.LUN)
        self.assertEqual(cc.fecha_postergada(dt.date(2026, 10, 22), self.MAR), dt.date(2026, 10, 23))

    def test_pase_automatico(self):
        # sin vencer (hoy o más adelante) no se mueve
        self.assertEqual(cc.fecha_pase_automatico(self.MAR, self.MAR), self.MAR)
        self.assertEqual(cc.fecha_pase_automatico(self.VIE, self.MAR), self.VIE)
        # martes sin hacer → el miércoles
        self.assertEqual(cc.fecha_pase_automatico(self.MAR, dt.date(2026, 10, 14)), dt.date(2026, 10, 14))
        # viernes sin hacer → el lunes, aunque hoy sea sábado o domingo
        self.assertEqual(cc.fecha_pase_automatico(self.VIE, self.SAB), self.LUN)
        self.assertEqual(cc.fecha_pase_automatico(self.VIE, self.DOM), self.LUN)
        self.assertEqual(cc.fecha_pase_automatico(self.VIE, self.LUN), self.LUN)
        # panel apagado varios días: salta al primer día hábil desde hoy (no queda atrasado)
        self.assertEqual(cc.fecha_pase_automatico(self.MAR, dt.date(2026, 10, 21)), dt.date(2026, 10, 21))
        # sábado sin hacer (se creó para un sábado) → lunes
        self.assertEqual(cc.fecha_pase_automatico(self.SAB, self.DOM), self.LUN)


class _BaseGoogleFalso(unittest.TestCase):
    """Google falso (anota los PATCH) y variables de entorno del calendario; sin tests propios."""
    HOY = dt.date(2026, 10, 13)   # martes

    def setUp(self):
        self.env = dict(os.environ)
        os.environ["GOOGLE_CALENDAR_ID"] = "estudio@gmail.com"
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = '{"client_email": "sa@x.iam", "private_key": "k"}'
        self.pedidos = []
        self.falla = None
        self._pedir, self._guardar = cc._pedir, cc._guardar_resultado

        def pedir(metodo, ruta, **kw):
            if self.falla:
                raise self.falla
            self.pedidos.append((metodo, ruta, kw.get("json")))
            return {"id": "g1"}
        cc._pedir = pedir
        cc._guardar_resultado = lambda conexion, recurso: 7

    def tearDown(self):
        cc._pedir, cc._guardar_resultado = self._pedir, self._guardar
        os.environ.clear(); os.environ.update(self.env)



class PostergarRecordatorio(_BaseGoogleFalso):
    def fila(self, y=2026, m=10, d=13, tipo="seguimiento", hecho=False, dias=1):
        return ("g1", tipo, hecho, _ts(y, m, d), _ts(y, m, d) + dt.timedelta(days=dias))

    def test_martes_pasa_al_miercoles(self):
        nueva = cc.postergar_recordatorio(conexion_falsa([self.fila()]), 7, hoy=self.HOY)
        self.assertEqual(nueva, dt.date(2026, 10, 14))
        metodo, ruta, cuerpo = self.pedidos[-1]
        self.assertEqual(metodo, "PATCH")
        self.assertTrue(ruta.endswith("/events/g1"))
        self.assertEqual(cuerpo["start"]["date"], "2026-10-14")
        self.assertEqual(cuerpo["end"]["date"], "2026-10-15")
        # no toca color ni si está hecho
        self.assertNotIn("colorId", cuerpo)
        self.assertNotIn("extendedProperties", cuerpo)
        # y anula dateTime/timeZone para que el PATCH no mezcle fecha con hora
        self.assertIsNone(cuerpo["start"]["dateTime"])

    def test_viernes_pasa_al_lunes(self):
        nueva = cc.postergar_recordatorio(conexion_falsa([self.fila(d=16)]), 7, hoy=dt.date(2026, 10, 16))
        self.assertEqual(nueva, dt.date(2026, 10, 19))
        self.assertEqual(self.pedidos[-1][2]["start"]["date"], "2026-10-19")

    def test_conserva_el_largo_en_dias(self):
        cc.postergar_recordatorio(conexion_falsa([self.fila(dias=3)]), 7, hoy=self.HOY)
        cuerpo = self.pedidos[-1][2]
        self.assertEqual((cuerpo["start"]["date"], cuerpo["end"]["date"]), ("2026-10-14", "2026-10-17"))

    def test_no_se_posterga_lo_que_no_corresponde(self):
        with self.assertRaises(cc.CalendarioError):
            cc.postergar_recordatorio(conexion_falsa([self.fila(tipo="evento")]), 7, hoy=self.HOY)
        with self.assertRaises(cc.CalendarioError):
            cc.postergar_recordatorio(conexion_falsa([self.fila(hecho=True)]), 7, hoy=self.HOY)
        with self.assertRaises(cc.CalendarioError):
            cc.postergar_recordatorio(conexion_falsa([]), 7, hoy=self.HOY)
        self.assertEqual(self.pedidos, [])


class PaseAutomatico(_BaseGoogleFalso):
    HOY = dt.date(2026, 10, 14)   # miércoles

    def fila_pase(self, google_id, d, dias=1):
        return (google_id, _ts(2026, 10, d), _ts(2026, 10, d) + dt.timedelta(days=dias))

    def test_mueve_los_atrasados_y_deja_el_resto(self):
        ejecutadas = []
        filas = [self.fila_pase("a", 13), self.fila_pase("b", 9)]   # martes y viernes anterior, ambos sin hacer
        n = cc.pasar_recordatorios_atrasados(conexion_falsa(ejecutadas=ejecutadas, filas_fetchall=filas), hoy=self.HOY)
        self.assertEqual(n, 2)
        # el SQL pide solo pendientes de tipo seguimiento con fecha anterior a hoy (00:00 de Argentina)
        sql, params = ejecutadas[0]
        self.assertIn("NOT hecho", sql)
        self.assertEqual(params[1], cc.TIPO_SEGUIMIENTO)
        self.assertEqual(params[2], dt.datetime(2026, 10, 14, tzinfo=cc.TZ))
        # los dos pasan a hoy (miércoles): el martes por «día siguiente», el viernes viejo porque no puede quedar atrasado
        self.assertEqual([p[2]["start"]["date"] for p in self.pedidos], ["2026-10-14", "2026-10-14"])

    def test_sin_atrasados_no_hace_nada(self):
        self.assertEqual(cc.pasar_recordatorios_atrasados(conexion_falsa(), hoy=self.HOY), 0)
        self.assertEqual(self.pedidos, [])

    def test_viernes_sin_hacer_el_sabado_pasa_al_lunes(self):
        n = cc.pasar_recordatorios_atrasados(conexion_falsa(filas_fetchall=[self.fila_pase("v", 16)]), hoy=dt.date(2026, 10, 17))
        self.assertEqual(n, 1)
        self.assertEqual(self.pedidos[-1][2]["start"]["date"], "2026-10-19")

    def test_borrado_en_google_se_saltea(self):
        self.falla = cc.GoogleError(404, "no existe")
        n = cc.pasar_recordatorios_atrasados(conexion_falsa(filas_fetchall=[self.fila_pase("a", 13)]), hoy=self.HOY)
        self.assertEqual(n, 0)

    def test_un_error_de_google_corta_la_tanda(self):
        self.falla = cc.GoogleError(403, "sin permiso")
        with self.assertRaises(cc.GoogleError):
            cc.pasar_recordatorios_atrasados(conexion_falsa(filas_fetchall=[self.fila_pase("a", 13)]), hoy=self.HOY)

    def test_se_puede_apagar(self):
        self.assertTrue(cc.pase_recordatorios_activo())
        for valor in ("off", "OFF", "0", "no"):
            os.environ["RECORDATORIOS_PASE_AUTOMATICO"] = valor
            self.assertFalse(cc.pase_recordatorios_activo())
        self.assertIsNone(cc.iniciar_pase_recordatorios(conexion_falsa()))


class TextoWhatsApp(unittest.TestCase):
    def ev(self, **kw):
        base_ = {"fecha": "2026-10-13", "fecha_fin": "2026-10-13", "hora": "10:00", "hora_fin": "10:30",
                 "todo_el_dia": False, "modalidad": None, "lugar": "", "meet_url": None}
        base_.update(kw)
        return base_

    def test_meet_incluye_el_link(self):
        t = cc.texto_whatsapp(self.ev(modalidad="meet", meet_url="https://meet.google.com/abc"))
        self.assertIn("martes 13 de octubre de 2026", t)
        self.assertIn("10:00 a 10:30", t)
        self.assertIn("https://meet.google.com/abc", t)

    def test_llamada_y_presencial(self):
        self.assertIn("11 5555-1234", cc.texto_whatsapp(self.ev(modalidad="llamada", lugar="11 5555-1234")))
        t = cc.texto_whatsapp(self.ev(modalidad="presencial", lugar="Av. Corrientes 1234"))
        self.assertIn("En persona: Av. Corrientes 1234", t)

    def test_saludo_segun_el_tipo_de_evento(self):
        hoy = dt.date(2026, 10, 13)
        t = cc.texto_whatsapp(self.ev(modalidad="meet", meet_url="https://meet.google.com/abc"), hoy=hoy)
        self.assertEqual(t.splitlines()[0],
                         "Hola! Cómo estás? Te paso los datos de nuestra reunión virtual por Google Meet de hoy:")
        t = cc.texto_whatsapp(self.ev(modalidad="llamada", lugar="11 5555-1234"), hoy=hoy)
        self.assertEqual(t.splitlines()[0],
                         "Hola! Cómo estás? Te paso los datos de nuestra llamada telefónica de hoy:")
        # Los datos de abajo quedan iguales.
        self.assertIn("📞 Te llamamos por teléfono al 11 5555-1234", t)
        self.assertIn("Si necesitás cambiar el horario, avisame por acá. ¡Saludos!", t)

    def test_saludo_si_no_es_hoy(self):
        ev = self.ev(modalidad="meet", meet_url="https://meet.google.com/abc")
        manana = cc.texto_whatsapp(ev, hoy=dt.date(2026, 10, 12)).splitlines()[0]
        self.assertTrue(manana.endswith("Google Meet de mañana:"))
        otro = cc.texto_whatsapp(ev, hoy=dt.date(2026, 10, 1)).splitlines()[0]
        self.assertTrue(otro.endswith("Google Meet:"))

    def test_json_trae_el_texto_solo_en_reuniones(self):
        f = base.fila_evento()
        self.assertIn("texto_whatsapp", cc.evento_a_json(f))
        r = base.fila_evento(tipo="seguimiento", hecho=False, todo_el_dia=True, modalidad=None, meet_url=None)
        self.assertFalse(cc.evento_a_json(r).get("texto_whatsapp"))


class AgendaConRecordatorios(unittest.TestCase):
    def test_bloque_de_recordatorios_en_el_mail(self):
        seg = [{"titulo": "Llamar a Pérez", "fecha": "2026-10-12", "descripcion": "ver presupuesto", "vinculos": []},
               {"titulo": "Mandar contrato", "fecha": "2026-10-13", "descripcion": "", "vinculos": []}]
        asunto, html, texto = cc.armar_agenda(dt.date(2026, 10, 13), [], "https://panel.test", seg)
        self.assertIn("2 recordatorios", html)
        self.assertIn("RECORDATORIOS", texto)
        self.assertIn("atrasado", texto)
        self.assertIn("Mandar contrato", html)

    def test_sin_recordatorios_igual_que_antes(self):
        _, html, texto = cc.armar_agenda(dt.date(2026, 10, 13), [], "")
        self.assertNotIn("RECORDATORIOS", texto)


class GuardarDisponibilidad(unittest.TestCase):
    V = {"fecha": "2026-10-13", "desde": "08:00", "hasta": "15:00"}

    def test_repetir_copia_semana_a_semana(self):
        ejecutadas = []
        r = cc.guardar_disponibilidad(conexion_falsa([(1,), (2,), (3,)], ejecutadas), [self.V], "marcos", 2, hoy=HOY)
        fechas = [p[0].isoformat() for _, p in ejecutadas]
        self.assertEqual(fechas, ["2026-10-13", "2026-10-20", "2026-10-27"])
        self.assertEqual((r["creadas"], r["repetidas"]), (3, 0))

    def test_lo_ya_cargado_no_se_duplica(self):
        r = cc.guardar_disponibilidad(conexion_falsa([]), [self.V], "marcos", 0, hoy=HOY)
        self.assertEqual((r["creadas"], r["repetidas"]), (0, 1))

    def test_validaciones(self):
        for args in (([], 0), ([self.V], 99), ([self.V] * 61, 0)):
            with self.assertRaises(cc.CalendarioError):
                cc.guardar_disponibilidad(conexion_falsa(), args[0], "m", args[1], hoy=HOY)
        with self.assertRaises(cc.CalendarioError):
            cc.guardar_disponibilidad(conexion_falsa(), [{**self.V, "desde": "15:00", "hasta": "08:00"}], "m", 0, hoy=HOY)


if __name__ == "__main__":
    unittest.main()
