"""
Pruebas de calendario_core.py (sin red y sin base). Correr con:
    cd scripts && python3 -m unittest test_calendario -v

Google se simula reemplazando calendario_core._pedir; la base, con un cursor
que solo anota lo que se le pide (el SQL real se prueba aparte contra Postgres).
"""

import datetime as dt
import os
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

TZ = cc.TZ


class ExtraerActas(unittest.TestCase):
    def test_palabra_acta(self):
        self.assertEqual(cc.extraer_actas("Reunión por el Acta 4797001"), ["4797001"])
        self.assertEqual(cc.extraer_actas("acta: 4797001"), ["4797001"])
        self.assertEqual(cc.extraer_actas("ACTA Nº 4797001 - Pérez"), ["4797001"])
        self.assertEqual(cc.extraer_actas("Acta N° 4797001"), ["4797001"])
        self.assertEqual(cc.extraer_actas("acta nro. 4797001"), ["4797001"])
        self.assertEqual(cc.extraer_actas("Acta número 4797001"), ["4797001"])

    def test_varias_actas(self):
        self.assertEqual(cc.extraer_actas("Actas 4797001 y 4797002"), ["4797001", "4797002"])
        self.assertEqual(cc.extraer_actas("Actas: 4797001, 4797002, 4797003"), ["4797001", "4797002", "4797003"])
        self.assertEqual(cc.extraer_actas("actas 4797001/4797002 y la 4797003"), ["4797001", "4797002"])
        self.assertEqual(cc.extraer_actas("Acta 4797001\nActa 4797002"), ["4797001", "4797002"])

    def test_numeral(self):
        self.assertEqual(cc.extraer_actas("Llamar #4797001"), ["4797001"])
        self.assertEqual(cc.extraer_actas("Link https://x.com/#4797001"), [])

    def test_sin_repetir_y_orden(self):
        self.assertEqual(cc.extraer_actas("Acta 4797002 y #4797001, otra vez acta 4797002"), ["4797002", "4797001"])

    def test_no_confunde_otros_numeros(self):
        self.assertEqual(cc.extraer_actas("Llamar al 11 5555-1234"), [])
        self.assertEqual(cc.extraer_actas("CUIT 20-12345678-9"), [])
        self.assertEqual(cc.extraer_actas("Acta 12345678901"), [])  # 11 cifras: es un CUIT, no un acta
        self.assertEqual(cc.extraer_actas("Cobrar $ 4797001 de honorarios"), [])
        self.assertEqual(cc.extraer_actas(""), [])
        self.assertEqual(cc.extraer_actas(None), [])

    def test_numeros_sueltos(self):
        self.assertEqual(cc.numeros_sueltos("Reunión con Pérez 4797001 a las 15"), ["4797001"])
        self.assertEqual(cc.numeros_sueltos("tel 1155551234"), [])  # 10 cifras
        self.assertEqual(cc.numeros_sueltos("importe 1.234.567,50"), [])
        self.assertEqual(cc.numeros_sueltos("4797001 y 4797002"), ["4797001", "4797002"])

    def test_actas_del_evento_une_las_tres_fuentes(self):
        actas = cc.actas_del_evento(
            "Pérez 4797003", "Ver Acta 4797001", "4797002,basura, 4797001", existentes={"4797003", "9999999"}
        )
        self.assertEqual(sorted(actas), ["4797001", "4797002", "4797003"])

    def test_numero_suelto_que_no_existe_no_vincula(self):
        self.assertEqual(cc.actas_del_evento("Cita 5551234", "", "", existentes=set()), [])


class EventosDeGoogle(unittest.TestCase):
    def test_evento_con_hora(self):
        ev = {
            "id": "abc", "summary": "Reunión Pérez", "description": "Traer DNI<br>Acta 4797001",
            "start": {"dateTime": "2026-10-08T15:00:00-03:00"}, "end": {"dateTime": "2026-10-08T16:00:00-03:00"},
            "htmlLink": "https://calendar.google.com/x", "etag": '"e1"', "updated": "2026-10-07T18:00:00.000Z",
            "extendedProperties": {"private": {"panel": "1", "usuario": "marcas", "actas": "4797002"}},
        }
        f = cc.evento_a_fila(ev, "cal@gmail.com")
        self.assertEqual(f["titulo"], "Reunión Pérez")
        self.assertEqual(f["descripcion"], "Traer DNI\nActa 4797001")
        self.assertFalse(f["todo_el_dia"])
        self.assertEqual(f["inicio"].astimezone(TZ).strftime("%Y-%m-%d %H:%M"), "2026-10-08 15:00")
        self.assertEqual(f["origen"], "panel")
        self.assertEqual(f["creado_por"], "marcas")
        self.assertEqual(f["privadas_actas"], "4797002")
        self.assertEqual(f["actualizado_google"].tzinfo is not None, True)

    def test_evento_de_todo_el_dia(self):
        ev = {"id": "d", "summary": "Feriado", "start": {"date": "2026-10-12"}, "end": {"date": "2026-10-13"}}
        f = cc.evento_a_fila(ev, "c")
        self.assertTrue(f["todo_el_dia"])
        self.assertEqual(f["inicio"].astimezone(TZ).date().isoformat(), "2026-10-12")
        self.assertEqual(f["origen"], "google")
        j = cc.evento_a_json({**f, "id": 1, "actas": []})
        self.assertEqual((j["fecha"], j["fecha_fin"], j["hora"]), ("2026-10-12", "2026-10-12", None))

    def test_todo_el_dia_varios_dias(self):
        ev = {"id": "d", "summary": "Viaje", "start": {"date": "2026-10-12"}, "end": {"date": "2026-10-15"}}
        j = cc.evento_a_json({**cc.evento_a_fila(ev, "c"), "id": 1, "actas": []})
        self.assertEqual((j["fecha"], j["fecha_fin"]), ("2026-10-12", "2026-10-14"))

    def test_sin_titulo_y_sin_fin(self):
        ev = {"id": "x", "start": {"dateTime": "2026-10-08T10:00:00-03:00"}}
        f = cc.evento_a_fila(ev, "c")
        self.assertEqual(f["titulo"], "(sin título)")
        self.assertEqual(f["fin"] - f["inicio"], dt.timedelta(minutes=30))

    def test_utc_se_muestra_en_hora_argentina(self):
        ev = {"id": "x", "summary": "t", "start": {"dateTime": "2026-10-08T18:00:00Z"}, "end": {"dateTime": "2026-10-08T19:30:00Z"}}
        j = cc.evento_a_json({**cc.evento_a_fila(ev, "c"), "id": 1, "actas": []})
        self.assertEqual((j["fecha"], j["hora"], j["hora_fin"]), ("2026-10-08", "15:00", "16:30"))

    def test_evento_que_cruza_medianoche(self):
        ev = {"id": "x", "summary": "t", "start": {"dateTime": "2026-10-08T22:00:00-03:00"}, "end": {"dateTime": "2026-10-09T01:00:00-03:00"}}
        j = cc.evento_a_json({**cc.evento_a_fila(ev, "c"), "id": 1, "actas": []})
        self.assertEqual((j["fecha"], j["fecha_fin"]), ("2026-10-08", "2026-10-09"))

    def test_termina_justo_a_medianoche_no_ocupa_el_dia_siguiente(self):
        ev = {"id": "x", "summary": "t", "start": {"dateTime": "2026-10-08T22:00:00-03:00"}, "end": {"dateTime": "2026-10-09T00:00:00-03:00"}}
        j = cc.evento_a_json({**cc.evento_a_fila(ev, "c"), "id": 1, "actas": []})
        self.assertEqual((j["fecha"], j["fecha_fin"]), ("2026-10-08", "2026-10-08"))

    def test_texto_plano(self):
        self.assertEqual(cc.texto_plano("Hola&nbsp;<b>mundo</b><br/>Acta&nbsp;4797001 &amp; más"), "Hola\xa0mundo\nActa\xa04797001 & más")
        self.assertEqual(cc.texto_plano(None), "")


