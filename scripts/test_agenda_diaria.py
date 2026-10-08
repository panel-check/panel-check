"""Pruebas del mail diario AGENDA (20 hs): qué entra, cuándo NO se manda, que salga una sola vez y el formato.

Sin base ni servidor: una tabla calendario_agenda_envios en memoria y el envío de Resend simulado.
Ejecutar: cd scripts && python3 -m unittest test_agenda_diaria
"""
import datetime as dt
import os
import re
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))
try:  # el SQL no se corre acá: alcanza con que el módulo se pueda importar
    import psycopg2.extras  # noqa: F401
except ImportError:
    fake = types.ModuleType("psycopg2")
    fake.extras = types.ModuleType("psycopg2.extras")
    fake.extras.RealDictCursor = object
    fake.extras.execute_batch = lambda cur, sql, filas: cur.lotes.append(list(filas))
    sys.modules["psycopg2"] = fake
    sys.modules["psycopg2.extras"] = fake.extras

import calendario_core as cc  # noqa: E402
import mails_core as mc  # noqa: E402

MANANA = dt.date(2026, 10, 8)   # jueves


def evento(hora, fin, titulo, modalidad=None, lugar="", meet=None, todo=False, fecha=MANANA, **kw):
    return {"id": 1, "titulo": titulo, "fecha": fecha.isoformat(), "fecha_fin": fecha.isoformat(), "hora": None if todo else hora,
            "hora_fin": None if todo else fin, "todo_el_dia": todo, "modalidad": modalidad, "lugar": lugar, "meet_url": meet,
            "vinculos": kw.get("vinculos", []), "email_aviso": kw.get("email_aviso")}


class TablaEnvios:
    """calendario_agenda_envios en memoria."""

    def __init__(self):
        self.filas = {}

    def conexion(self):
        tabla = self

        class Cur:
            def __enter__(s): return s
            def __exit__(s, *a): return False

            def execute(s, sql, params=()):
                q = " ".join(sql.split())
                s._res = None
                if q.startswith("INSERT INTO calendario_agenda_envios"):
                    fecha, mins = params
                    f = tabla.filas.get(fecha)
                    ahora = dt.datetime.now(dt.timezone.utc)
                    if f is None:
                        tabla.filas[fecha] = {"intento_en": ahora, "enviado_en": None, "omitida": False, "error": None}
                        s._res = (fecha,)
                    elif f["enviado_en"] is None and not f["omitida"] and (f["intento_en"] is None or f["intento_en"] < ahora - dt.timedelta(minutes=mins)):
                        f["intento_en"] = ahora
                        s._res = (fecha,)
                elif q.startswith("UPDATE calendario_agenda_envios SET"):
                    cols = re.findall(r"(\w+) = %s", q.split(" WHERE ")[0])
                    *vals, fecha = params
                    tabla.filas[fecha].update(dict(zip(cols, vals)))
                else:
                    raise AssertionError("consulta inesperada: " + q)

            def fetchone(s): return s._res

        class Conn:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def cursor(s, **kw): return Cur()
            def commit(s): pass
            def rollback(s): pass

        return Conn()


