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


if __name__ == "__main__":
    unittest.main()