class ArmadoDeEventos(unittest.TestCase):
    def test_con_hora_y_duracion(self):
        s, e, ini = cc._inicio_fin_google({"fecha": "2026-10-08", "hora": "15:30", "duracion_min": 45})
        self.assertEqual(s["dateTime"], "2026-10-08T15:30:00-03:00")
        self.assertEqual(e["dateTime"], "2026-10-08T16:15:00-03:00")
        self.assertEqual(s["timeZone"], cc.TZ_NOMBRE)

    def test_sin_hora_es_todo_el_dia(self):
        s, e, _ = cc._inicio_fin_google({"fecha": "2026-10-08"})
        self.assertEqual((s, e), ({"date": "2026-10-08"}, {"date": "2026-10-09"}))

    def test_varios_dias(self):
        s, e, _ = cc._inicio_fin_google({"fecha": "2026-10-08", "todo_el_dia": True, "fecha_fin": "2026-10-10"})
        self.assertEqual((s, e), ({"date": "2026-10-08"}, {"date": "2026-10-11"}))

    def test_validaciones(self):
        for datos in ({}, {"fecha": "08/10/2026"}, {"fecha": "2026-10-08", "hora": "25:99"},
                      {"fecha": "2026-10-08", "hora": "10:00", "duracion_min": 1},
                      {"fecha": "2026-10-08", "todo_el_dia": True, "fecha_fin": "2026-10-01"}):
            with self.assertRaises(cc.CalendarioError, msg=str(datos)):
                cc._inicio_fin_google(datos)

    def test_cuerpo_agrega_la_linea_de_acta_si_falta(self):
        c = cc._cuerpo_evento({"titulo": "  Reunión   Pérez ", "fecha": "2026-10-08", "hora": "10:00",
                               "descripcion": "Traer DNI", "actas": ["4797001", "4797002"]}, "marcas")
        self.assertEqual(c["summary"], "Reunión Pérez")
        self.assertEqual(c["description"], "Traer DNI\n\nActas 4797001, 4797002")
        self.assertEqual(c["extendedProperties"]["private"], {"panel": "1", "usuario": "marcas", "actas": "4797001,4797002"})

    def test_cuerpo_no_repite_un_acta_que_ya_esta_en_la_nota(self):
        c = cc._cuerpo_evento({"titulo": "t", "fecha": "2026-10-08", "descripcion": "Ver Acta 4797001", "actas": ["4797001"]}, "u")
        self.assertEqual(c["description"], "Ver Acta 4797001")

    def test_cuerpo_validaciones(self):
        with self.assertRaises(cc.CalendarioError):
            cc._cuerpo_evento({"titulo": "  ", "fecha": "2026-10-08"}, "u")
        with self.assertRaises(cc.CalendarioError):
            cc._cuerpo_evento({"titulo": "t", "fecha": "2026-10-08", "actas": ["12ab"]}, "u")


# ── Sincronización con Google y base simuladas ─────────────────────────

class CursorFalso:
    """Anota los pedidos; responde a las pocas lecturas que hace la sincronización."""

    def __init__(self, base):
        self.base = base
        self.lotes = base.lotes

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.base.sqls.append((s, params))
        self._resp = []
        if s.startswith("SELECT pg_try_advisory_lock"):
            self._resp = [{"ok": self.base.candado_libre}]
        elif s.startswith("SELECT * FROM calendario_estado"):
            self._resp = [dict(self.base.estado)]
        elif "FROM marcas WHERE acta = ANY" in s:
            self._resp = [(n,) for n in self.base.actas_reales if n in params["n"]]
        elif s.startswith("UPDATE calendario_estado SET calendar_id"):
            cal, token, cambios, completa = params
            self.base.estado.update(calendar_id=cal, sync_token=token, ultimo_cambios=cambios)
            if completa:
                self.base.estado["ultima_completa_en"] = dt.datetime.now(dt.timezone.utc)

    def fetchone(self):
        return self._resp[0] if self._resp else None

    def fetchall(self):
        return self._resp


class ConexionFalsa:
    def __init__(self, base):
        self.base = base

    def cursor(self, **kw):
        return CursorFalso(self.base)

    def commit(self):
        self.base.commits += 1

    def rollback(self):
        self.base.rollbacks += 1


class BaseFalsa:
    def __init__(self):
        self.lotes, self.sqls = [], []
        self.commits = self.rollbacks = 0
        self.candado_libre = True
        self.actas_reales = set()
        self.estado = {"calendar_id": None, "sync_token": None, "ultima_completa_en": None}

    def conexion(self):
        base = self

        class Ctx:
            def __enter__(s):
                return ConexionFalsa(base)

            def __exit__(s, *a):
                return False

        return Ctx()

    def borrados(self):
        return [p for s, p in self.sqls if s.startswith("DELETE FROM calendario_eventos")]


def ev(i, titulo="t", desc=""):
    return {"id": i, "summary": titulo, "description": desc,
            "start": {"dateTime": "2026-10-08T10:00:00-03:00"}, "end": {"dateTime": "2026-10-08T11:00:00-03:00"}}