class EnvioDeLaAgenda(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ["GOOGLE_CALENDAR_ID"] = "estudio@gmail.com"
        os.environ.pop("PANEL_URL", None)
        self.tabla = TablaEnvios()
        self.eventos, self.enviados, self.falla = [], [], False
        self.orig = (cc.eventos_de_agenda, cc.sincronizar, mc.preparar, mc.enviar, cc.seguimientos_de_agenda)
        self.seguimientos = []
        cc.seguimientos_de_agenda = lambda cur, fecha: list(self.seguimientos)
        cc.eventos_de_agenda = lambda cur, fecha: list(self.eventos)
        cc.sincronizar = lambda conexion, completa=False: {}
        mc.preparar = lambda clave, dsn=None: {"cuenta": "interna", "remitente": "Avisos Panel <avisos@quieroregistrarmimarca.com.ar>",
                                                "responder_a": None, "destinatarios": ["estudio@gmail.com"]}

        def enviar(cuenta, remitente, responder_a, para, asunto, html, texto, adjuntos=None):
            if self.falla:
                raise ValueError("Resend rechazó el envío (403)")
            self.enviados.append((cuenta, remitente, list(para), asunto, html, texto))
            return "id"

        mc.enviar = enviar
        self.conexion = self.tabla.conexion

    def tearDown(self):
        cc.eventos_de_agenda, cc.sincronizar, mc.preparar, mc.enviar, cc.seguimientos_de_agenda = self.orig
        os.environ.clear(); os.environ.update(self.env)

    def test_con_reuniones_manda_la_agenda_desde_avisos_por_la_cuenta_interna(self):
        self.eventos = [evento("09:00", "09:30", "Llamada (LUNA)", "llamada", "11 5555-1234")]
        r = cc.enviar_agenda(self.conexion, MANANA)
        self.assertEqual((r["estado"], r["cantidad"]), ("enviada", 1))
        cuenta, remitente, para, asunto, _, _ = self.enviados[0]
        self.assertEqual((cuenta, para), ("interna", ["estudio@gmail.com"]))
        self.assertIn("avisos@quieroregistrarmimarca.com.ar", remitente)
        self.assertEqual(asunto, "AGENDA jueves 8/10/2026")
        self.assertIsNotNone(self.tabla.filas[MANANA]["enviado_en"])

    def test_sin_ninguna_reunion_ni_llamada_no_se_manda(self):
        r = cc.enviar_agenda(self.conexion, MANANA)
        self.assertEqual(r["estado"], "sin_eventos")
        self.assertEqual(self.enviados, [])
        self.assertTrue(self.tabla.filas[MANANA]["omitida"])

    def test_si_a_la_noche_no_habia_nada_no_sale_despues_aunque_se_agregue_algo(self):
        cc.enviar_agenda(self.conexion, MANANA)
        self.eventos = [evento("10:00", "10:30", "Llamada", "llamada")]
        self.assertEqual(cc.enviar_agenda(self.conexion, MANANA)["estado"], "ya_enviada")
        self.assertEqual(self.enviados, [])

    def test_sale_una_sola_vez_por_dia(self):
        self.eventos = [evento("09:00", "09:30", "Llamada", "llamada")]
        cc.enviar_agenda(self.conexion, MANANA)
        for _ in range(3):
            self.assertEqual(cc.enviar_agenda(self.conexion, MANANA)["estado"], "ya_enviada")
        self.assertEqual(len(self.enviados), 1)

    def test_si_falla_se_reintenta_pero_no_enseguida(self):
        self.eventos = [evento("09:00", "09:30", "Llamada", "llamada")]
        self.falla = True
        r = cc.enviar_agenda(self.conexion, MANANA)
        self.assertEqual(r["estado"], "error")
        self.assertIn("Resend", self.tabla.filas[MANANA]["error"])
        self.assertEqual(cc.enviar_agenda(self.conexion, MANANA)["estado"], "ya_enviada", "no insiste cada minuto")
        self.tabla.filas[MANANA]["intento_en"] -= dt.timedelta(minutes=cc.AGENDA_REINTENTO_MIN + 1)
        self.falla = False
        self.assertEqual(cc.enviar_agenda(self.conexion, MANANA)["estado"], "enviada")
        self.assertEqual(len(self.enviados), 1)

    def test_manual_manda_aunque_ya_haya_salido_y_no_toca_el_registro(self):
        self.eventos = [evento("09:00", "09:30", "Llamada", "llamada")]
        cc.enviar_agenda(self.conexion, MANANA)
        antes = dict(self.tabla.filas[MANANA])
        r = cc.enviar_agenda(self.conexion, MANANA, manual=True)
        self.assertEqual(r["estado"], "enviada")
        self.assertEqual(len(self.enviados), 2)
        self.assertEqual(self.tabla.filas[MANANA], antes)

    def test_manual_sin_nada_tampoco_manda_ni_deja_registro(self):
        r = cc.enviar_agenda(self.conexion, MANANA, manual=True)
        self.assertEqual(r["estado"], "sin_eventos")
        self.assertEqual((self.enviados, self.tabla.filas), ([], {}))

    def test_sin_destinatario_avisa_en_vez_de_perderse(self):
        self.eventos = [evento("09:00", "09:30", "Llamada", "llamada")]
        mc.preparar = lambda clave, dsn=None: {"cuenta": "interna", "remitente": "a@b.com", "responder_a": None, "destinatarios": []}
        r = cc.enviar_agenda(self.conexion, MANANA)
        self.assertEqual(r["estado"], "error")
        self.assertIn("destinatario", r["error"])


class QueEntraEnLaAgenda(unittest.TestCase):
    """eventos_de_agenda filtra lo que devuelve listar_eventos: solo con horario y que empiecen ese día."""

    def test_filtra_todo_el_dia_y_los_que_empezaron_antes(self):
        orig = cc.listar_eventos
        try:
            cc.listar_eventos = lambda cur, d0, d1: [
                evento("09:00", "09:30", "Llamada", "llamada"),
                evento(None, None, "CUMPLE PAME", todo=True),
                evento("22:00", "23:59", "Empezó ayer", fecha=MANANA - dt.timedelta(days=1)),
                evento("15:00", "16:00", "Meet", "meet", meet="https://meet.google.com/x"),
            ]
            titulos = [e["titulo"] for e in cc.eventos_de_agenda(None, MANANA)]
        finally:
            cc.listar_eventos = orig
        self.assertEqual(titulos, ["Llamada", "Meet"])


class FormatoDelMail(unittest.TestCase):
    def setUp(self):
        self.eventos = [
            evento("09:00", "09:30", "Llamada (LUNA NUEVA)", "llamada", "11 5555-1234", email_aviso="maria@x.com",
                   vinculos=[{"acta": "4797123", "denominacion": "LUNA NUEVA", "titular": "María Gómez", "tipo": "lead"}]),
            evento("11:30", "12:00", "Reunión virtual (DON LUIS)", "meet", meet="https://meet.google.com/abc-defg-hij",
                   vinculos=[{"acta": "3901234", "denominacion": "DON LUIS", "titular": "Panadería", "tipo": "cliente"}]),
            evento("16:00", "16:30", "Cita en el estudio", "presencial", "Av. Corrientes 1234"),
            evento("17:00", "17:30", "Algo cargado en Google"),
        ]
        self.asunto, self.html, self.texto = cc.armar_agenda(MANANA, self.eventos, "https://panel.test")

    def test_asunto_con_la_fecha_del_dia_siguiente(self):
        self.assertEqual(self.asunto, "AGENDA jueves 8/10/2026")

    def test_cada_horario_dice_si_es_llamada_meet_o_presencial(self):
        for hora, tipo in (("09:00 – 09:30", "LLAMADA"), ("11:30 – 12:00", "MEET"), ("16:00 – 16:30", "PRESENCIAL"),
                           ("17:00 – 17:30", "SIN ESPECIFICAR")):
            self.assertRegex(self.texto, re.escape(hora) + r"\s+\S*\s*" + tipo)
            self.assertIn(hora, self.html)

    def test_trae_telefono_link_lugar_marca_y_mail(self):
        for dato in ("11 5555-1234", "https://meet.google.com/abc-defg-hij", "Av. Corrientes 1234", "LUNA NUEVA (lead, María Gómez)",
                     "DON LUIS (cliente, Panadería)", "maria@x.com"):
            self.assertIn(dato, self.texto)
            self.assertIn(dato, self.html)

    def test_estan_en_orden_y_el_resumen_cuenta_cada_tipo(self):
        self.assertTrue(self.texto.index("09:00") < self.texto.index("11:30") < self.texto.index("16:00") < self.texto.index("17:00"))
        self.assertIn("4 reuniones y llamadas", self.texto)
        self.assertIn("1 llamada", self.texto)
        self.assertIn("1 Meet", self.texto)

    def test_el_texto_del_titulo_se_escapa_en_el_html(self):
        _, h, _ = cc.armar_agenda(MANANA, [evento("09:00", "09:30", "<b>x</b> & y", "llamada")], "")
        self.assertIn("&lt;b&gt;x&lt;/b&gt; &amp; y", h)
        self.assertNotIn("<b>x</b>", h)

    def test_la_vista_previa_de_la_pestana_mails_se_arma(self):
        asunto, html = cc.ejemplo_agenda("https://panel.test")
        self.assertTrue(asunto.startswith("AGENDA "))
        self.assertIn("LLAMADA", html)
        self.assertIn("MEET", html)


class HoraDelEnvio(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.env)

    def test_por_defecto_20_hs(self):
        os.environ.pop("AGENDA_DIARIA_HORA", None)
        self.assertEqual(cc.agenda_hora(), (20, 0))

    def test_se_puede_cambiar_o_apagar(self):
        for valor, esperado in (("21:30", (21, 30)), ("9", (9, 0)), ("off", None), ("no", None), ("cualquier cosa", (20, 0))):
            os.environ["AGENDA_DIARIA_HORA"] = valor
            self.assertEqual(cc.agenda_hora(), esperado, valor)

    def test_el_mail_se_reconoce_como_agenda_en_el_filtro_de_enviados(self):
        self.assertEqual(mc.tipo_por_asunto("AGENDA jueves 8/10/2026", "interna"), "agenda")
        self.assertIn("agenda", mc.CATALOGO)
        self.assertEqual(mc.CATALOGO["agenda"]["cuenta"], "interna")


if __name__ == "__main__":
    unittest.main()