class Sincronizacion(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = '{"client_email": "sa@x.iam", "private_key": "k"}'
        os.environ["GOOGLE_CALENDAR_ID"] = "estudio@gmail.com"
        self.base = BaseFalsa()
        self.pedidos = []
        self.respuestas = []
        self._pedir = cc._pedir

        def falso(metodo, ruta, **kw):
            self.pedidos.append((metodo, ruta, dict(kw.get("params") or {})))
            r = self.respuestas.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        cc._pedir = falso

    def tearDown(self):
        cc._pedir = self._pedir
        os.environ.clear()
        os.environ.update(self.env)

    def test_primera_vez_es_completa_y_guarda_el_token(self):
        self.respuestas = [{"items": [ev("a"), ev("b")], "nextSyncToken": "T1"}]
        r = cc.sincronizar(self.base.conexion)
        self.assertEqual(r, {"omitida": False, "cambios": 2, "completa": True})
        p = self.pedidos[0][2]
        self.assertNotIn("syncToken", p)
        self.assertIn("timeMin", p)
        self.assertIn("timeMax", p)
        self.assertEqual(p["singleEvents"], "true")
        self.assertEqual(self.base.estado["sync_token"], "T1")
        self.assertEqual([f["google_id"] for f in self.base.lotes[0]], ["a", "b"])
        self.assertEqual(self.pedidos[0][1], "/calendars/estudio%40gmail.com/events")

    def test_las_siguientes_son_incrementales(self):
        self.base.estado.update(calendar_id="estudio@gmail.com", sync_token="T1",
                                ultima_completa_en=dt.datetime.now(dt.timezone.utc))
        self.respuestas = [{"items": [ev("c")], "nextSyncToken": "T2"}]
        r = cc.sincronizar(self.base.conexion)
        self.assertEqual(r["completa"], False)
        p = self.pedidos[0][2]
        self.assertEqual(p["syncToken"], "T1")
        self.assertNotIn("timeMin", p)  # Google no permite timeMin junto con syncToken
        self.assertEqual(self.base.estado["sync_token"], "T2")
        self.assertEqual(self.base.borrados(), [])  # una incremental nunca barre lo que no vio

    def test_los_cancelados_se_borran(self):
        self.base.estado.update(calendar_id="estudio@gmail.com", sync_token="T1",
                                ultima_completa_en=dt.datetime.now(dt.timezone.utc))
        self.respuestas = [{"items": [{"id": "z", "status": "cancelled"}, ev("c")], "nextSyncToken": "T2"}]
        r = cc.sincronizar(self.base.conexion)
        self.assertEqual(r["cambios"], 2)
        self.assertIn(("estudio@gmail.com", ["z"]), self.base.borrados())
        self.assertEqual([f["google_id"] for f in self.base.lotes[0]], ["c"])

    def test_paginado(self):
        self.respuestas = [
            {"items": [ev("a")], "nextPageToken": "P2"},
            {"items": [ev("b")], "nextSyncToken": "T9"},
        ]
        r = cc.sincronizar(self.base.conexion)
        self.assertEqual(r["cambios"], 2)
        self.assertEqual(self.pedidos[1][2]["pageToken"], "P2")
        self.assertEqual(self.base.estado["sync_token"], "T9")

    def test_completa_borra_lo_que_ya_no_esta_en_google(self):
        self.respuestas = [{"items": [ev("a"), ev("b")], "nextSyncToken": "T1"}]
        cc.sincronizar(self.base.conexion)
        self.assertIn(("estudio@gmail.com", ["a", "b"]), self.base.borrados())

    def test_completa_sin_eventos_vacia_el_calendario(self):
        self.respuestas = [{"items": [], "nextSyncToken": "T1"}]
        cc.sincronizar(self.base.conexion)
        self.assertIn(("estudio@gmail.com",), self.base.borrados())

    def test_token_vencido_rehace_la_completa(self):
        self.base.estado.update(calendar_id="estudio@gmail.com", sync_token="VIEJO",
                                ultima_completa_en=dt.datetime.now(dt.timezone.utc))
        self.respuestas = [cc.GoogleError(410, "gone"), {"items": [ev("a")], "nextSyncToken": "NUEVO"}]
        r = cc.sincronizar(self.base.conexion)
        self.assertTrue(r["completa"])
        self.assertEqual(self.pedidos[0][2]["syncToken"], "VIEJO")
        self.assertNotIn("syncToken", self.pedidos[1][2])
        self.assertEqual(self.base.estado["sync_token"], "NUEVO")

    def test_cambio_de_calendario_empieza_de_cero(self):
        self.base.estado.update(calendar_id="otro@gmail.com", sync_token="T1",
                                ultima_completa_en=dt.datetime.now(dt.timezone.utc))
        self.respuestas = [{"items": [], "nextSyncToken": "T2"}]
        self.assertTrue(cc.sincronizar(self.base.conexion)["completa"])

    def test_completa_forzada_y_semanal(self):
        vieja = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=cc.RESINCRONIZAR_CADA_DIAS + 1)
        self.base.estado.update(calendar_id="estudio@gmail.com", sync_token="T1", ultima_completa_en=vieja)
        self.respuestas = [{"items": [], "nextSyncToken": "T2"}]
        self.assertTrue(cc.sincronizar(self.base.conexion)["completa"])
        self.base.estado.update(sync_token="T2", ultima_completa_en=dt.datetime.now(dt.timezone.utc))
        self.respuestas = [{"items": [], "nextSyncToken": "T3"}]
        self.assertTrue(cc.sincronizar(self.base.conexion, completa=True)["completa"])

    def test_un_error_de_google_queda_anotado_y_se_propaga(self):
        self.respuestas = [cc.GoogleError(404, "no encuentro el calendario")]
        with self.assertRaises(cc.GoogleError):
            cc.sincronizar(self.base.conexion)
        anotado = [p for s, p in self.base.sqls if s.startswith("UPDATE calendario_estado SET ultimo_error")]
        self.assertEqual(anotado, [("no encuentro el calendario",)])
        self.assertIsNone(self.base.estado["sync_token"])

    def test_otra_corrida_en_marcha_se_omite(self):
        self.base.candado_libre = False
        self.assertEqual(cc.sincronizar(self.base.conexion), {"omitida": True})
        self.assertEqual(self.pedidos, [])

    def test_sin_configurar(self):
        os.environ.pop("GOOGLE_CALENDAR_ID")
        with self.assertRaises(cc.CalendarioError):
            cc.sincronizar(self.base.conexion)

    def test_numero_suelto_se_vincula_solo_si_es_una_acta_real(self):
        self.base.actas_reales = {"4797001"}
        self.respuestas = [{"items": [ev("a", "Pérez 4797001"), ev("b", "Cita 5551234"), ev("c", "x", "Acta 4000000")],
                            "nextSyncToken": "T1"}]
        cc.sincronizar(self.base.conexion)
        por_id = {f["google_id"]: f["actas"] for f in self.base.lotes[0]}
        self.assertEqual(por_id, {"a": ["4797001"], "b": [], "c": ["4000000"]})


class Configuracion(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def test_json_con_saltos_de_linea_sueltos_en_la_clave(self):
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = '{"client_email": "sa@x.iam", "private_key": "-----BEGIN\nabc\n-----END"}'
        self.assertEqual(cc._info_cuenta()["client_email"], "sa@x.iam")

    def test_json_invalido_y_json_que_no_es_cuenta_de_servicio(self):
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = "{no es json"
        with self.assertRaises(cc.CalendarioError):
            cc._info_cuenta()
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = '{"foo": 1}'
        with self.assertRaises(cc.CalendarioError):
            cc._info_cuenta()

    def test_configurado(self):
        os.environ.pop("GOOGLE_SERVICE_ACCOUNT_JSON", None)
        os.environ.pop("GOOGLE_CALENDAR_ID", None)
        self.assertFalse(cc.configurado())
        self.assertIsNone(cc.cuenta_servicio())
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = '{"client_email": "sa@x.iam", "private_key": "k"}'
        self.assertFalse(cc.configurado())
        os.environ["GOOGLE_CALENDAR_ID"] = " estudio@gmail.com "
        self.assertTrue(cc.configurado())
        self.assertEqual(cc.calendar_id(), "estudio@gmail.com")
        self.assertEqual(cc.cuenta_servicio(), "sa@x.iam")


# ── Modalidad (Meet / llamada / presencial) y avisos por mail ──────────────

def fila_evento(**kw):
    base = {"id": 7, "titulo": "Reunión con Pérez", "descripcion": "", "lugar": None, "todo_el_dia": False,
            "inicio": dt.datetime(2026, 10, 8, 16, 0, tzinfo=TZ), "fin": dt.datetime(2026, 10, 8, 17, 0, tzinfo=TZ),
            "actas": [], "origen": "panel", "creado_por": "marcos", "link": "https://g/e", "modalidad": "meet",
            "meet_url": "https://meet.google.com/abc-defg-hij"}
    base.update(kw)
    return base


class ModalidadYMeet(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN"):
            os.environ.pop(k, None)
        os.environ["GOOGLE_CALENDAR_ID"] = "estudio@gmail.com"
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = '{"client_email": "sa@x.iam", "private_key": "k"}'
        self.pedidos, self.respuestas = [], []
        self._pedir, self._guardar, self._sleep = cc._pedir, cc._guardar_resultado, cc.time.sleep

        def falso(metodo, ruta, **kw):
            self.pedidos.append((metodo, ruta, kw.get("json"), dict(kw.get("params") or {})))
            return self.respuestas.pop(0)

        cc._pedir = falso
        cc._guardar_resultado = lambda conexion, recurso: 7
        cc.time.sleep = lambda s: None

    def tearDown(self):
        cc._pedir, cc._guardar_resultado, cc.time.sleep = self._pedir, self._guardar, self._sleep
        os.environ.clear()
        os.environ.update(self.env)

    def conectar_oauth(self):
        os.environ.update(GOOGLE_OAUTH_CLIENT_ID="cid", GOOGLE_OAUTH_CLIENT_SECRET="sec", GOOGLE_OAUTH_REFRESH_TOKEN="tok")

    DATOS = {"titulo": "Reunión", "fecha": "2026-10-08", "hora": "16:00", "duracion_min": 60}

    def test_link_de_meet_del_evento(self):
        self.assertEqual(cc.meet_de_evento({"hangoutLink": "https://meet.google.com/x"}), "https://meet.google.com/x")
        self.assertEqual(cc.meet_de_evento({"conferenceData": {"entryPoints": [
            {"entryPointType": "phone", "uri": "tel:+1"}, {"entryPointType": "video", "uri": "https://meet.google.com/y"}]}}),
            "https://meet.google.com/y")
        self.assertIsNone(cc.meet_de_evento({"summary": "sin meet"}))

    def test_evento_de_google_modalidad(self):
        e = ev("a")
        e["hangoutLink"] = "https://meet.google.com/x"
        f = cc.evento_a_fila(e, "c")
        self.assertEqual((f["modalidad"], f["meet_url"]), ("meet", "https://meet.google.com/x"))
        e = ev("b")
        e["extendedProperties"] = {"private": {"panel": "1", "modalidad": "llamada"}}
        self.assertEqual(cc.evento_a_fila(e, "c")["modalidad"], "llamada")
        e["extendedProperties"] = {"private": {"modalidad": "meet"}}  # le sacaron el Meet desde Google
        f = cc.evento_a_fila(e, "c")
        self.assertEqual((f["modalidad"], f["meet_url"]), (None, None))
        e["extendedProperties"] = {"private": {"modalidad": "inventada"}}
        self.assertIsNone(cc.evento_a_fila(e, "c")["modalidad"])

    def test_cuerpo_con_modalidad(self):
        c = cc._cuerpo_evento({**self.DATOS, "modalidad": "llamada"}, "marcos")
        self.assertEqual(c["extendedProperties"]["private"]["modalidad"], "llamada")
        self.assertNotIn("modalidad", cc._cuerpo_evento(self.DATOS, "marcos")["extendedProperties"]["private"])
        with self.assertRaises(cc.CalendarioError):
            cc._cuerpo_evento({**self.DATOS, "modalidad": "zoom"}, "marcos")

    def test_oauth_estados(self):
        self.assertFalse(cc.puede_meet())
        self.assertFalse(cc.oauth_pendiente())
        os.environ.update(GOOGLE_OAUTH_CLIENT_ID="cid", GOOGLE_OAUTH_CLIENT_SECRET="sec")
        self.assertTrue(cc.oauth_pendiente())
        self.assertFalse(cc.puede_meet())
        os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"] = "tok"
        self.assertTrue(cc.puede_meet())
        self.assertFalse(cc.oauth_pendiente())
        # con OAuth alcanza, aunque no haya cuenta de servicio
        os.environ.pop("GOOGLE_SERVICE_ACCOUNT_JSON")
        self.assertTrue(cc.configurado())

    def test_meet_sin_cuenta_conectada_no_llama_a_google(self):
        with self.assertRaises(cc.CalendarioError) as m:
            cc.crear_evento(None, {**self.DATOS, "modalidad": "meet"}, "marcos")
        self.assertIn("Conectar con Google", str(m.exception))
        self.assertEqual(self.pedidos, [])

    def test_crear_con_meet_pide_la_sala_e_invita_al_mail_fijo_sin_avisarle(self):
        self.conectar_oauth()
        self.respuestas = [{"id": "g1", "hangoutLink": "https://meet.google.com/x"}]
        self.assertEqual(cc.crear_evento(None, {**self.DATOS, "modalidad": "meet"}, "marcos"), 7)
        metodo, ruta, cuerpo, params = self.pedidos[0]
        self.assertEqual(metodo, "POST")
        self.assertEqual(params, {"sendUpdates": "none", "conferenceDataVersion": 1})
        pedido = cuerpo["conferenceData"]["createRequest"]
        self.assertEqual(pedido["conferenceSolutionKey"], {"type": "hangoutsMeet"})
        self.assertTrue(pedido["requestId"])
        self.assertEqual(cuerpo["attendees"], [{"email": "marcaskom@gmail.com"}])
        self.assertEqual(params["sendUpdates"], "none")   # se invita sin mandar mail

    def test_llamada_no_invita_a_nadie(self):
        self.respuestas = [{"id": "g1"}]
        cc.crear_evento(None, {**self.DATOS, "modalidad": "llamada"}, "marcos")
        self.assertNotIn("attendees", self.pedidos[0][2])

    def test_si_el_link_tarda_se_vuelve_a_pedir(self):
        self.conectar_oauth()
        self.respuestas = [{"id": "g1", "conferenceData": {"createRequest": {"status": {"statusCode": "pending"}}}},
                           {"id": "g1"}, {"id": "g1", "hangoutLink": "https://meet.google.com/x"}]
        cc.crear_evento(None, {**self.DATOS, "modalidad": "meet"}, "marcos")
        self.assertEqual([p[0] for p in self.pedidos], ["POST", "GET", "GET"])

    def test_si_google_nunca_da_el_link_avisa(self):
        self.conectar_oauth()
        self.respuestas = [{"id": "g1"}] * 5
        with self.assertRaises(cc.CalendarioError) as m:
            cc.crear_evento(None, {**self.DATOS, "modalidad": "meet"}, "marcos")
        self.assertIn("link de Meet", str(m.exception))

    def test_llamada_no_pide_meet(self):
        self.respuestas = [{"id": "g1"}]
        cc.crear_evento(None, {**self.DATOS, "modalidad": "llamada", "lugar": "11 5555-1234"}, "marcos")
        _, _, cuerpo, params = self.pedidos[0]
        self.assertNotIn("conferenceData", cuerpo)
        self.assertEqual(params, {"sendUpdates": "none"})
        self.assertEqual(cuerpo["location"], "11 5555-1234")

    def conexion_con(self, fila):
        class Cur:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def execute(s, sql, params=None): pass
            def fetchone(s): return fila
        class Conn:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def cursor(s, **kw): return Cur()
        return lambda: Conn()

    def test_editar_de_horario_a_todo_el_dia_anula_lo_que_sobra(self):
        """Causa del 502: en un PATCH start/end se mezclan con lo que ya tiene el evento; con date y dateTime juntos Google rechaza."""
        self.respuestas = [{"id": "g1"}]
        cc.editar_evento(self.conexion_con(("g1", None, "evento", False)), 7,
                         {"titulo": "CUMPLE", "fecha": "2026-10-14", "fecha_fin": "2026-10-15", "todo_el_dia": True}, "marcos")
        cuerpo = self.pedidos[-1][2]
        self.assertEqual(cuerpo["start"], {"date": "2026-10-14", "dateTime": None, "timeZone": None})
        self.assertEqual(cuerpo["end"], {"date": "2026-10-16", "dateTime": None, "timeZone": None})   # el fin de Google es exclusivo
        # y al revés: de todo el día a un horario
        self.respuestas = [{"id": "g1"}]
        cc.editar_evento(self.conexion_con(("g1", None, "evento", False)), 7, self.DATOS, "marcos")
        cuerpo = self.pedidos[-1][2]
        self.assertIsNone(cuerpo["start"]["date"])
        self.assertEqual(cuerpo["start"]["dateTime"], "2026-10-08T16:00:00-03:00")
        self.assertEqual(cuerpo["end"]["dateTime"], "2026-10-08T17:00:00-03:00")
        self.assertIsNone(cuerpo["end"]["date"])

    def test_el_mail_de_la_persona_se_guarda_en_el_evento(self):
        c = cc._cuerpo_evento({**self.DATOS, "email_aviso": " a@b.com,  c@d.com "}, "marcos")
        self.assertEqual(c["extendedProperties"]["private"]["email_aviso"], "a@b.com, c@d.com")
        self.assertNotIn("email_aviso", cc._cuerpo_evento(self.DATOS, "marcos")["extendedProperties"]["private"])
        e = ev("a")
        e["extendedProperties"] = {"private": {"panel": "1", "email_aviso": " a@b.com "}}
        self.assertEqual(cc.evento_a_fila(e, "c")["email_aviso"], "a@b.com")
        self.assertIsNone(cc.evento_a_fila(ev("b"), "c")["email_aviso"])
        # al editar sin mail, se borra el que había
        self.respuestas = [{"id": "g1"}]
        cc.editar_evento(self.conexion_con(("g1", None, "evento", False)), 7, self.DATOS, "marcos")
        self.assertIsNone(self.pedidos[-1][2]["extendedProperties"]["private"]["email_aviso"])
        self.respuestas = [{"id": "g1"}]
        cc.editar_evento(self.conexion_con(("g1", None, "evento", False)), 7, {**self.DATOS, "email_aviso": "a@b.com"}, "marcos")
        self.assertEqual(self.pedidos[-1][2]["extendedProperties"]["private"]["email_aviso"], "a@b.com")

    def test_editar_agrega_saca_o_conserva_el_meet(self):
        self.conectar_oauth()
        # tenía llamada y pasa a Meet: se pide la sala
        self.respuestas = [{"id": "g1", "hangoutLink": "https://meet.google.com/x"}]
        cc.editar_evento(self.conexion_con(("g1", None, "evento", False)), 7, {**self.DATOS, "modalidad": "meet"}, "marcos")
        _, _, cuerpo, params = self.pedidos[-1]
        self.assertIn("createRequest", cuerpo["conferenceData"])
        self.assertEqual(cuerpo["attendees"], [{"email": "marcaskom@gmail.com"}])
        self.assertEqual(params["conferenceDataVersion"], 1)
        # ya tenía Meet y sigue siendo Meet: no se toca la sala
        self.respuestas = [{"id": "g1"}, {"id": "g1", "hangoutLink": "https://meet.google.com/x"}]
        cc.editar_evento(self.conexion_con(("g1", "https://meet.google.com/x", "evento", False)), 7, {**self.DATOS, "modalidad": "meet"}, "marcos")
        _, _, cuerpo, params = self.pedidos[-1]
        self.assertNotIn("conferenceData", cuerpo)
        self.assertNotIn("conferenceDataVersion", params)
        self.assertEqual(cuerpo["attendees"], [{"email": "marcaskom@gmail.com"}])
        # si ya había invitados, se conservan y no se duplica el fijo
        self.respuestas = [{"id": "g1", "attendees": [{"email": "x@y.com"}, {"email": "MarcasKom@gmail.com"}]},
                           {"id": "g1", "hangoutLink": "https://meet.google.com/x"}]
        cc.editar_evento(self.conexion_con(("g1", "https://meet.google.com/x", "evento", False)), 7, {**self.DATOS, "modalidad": "meet"}, "marcos")
        self.assertEqual([a["email"] for a in self.pedidos[-1][2]["attendees"]], ["x@y.com", "MarcasKom@gmail.com"])
        # tenía Meet y pasa a llamada: se saca la videollamada y la marca anterior
        self.respuestas = [{"id": "g1"}]
        cc.editar_evento(self.conexion_con(("g1", "https://meet.google.com/x", "evento", False)), 7, {**self.DATOS, "modalidad": "llamada"}, "marcos")
        _, _, cuerpo, params = self.pedidos[-1]
        self.assertIn("conferenceData", cuerpo)
        self.assertIsNone(cuerpo["conferenceData"])
        self.assertEqual(params["conferenceDataVersion"], 1)
        # sin modalidad: se borra la marca vieja
        self.respuestas = [{"id": "g1"}]
        cc.editar_evento(self.conexion_con(("g1", None, "evento", False)), 7, self.DATOS, "marcos")
        self.assertIn("modalidad", self.pedidos[-1][2]["extendedProperties"]["private"])
        self.assertIsNone(self.pedidos[-1][2]["extendedProperties"]["private"]["modalidad"])

    def test_evento_a_json_trae_modalidad_y_meet(self):
        j = cc.evento_a_json(fila_evento())
        self.assertEqual((j["modalidad"], j["meet_url"]), ("meet", "https://meet.google.com/abc-defg-hij"))


class TextosDelAviso(unittest.TestCase):
    def test_cuando(self):
        j = cc.evento_a_json(fila_evento())
        self.assertEqual(cc.cuando_texto(j), "jueves 8 de octubre de 2026, de 16:00 a 17:00 hs (hora de Argentina)")
        todo = cc.evento_a_json(fila_evento(todo_el_dia=True, inicio=dt.datetime(2026, 10, 8, tzinfo=TZ), fin=dt.datetime(2026, 10, 9, tzinfo=TZ)))
        self.assertEqual(cc.cuando_texto(todo), "jueves 8 de octubre de 2026 (todo el día)")

    def test_como(self):
        self.assertEqual(cc.como_texto({"modalidad": "meet"}), "Videollamada por Google Meet")
        self.assertEqual(cc.como_texto({"modalidad": "llamada", "lugar": "11 5555-1234"}), "Te llamamos por teléfono al 11 5555-1234")
        self.assertEqual(cc.como_texto({"modalidad": "llamada", "lugar": ""}), "Te llamamos por teléfono")
        self.assertEqual(cc.como_texto({"modalidad": "presencial", "lugar": "el estudio"}), "En persona: el estudio")
        self.assertEqual(cc.como_texto({"modalidad": None, "lugar": ""}), "A coordinar")

    def test_link_para_agregar_al_calendario(self):
        j = cc.evento_a_json(fila_evento())
        url = cc.link_agregar_a_calendario(j)
        self.assertTrue(url.startswith("https://calendar.google.com/calendar/render?action=TEMPLATE"))
        self.assertIn("dates=20261008T160000/20261008T170000", url)
        self.assertIn("text=Reuni%C3%B3n%20con%20P%C3%A9rez", url)
        self.assertIn("meet.google.com", url)

    def test_aviso_a_la_persona(self):
        j = cc.evento_a_json(fila_evento())
        asunto, html, texto = cc.armar_aviso(j, "agendada", False, "marcos")
        self.assertEqual(asunto, "Agendamos tu reunión: Reunión con Pérez")
        for t in (html, texto):
            self.assertIn("jueves 8 de octubre de 2026", t)
            self.assertIn("https://meet.google.com/abc-defg-hij", t)
            self.assertNotIn("{{", t)
        self.assertNotIn("marcos", texto)  # el mail a la persona no cuenta quién lo agendó
        self.assertEqual(cc.armar_aviso(j, "modificada", False, "marcos")[0], "Actualizamos tu reunión: Reunión con Pérez")

    def test_aviso_sin_meet_no_muestra_link_vacio(self):
        j = cc.evento_a_json(fila_evento(modalidad="llamada", meet_url=None, lugar="11 5555-1234"))
        _, _, texto = cc.armar_aviso(j, "agendada", False, "marcos")
        self.assertNotIn("Videollamada (link)", texto)
        self.assertIn("Te llamamos por teléfono al 11 5555-1234", texto)

    def test_aviso_al_equipo_cuenta_quien_actas_y_a_quien(self):
        j = cc.evento_a_json(fila_evento(actas=["4797001"]))
        j["vinculos"] = [{"acta": "4797001", "denominacion": "LUNA", "titular": "PEREZ JUAN", "tipo": "cliente"}]
        asunto, html, texto = cc.armar_aviso(j, "agendada", True, "marcos", "juan@x.com", "https://panel.test")
        # el asunto es el título y lo primero del cuerpo, el estado y el cuándo (así se lee en la lista de la bandeja)
        self.assertEqual(asunto, "Reunión con Pérez")
        self.assertTrue(texto.startswith("Agendada: jueves 8 oct 2026 · 4pm – 5pm (Hora estándar de Argentina)"), texto)
        self.assertIn("Agendó: marcos", texto)
        self.assertIn("4797001 (LUNA, PEREZ JUAN) – cliente", texto)
        self.assertIn("juan@x.com", texto)
        self.assertIn("https://panel.test/calendario", texto)
        _, _, sin = cc.armar_aviso(j, "agendada", True, "marcos", "", "https://panel.test")
        self.assertIn("no se mandó", sin)
        self.assertEqual(cc.armar_aviso(j, "modificada", True, "marcos")[0], "Actualizada: Reunión con Pérez")
        self.assertTrue(cc.armar_aviso(j, "modificada", True, "marcos")[2].startswith("Actualizada: jueves"))

    def test_aviso_al_equipo_de_una_llamada(self):
        j = cc.evento_a_json(fila_evento(modalidad="llamada", meet_url=None, lugar="11 5555-1234",
                                         inicio=dt.datetime(2026, 10, 9, 13, 30, tzinfo=TZ), fin=dt.datetime(2026, 10, 9, 14, 0, tzinfo=TZ),
                                         titulo="Llamada (OJOS DE AGUA)"))
        asunto, _, texto = cc.armar_aviso(j, "agendada", True, "marcos")
        self.assertEqual(asunto, "Llamada (OJOS DE AGUA)")
        self.assertTrue(texto.startswith("Agendada: viernes 9 oct 2026 · 1:30pm – 2pm (Hora estándar de Argentina)"), texto)
        self.assertIn("Cómo: Llamada al 11 5555-1234", texto)
        self.assertNotIn("Link de Meet", texto)

    def test_cuando_corto_como_google_calendar(self):
        f = lambda **k: cc.cuando_corto(cc.evento_a_json(fila_evento(**k)))
        self.assertEqual(f(inicio=dt.datetime(2026, 10, 9, 0, 0, tzinfo=TZ), fin=dt.datetime(2026, 10, 9, 0, 30, tzinfo=TZ)),
                         "viernes 9 oct 2026 · 12am – 12:30am (Hora estándar de Argentina)")
        self.assertEqual(f(inicio=dt.datetime(2026, 10, 9, 12, 0, tzinfo=TZ), fin=dt.datetime(2026, 10, 9, 13, 15, tzinfo=TZ)),
                         "viernes 9 oct 2026 · 12pm – 1:15pm (Hora estándar de Argentina)")
        self.assertEqual(f(todo_el_dia=True, inicio=dt.datetime(2026, 10, 9, tzinfo=TZ), fin=dt.datetime(2026, 10, 10, tzinfo=TZ)),
                         "viernes 9 oct 2026 · todo el día")
        self.assertEqual(f(todo_el_dia=True, inicio=dt.datetime(2026, 10, 9, tzinfo=TZ), fin=dt.datetime(2026, 10, 12, tzinfo=TZ)),
                         "viernes 9 oct 2026 – domingo 11 oct 2026")

    def test_el_titulo_no_inyecta_html(self):
        j = cc.evento_a_json(fila_evento(titulo="<script>alert(1)</script> {{marca}}"))
        _, html, _ = cc.armar_aviso(j, "agendada", False, "marcos")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("{{marca}}", html)  # un título con llaves no se interpreta como variable


class EnvioDeAvisos(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ["GOOGLE_CALENDAR_ID"] = "estudio@gmail.com"
        os.environ.pop("CALENDARIO_AVISO_EQUIPO", None)
        import mails_core as mc
        self.mc = mc
        self.orig = (getattr(mc, "preparar", None), getattr(mc, "enviar", None), getattr(mc, "esta_de_baja", None))
        self.enviados, self.bajas, self.falla = [], set(), set()
        mc.preparar = lambda clave, dsn=None: {"cuenta": "prospectos", "remitente": "Smarties <s@x.com>", "responder_a": "info@x.com"}

        def enviar(cuenta, remitente, responder_a, para, asunto, html, texto, adjuntos=None):
            if para[0] in self.falla:
                raise ValueError("Resend rechazó el envío (403)")
            self.enviados.append((cuenta, remitente, responder_a, list(para), asunto))
            return "id"

        mc.enviar = enviar
        mc.esta_de_baja = lambda cur, email: email in self.bajas
        fila = fila_evento()

        class Cur:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def execute(s, sql, params=None): pass
            def fetchone(s): return fila
            def fetchall(s): return []
        class Conn:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def cursor(s, **kw): return Cur()
        self.conexion = lambda: Conn()

    def tearDown(self):
        self.mc.preparar, self.mc.enviar, self.mc.esta_de_baja = self.orig
        os.environ.clear()
        os.environ.update(self.env)

    def test_avisa_a_la_persona_y_copia_al_equipo(self):
        r = cc.avisar(self.conexion, 7, "agendada", "marcos", "juan@x.com")
        self.assertTrue(r["persona"]["enviado"] and r["equipo"]["enviado"])
        self.assertEqual([e[3] for e in self.enviados], [["juan@x.com"], ["estudio@gmail.com"]])
        self.assertEqual(self.enviados[0][:3], ("prospectos", "Smarties <s@x.com>", "info@x.com"))
        self.assertTrue(self.enviados[0][4].startswith("Agendamos tu reunión"))
        self.assertEqual(self.enviados[1][4], "Reunión con Pérez")

    def test_la_copia_al_equipo_sale_por_la_cuenta_interna_desde_avisos(self):
        os.environ.pop("RESEND_FROM", None)
        cc.avisar(self.conexion, 7, "agendada", "marcos", "juan@x.com")
        persona, equipo = self.enviados
        self.assertEqual(persona[:2], ("prospectos", "Smarties <s@x.com>"))
        self.assertEqual(equipo[:2], ("interna", "Avisos Panel <avisos@quieroregistrarmimarca.com.ar>"))
        self.assertEqual(equipo[2], "info@x.com", "si responden, va a la casilla del estudio")

    def test_el_remitente_de_la_copia_sigue_a_resend_from(self):
        os.environ["RESEND_FROM"] = "Avisos <otro@dominio.com>"
        cc.avisar(self.conexion, 7, "agendada", "marcos", "")
        self.assertEqual(self.enviados[0][:2], ("interna", "Avisos <otro@dominio.com>"))

    def test_sin_mail_de_la_persona_va_solo_al_equipo(self):
        r = cc.avisar(self.conexion, 7, "agendada", "marcos", "")
        self.assertIsNone(r["persona"])
        self.assertEqual([e[3] for e in self.enviados], [["estudio@gmail.com"]])

    def test_mail_invalido_no_se_manda_pero_el_equipo_si(self):
        r = cc.avisar(self.conexion, 7, "agendada", "marcos", "esto no es un mail")
        self.assertFalse(r["persona"]["enviado"])
        self.assertIn("no es un mail válido", r["persona"]["error"])
        self.assertTrue(r["equipo"]["enviado"])

    def test_persona_de_baja_no_recibe(self):
        self.bajas.add("juan@x.com")
        r = cc.avisar(self.conexion, 7, "agendada", "marcos", "juan@x.com")
        self.assertFalse(r["persona"]["enviado"])
        self.assertIn("baja", r["persona"]["error"])
        self.assertEqual([e[3] for e in self.enviados], [["estudio@gmail.com"]])

    def test_si_falla_el_mail_a_la_persona_el_del_equipo_sale_igual(self):
        self.falla.add("juan@x.com")
        r = cc.avisar(self.conexion, 7, "agendada", "marcos", "juan@x.com")
        self.assertFalse(r["persona"]["enviado"])
        self.assertIn("Resend", r["persona"]["error"])
        self.assertTrue(r["equipo"]["enviado"])

    def test_copia_al_equipo_configurable(self):
        os.environ["CALENDARIO_AVISO_EQUIPO"] = "pamela@x.com"
        cc.avisar(self.conexion, 7, "agendada", "marcos", "")
        self.assertEqual(self.enviados[0][3], ["pamela@x.com"])
        os.environ["CALENDARIO_AVISO_EQUIPO"] = "no-es-mail"
        self.assertIsNone(cc.mail_equipo())


class EventosOcultos(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ.pop("CALENDARIO_OCULTAR", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def test_por_defecto_se_oculta_el_meet_fijo(self):
        filas = [{"titulo": t} for t in ("MEET PAME / TOMI", "meet pame/tomi", " Meet  Pame  /  Tomi ", "Reunión", "MEET PAME")]
        self.assertEqual([f["titulo"] for f in cc._sin_ocultos(filas)], ["Reunión", "MEET PAME"])

    def test_sin_tildes_ni_mayusculas(self):
        os.environ["CALENDARIO_OCULTAR"] = "Almuerzo de Equipo; Café"
        filas = [{"titulo": t} for t in ("ALMUERZO DE EQUIPO", "cafe", "Café con cliente")]
        self.assertEqual([f["titulo"] for f in cc._sin_ocultos(filas)], ["Café con cliente"])

    def test_vacio_no_oculta_nada(self):
        os.environ["CALENDARIO_OCULTAR"] = ""
        filas = [{"titulo": "MEET PAME / TOMI"}]
        self.assertEqual(cc._sin_ocultos(filas), filas)


class BuscadorDeMarcas(unittest.TestCase):
    class Cur:
        def __init__(self, filas):
            self.filas, self.sql, self.params = filas, None, None

        def execute(self, sql, params=None):
            self.sql, self.params = " ".join(sql.split()), params

        def fetchall(self):
            return self.filas

    def fila(self, acta, nombre, tipo="lead", email=None, titular="X"):
        return {"tipo": tipo, "acta": acta, "denominacion": nombre, "titular": titular, "clase": 25, "cliente": None, "email": email}

    def test_texto_muy_corto_no_busca(self):
        cur = self.Cur([])
        self.assertEqual(cc.buscar_marcas(cur, ""), [])
        self.assertEqual(cc.buscar_marcas(cur, " a "), [])
        self.assertIsNone(cur.sql)

    def test_cada_palabra_es_un_patron_sin_tildes_y_con_comodines_escapados(self):
        cur = self.Cur([])
        cc.buscar_marcas(cur, "Ojos  de Ágüa 50%")
        self.assertIn("%ojos%", cur.params)
        self.assertIn("%agua%", cur.params)
        self.assertIn("%50\\%%", cur.params)
        self.assertEqual(len(cur.params) % 2, 0)   # los mismos patrones para clientes y para leads
        self.assertIn("m.es_lead", cur.sql)

    def test_sin_repetidas_clientes_primero_y_los_que_empiezan_igual_antes(self):
        cur = self.Cur([self.fila("1", "AGUA OJOS"), self.fila("2", "OJOS DE AGUA"), self.fila("2", "OJOS DE AGUA", "cliente"),
                        self.fila("3", "LOS OJOS", "cliente", email=" a@b.com ")])
        r = cc.buscar_marcas(cur, "ojos")
        self.assertEqual([x["acta"] for x in r].count("2"), 1)
        # clientes antes que leads; dentro de cada grupo, primero los que empiezan con lo escrito
        self.assertEqual([(x["acta"], x["tipo"]) for x in r], [("2", "cliente"), ("3", "cliente"), ("1", "lead")])
        self.assertEqual({x["acta"]: x["email"] for x in r}["3"], "a@b.com")

    def test_tope(self):
        cur = self.Cur([self.fila(str(i), f"MARCA {i}") for i in range(40)])
        self.assertEqual(len(cc.buscar_marcas(cur, "marca", limite=12)), 12)


class AgendarConIA(unittest.TestCase):
    """armar_propuesta: lo que devuelve la IA se valida contra el texto y la base; lo que falta se pregunta."""

    HOY = dt.date(2026, 10, 7)   # miércoles
    TEXTO = "BUGGY'S CALIDAD Y SABOR, acta 4688297  18 de octubre 9am todosobret@gmail.com  GOOGLE MEET"
    CRUDO = {"negocio": "BUGGY'S CALIDAD Y SABOR", "acta": "4688297", "fecha": "2026-10-18", "hora": "09:00",
             "duracion_min": None, "modalidad": "meet", "email": "todosobret@gmail.com", "telefono": None,
             "lugar": None, "notas": None}

    def setUp(self):
        self.base = {"4688297": {"acta": "4688297", "existe": True, "denominacion": "BUGGYS", "titular": "SOBRET SA",
                                 "clave": None, "cliente_id": None, "cliente": None, "tipo": "lead", "email": "base@x.com"}}
        self.busqueda = []
        self._r, self._b = cc.resolver_actas, cc.buscar_marcas
        cc.resolver_actas = lambda cur, actas: {a: self.base.get(a, {"acta": a, "existe": False, "denominacion": None,
                                                                      "titular": None, "tipo": "desconocida", "email": None})
                                                for a in actas}
        cc.buscar_marcas = lambda cur, texto, limite=12: self.busqueda

    def tearDown(self):
        cc.resolver_actas, cc.buscar_marcas = self._r, self._b

    def armar(self, crudo=None, texto=None, omitir=()):
        return cc.armar_propuesta(None, self.CRUDO if crudo is None else crudo, texto or self.TEXTO, self.HOY, omitir)

    def test_el_ejemplo_completo_no_pregunta_nada(self):
        p = self.armar()
        c = p["campos"]
        self.assertEqual(p["faltantes"], [])
        self.assertEqual((c["fecha"], c["hora"], c["modalidad"], c["actas"]), ("2026-10-18", "09:00", "meet", ["4688297"]))
        self.assertEqual(c["email"], "todosobret@gmail.com")   # el del texto, no el de la base
        # el nombre es el que tiene la marca en el panel (no el que dijo el texto)
        self.assertEqual(c["titulo"], "Reunión virtual (BUGGYS)")
        self.assertEqual(p["nombre"], "BUGGYS")
        self.assertIsNone(c["lugar"])

    def test_el_domingo_se_avisa(self):
        self.assertTrue(any("domingo" in a for a in self.armar()["advertencias"]))

    def test_falta_la_hora_y_la_modalidad(self):
        p = self.armar({**self.CRUDO, "negocio": "Pérez", "hora": None, "modalidad": None}, "Pérez acta 4688297 el 18 de octubre a@b.com")
        self.assertEqual([f["campo"] for f in p["faltantes"]], ["hora", "modalidad"])
        self.assertFalse(any(f["omitible"] for f in p["faltantes"]))
        self.assertEqual(p["campos"]["titulo"], "Reunión (BUGGYS)")   # gana el nombre que tiene la marca en el panel

    def test_sin_fecha_pregunta_primero_la_fecha(self):
        p = self.armar({**self.CRUDO, "fecha": None})
        self.assertEqual(p["faltantes"][0]["campo"], "fecha")

    def test_fecha_pasada_se_pasa_al_anio_que_viene_si_el_texto_no_trae_anio(self):
        p = self.armar({**self.CRUDO, "fecha": "2026-03-18"})
        self.assertEqual(p["campos"]["fecha"], "2027-03-18")

    def test_fecha_pasada_con_anio_en_el_texto_se_pregunta(self):
        p = self.armar({**self.CRUDO, "fecha": "2026-03-18"}, self.TEXTO + " 2026")
        self.assertIsNone(p["campos"]["fecha"])
        self.assertIn("ya pasó", p["faltantes"][0]["pregunta"])

    def test_el_mail_sale_del_texto_y_no_de_lo_que_diga_la_ia(self):
        p = self.armar({**self.CRUDO, "email": "inventado@x.com"})
        self.assertEqual(p["campos"]["email"], "todosobret@gmail.com")

    def test_sin_mail_en_el_texto_usa_el_de_la_base_y_si_no_hay_lo_pregunta(self):
        texto = "BUGGY'S acta 4688297 18 de octubre 9am Meet"
        self.assertEqual(self.armar(texto=texto)["campos"]["email"], "base@x.com")
        self.base["4688297"]["email"] = None
        f = self.armar(texto=texto)["faltantes"]
        self.assertEqual([x["campo"] for x in f], ["email"])
        self.assertTrue(f[0]["omitible"])
        self.assertEqual(self.armar(texto=texto, omitir=["email"])["faltantes"], [])

    def test_un_negocio_que_la_ia_inventa_se_descarta(self):
        p = self.armar({**self.CRUDO, "negocio": "OTRA COSA"})
        self.assertIsNone(p["negocio"])
        self.assertEqual(p["campos"]["titulo"], "Reunión virtual (BUGGYS)")   # el de la base

    def test_acta_que_no_corresponde_al_nombre_avisa(self):
        self.base["4688297"]["denominacion"] = "LUNA"
        self.assertTrue(any("figura como «LUNA»" in a for a in self.armar()["advertencias"]))

    def test_titulo_segun_como_es_y_nombre_del_texto_si_la_marca_no_esta_en_la_base(self):
        t = "BUGGY'S CALIDAD Y SABOR acta 4999999 18 de octubre 9am a@b.com"
        c = {**self.CRUDO, "acta": "4999999"}
        self.assertEqual(self.armar(c, t)["campos"]["titulo"], "Reunión virtual (BUGGY'S CALIDAD Y SABOR)")
        self.assertEqual(self.armar({**c, "modalidad": "llamada", "telefono": None}, t)["campos"]["titulo"], "Llamada (BUGGY'S CALIDAD Y SABOR)")
        self.assertEqual(self.armar({**c, "modalidad": "presencial"}, t)["campos"]["titulo"], "Reunión (BUGGY'S CALIDAD Y SABOR)")

    def test_acta_desconocida_avisa_y_no_frena(self):
        p = self.armar(texto=self.TEXTO.replace("4688297", "4999999"), crudo={**self.CRUDO, "acta": "4999999"})
        self.assertEqual(p["faltantes"], [])
        self.assertTrue(any("no está en la base" in a for a in p["advertencias"]))

    def test_sin_acta_busca_por_nombre(self):
        texto = "BUGGY'S CALIDAD Y SABOR 18 de octubre 9am a@b.com Meet"
        h = {"acta": "4688297", "denominacion": "BUGGYS", "titular": "SOBRET SA", "cliente": None, "email": None, "tipo": "lead", "clase": 30}
        self.busqueda = [h]
        p = self.armar(texto=texto)
        self.assertEqual(p["campos"]["actas"], ["4688297"])
        self.assertTrue(any("Vinculé" in a for a in p["advertencias"]))
        self.busqueda = [h, {**h, "acta": "4688298"}]
        p = self.armar(texto=texto)
        self.assertEqual(p["campos"]["actas"], [])
        self.assertEqual(p["faltantes"][0]["campo"], "quien")
        self.assertEqual(len(p["faltantes"][0]["opciones"]), 2)
        self.busqueda = []
        p = self.armar(texto=texto)
        self.assertEqual(p["faltantes"], [])
        self.assertTrue(any("No encontré" in a for a in p["advertencias"]))

    def test_sin_acta_ni_nombre_se_pregunta_quien_y_se_puede_omitir(self):
        crudo = {**self.CRUDO, "negocio": None, "acta": None}
        texto = "mañana 9am a@b.com Meet"
        self.assertEqual([f["campo"] for f in self.armar(crudo, texto)["faltantes"]], ["quien"])
        self.assertEqual(self.armar(crudo, texto, omitir=["quien"])["faltantes"], [])

    def test_llamada_pide_telefono_y_presencial_pide_lugar(self):
        t = "Pérez acta 4688297 el 18 de octubre 9am a@b.com"
        p = self.armar({**self.CRUDO, "modalidad": "llamada"}, t)
        self.assertEqual([f["campo"] for f in p["faltantes"]], ["telefono"])
        p = self.armar({**self.CRUDO, "negocio": "Pérez", "modalidad": "llamada", "telefono": "11 5555-1234"}, t + " 11 5555-1234")
        self.assertEqual(p["faltantes"], [])
        self.assertEqual((p["campos"]["lugar"], p["campos"]["titulo"]), ("11 5555-1234", "Llamada (BUGGYS)"))
        p = self.armar({**self.CRUDO, "modalidad": "presencial"}, t)
        self.assertEqual([f["campo"] for f in p["faltantes"]], ["lugar"])

    def test_telefono_inventado_se_descarta(self):
        p = self.armar({**self.CRUDO, "modalidad": "llamada", "telefono": "11 9999-0000"})
        self.assertIsNone(p["campos"]["lugar"])
        self.assertEqual([f["campo"] for f in p["faltantes"]], ["telefono"])

    def test_modalidad_por_palabras_si_la_ia_no_la_trae(self):
        self.assertEqual(self.armar({**self.CRUDO, "modalidad": None})["campos"]["modalidad"], "meet")
        self.assertEqual(self.armar({**self.CRUDO, "modalidad": "zoom"}, "x acta 4688297 llamada")["campos"]["modalidad"], "llamada")

    def test_horas_en_distintos_formatos_y_basura(self):
        for entrada, salida in (("9", "09:00"), ("9:30", "09:30"), ("15.45", "15:45"), ("09:00", "09:00"), ("25:00", None), ("tarde", None), (None, None)):
            self.assertEqual(cc._hora_valida(entrada), salida, entrada)

    def test_duracion_fuera_de_rango_se_ignora(self):
        self.assertEqual(self.armar({**self.CRUDO, "duracion_min": 45})["campos"]["duracion_min"], 45)
        self.assertIsNone(self.armar({**self.CRUDO, "duracion_min": 9999})["campos"]["duracion_min"])
        self.assertIsNone(self.armar({**self.CRUDO, "duracion_min": "x"})["campos"]["duracion_min"])

    def test_crudo_vacio_no_rompe(self):
        p = self.armar({}, "algo de texto sin datos")
        self.assertEqual([f["campo"] for f in p["faltantes"]], ["fecha", "quien", "hora", "modalidad", "email"])
        self.assertEqual(p["campos"]["titulo"], "Reunión")


class ExtraerAgendaConIA(unittest.TestCase):
    def setUp(self):
        import ia_texto
        self.ia = ia_texto
        self._p = ia_texto._pedir
        self.mensajes = None

    def tearDown(self):
        self.ia._pedir = self._p

    def pedir(self, respuesta):
        def falso(mensajes, temperatura=0.2):
            self.mensajes = (mensajes, temperatura)
            return respuesta
        self.ia._pedir = falso

    def test_json_con_texto_alrededor_y_bloque_de_codigo(self):
        self.pedir('Acá está:\n```json\n{"fecha": "2026-10-18", "hora": "09:00"}\n```')
        self.assertEqual(self.ia.extraer_agenda("texto largo de prueba", "2026-10-07", "miércoles")["hora"], "09:00")

    def test_la_fecha_de_hoy_viaja_en_el_prompt_y_la_temperatura_es_cero(self):
        self.pedir("{}")
        self.ia.extraer_agenda("texto largo de prueba", "2026-10-07", "miércoles")
        mensajes, temp = self.mensajes
        self.assertIn("2026-10-07 (miércoles)", mensajes[0]["content"])
        self.assertEqual(mensajes[1]["content"], "texto largo de prueba")
        self.assertEqual(temp, 0)

    def test_respuesta_que_no_es_json_es_un_error_claro(self):
        self.pedir("no sé")
        with self.assertRaises(self.ia.ErrorIA):
            self.ia.extraer_agenda("texto largo de prueba", "2026-10-07", "miércoles")

    def test_texto_muy_corto(self):
        with self.assertRaises(self.ia.ErrorIA):
            self.ia.extraer_agenda("hola", "2026-10-07", "miércoles")

    def test_sin_clave_no_llama_al_proveedor(self):
        self.ia._pedir = self._p
        os.environ.pop("IA_API_KEY", None)
        with self.assertRaises(self.ia.ErrorIA):
            self.ia.extraer_agenda("texto largo de prueba", "2026-10-07", "miércoles")


class ResolverActasConAnalisis(unittest.TestCase):
    """resolver_actas marca qué actas están en `marcas` y si ya tienen análisis de marca escrito."""

    class Cur:
        def __init__(self):
            self.rows, self.sqls = [], []

        def execute(self, sql, params=None):
            self.sqls.append(sql)
            if "FROM marcas m" in sql:
                self.rows = [{"acta": "111111", "denominacion": "LUNA", "titular": "PEREZ", "clave": "20111111112", "email": "p@x.com"},
                             {"acta": "222222", "denominacion": "SOL", "titular": "GOMEZ", "clave": "20222222223", "email": None}]
            elif "FROM analisis_marca" in sql:
                self.rows = [{"acta": "111111"}]
            else:
                self.rows = []
            if params:   # como la base: solo las actas que se pidieron
                self.rows = [r for r in self.rows if r["acta"] in params[0]]

        def fetchall(self):
            return self.rows

    def test_en_marcas_y_tiene_analisis(self):
        r = cc.resolver_actas(self.Cur(), ["111111", "222222", "333333"])
        self.assertEqual((r["111111"]["en_marcas"], r["111111"]["tiene_analisis"]), (True, True))
        self.assertEqual((r["222222"]["en_marcas"], r["222222"]["tiene_analisis"]), (True, False))
        self.assertEqual((r["333333"]["en_marcas"], r["333333"]["tiene_analisis"], r["333333"]["tipo"]), (False, False, "desconocida"))
        self.assertEqual(r["111111"]["clave"], "20111111112")

    def test_sin_marcas_no_consulta_los_analisis(self):
        cur = self.Cur()
        cc.resolver_actas(cur, ["333333"])
        self.assertFalse(any("analisis_marca" in s for s in cur.sqls))


if __name__ == "__main__":
    unittest.main()
