"""
Pruebas de la cola de mails a prospectos (panel/mails_cola.py) y del cupo de Resend en
mails_core.enviar, sin red ni base (una base falsa en memoria). Correr con:
    cd scripts && python3 -m unittest test_mails_cola -v
"""

import datetime as dt
import os
import sys
import types
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

try:
    import psycopg2  # noqa: F401
    import psycopg2.extras  # noqa: F401
except ImportError:  # sin psycopg2 instalado alcanza con un reemplazo mínimo
    _pg = types.ModuleType("psycopg2")
    _ex = types.ModuleType("psycopg2.extras")
    _ex.Json = lambda x: x
    _ex.RealDictCursor = object
    _pg.extras = _ex
    _pg.connect = lambda *a, **k: None
    sys.modules["psycopg2"], sys.modules["psycopg2.extras"] = _pg, _ex
if not hasattr(sys.modules["psycopg2.extras"], "Json"):   # otra prueba pudo dejar un reemplazo más corto
    sys.modules["psycopg2.extras"].Json = lambda x: x
if not hasattr(sys.modules["psycopg2.extras"], "RealDictCursor"):
    sys.modules["psycopg2.extras"].RealDictCursor = object

import mails_core as mc  # noqa: E402
import mails_cola  # noqa: E402

UTC = dt.timezone.utc


class Respuesta:
    def __init__(self, status, cuerpo):
        self.status_code, self._cuerpo = status, cuerpo
        self.text = str(cuerpo)

    def json(self):
        return self._cuerpo


class CupoDeResend(unittest.TestCase):
    def setUp(self):
        os.environ["RESEND_API_KEY_PROSPECTOS"] = "re_test"

    def _enviar(self):
        return mc.enviar("prospectos", "a@b.com", "", ["x@y.com"], "Asunto", "<p>hola</p>", "hola")

    def test_cupo_diario_agotado_levanta_cupo_agotado(self):
        with mock.patch.object(mc.requests, "post", return_value=Respuesta(429, {"name": "daily_quota_exceeded"})):
            with self.assertRaises(mc.CupoAgotado) as c:
                self._enviar()
        self.assertEqual(c.exception.motivo, "daily_quota_exceeded")
        self.assertIsInstance(c.exception, ValueError)   # los que ya atrapan ValueError siguen andando

    def test_cupo_mensual(self):
        with mock.patch.object(mc.requests, "post", return_value=Respuesta(429, {"name": "monthly_quota_exceeded"})):
            with self.assertRaises(mc.CupoAgotado) as c:
                self._enviar()
        self.assertEqual(c.exception.motivo, "monthly_quota_exceeded")

    def test_limite_por_segundo_se_reintenta_y_no_encola(self):
        respuestas = [Respuesta(429, {"name": "rate_limit_exceeded"}), Respuesta(200, {"id": "abc"})]
        with mock.patch.object(mc.requests, "post", side_effect=respuestas), mock.patch("time.sleep"):
            self.assertEqual(self._enviar(), "abc")

    def test_otro_error_es_value_error_comun(self):
        with mock.patch.object(mc.requests, "post", return_value=Respuesta(422, {"name": "validation_error"})):
            with self.assertRaises(ValueError) as c:
                self._enviar()
        self.assertNotIsInstance(c.exception, mc.CupoAgotado)


class Reinicio(unittest.TestCase):
    def test_sale_a_las_21_02_hora_argentina(self):
        ahora = dt.datetime(2026, 10, 29, 18, 17, tzinfo=dt.timezone(dt.timedelta(hours=-3)))
        self.assertEqual(mails_cola.proximo_reinicio(ahora), dt.datetime(2026, 10, 30, 0, 2, tzinfo=UTC))

    def test_despues_de_las_21_es_el_reinicio_del_dia_siguiente(self):
        ahora = dt.datetime(2026, 10, 29, 22, 0, tzinfo=dt.timezone(dt.timedelta(hours=-3)))  # 01:00 UTC del 30
        self.assertEqual(mails_cola.proximo_reinicio(ahora), dt.datetime(2026, 10, 31, 0, 2, tzinfo=UTC))


# ── Base falsa: lo justo para lo que consulta mails_cola ─────────────────
class Base:
    def __init__(self):
        self.cola, self.envios, self.bajas, self.verif = [], {}, set(), {}
        self.ahora = dt.datetime(2026, 10, 30, 0, 5, tzinfo=UTC)


class Cursor:
    def __init__(self, base):
        self.b, self._res, self.rowcount, self.description = base, None, 0, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        b, q = self.b, " ".join(sql.split())
        self._res = None
        if q.startswith("INSERT INTO mails_cola"):
            params = [getattr(x, "adapted", x) for x in params]   # psycopg2.extras.Json real guarda el valor en .adapted
            (cuenta, rem, rta, para, asunto, html, texto, adj, clave, acta, origen, por, al, motivo, desde) = params
            fila = {"id": len(b.cola) + 1, "cuenta": cuenta, "remitente": rem, "responder_a": rta, "para": para,
                    "asunto": asunto, "html": html, "texto": texto, "adjuntos": adj, "clave_mail": clave, "acta": acta,
                    "origen": origen, "encolado_por": por, "al_enviar": al, "motivo": motivo, "intentar_desde": desde,
                    "estado": "pendiente", "intentos": 0, "ultimo_error": None, "resend_id": None}
            b.cola.append(fila)
            self._res = [{"id": fila["id"]}]
        elif q.startswith("SELECT * FROM mails_cola WHERE estado = 'pendiente'"):
            hasta, excluir = params[0], (params[1] if len(params) > 1 else [])
            pend = [f for f in b.cola if f["estado"] == "pendiente" and f["intentar_desde"] <= hasta
                    and f["cuenta"] not in excluir]
            self._res = [dict(pend[0])] if pend else []
        elif q.startswith("SELECT 1 FROM mails_bajas"):
            self._res = [(1,)] if params[0] in b.bajas else []
        elif q.startswith("UPDATE mails_cola SET intentar_desde"):
            desde, motivo, cuenta = params
            for f in b.cola:
                if f["estado"] == "pendiente" and f["cuenta"] == cuenta:
                    f["intentar_desde"], f["motivo"] = desde, motivo
        elif q.startswith("UPDATE mails_cola SET estado = 'enviado'"):
            rid, i = params
            f = b.cola[i - 1]
            f.update(estado="enviado", resend_id=rid)
        elif q.startswith("UPDATE mails_cola SET intentos"):
            intentos, err, estado, minutos, i = params
            b.cola[i - 1].update(intentos=intentos, ultimo_error=err, estado=estado,
                                 intentar_desde=b.ahora + dt.timedelta(minutes=minutos))
        elif q.startswith("UPDATE mails_cola SET estado = 'cancelado'"):
            motivo, i = params
            b.cola[i - 1].update(estado="cancelado", ultimo_error=motivo)
        elif q.startswith("UPDATE mails_envios SET resend_id"):
            b.envios[params["id"]]["resend_id"] = params["rid"]
        elif q.startswith("DELETE FROM mails_envios"):
            b.envios.pop(params[0], None)
        elif q.startswith("UPDATE verificaciones_marca SET confirmacion_enviada_en"):
            b.verif[params["id"]] = {"enviada": True, "error": None}
        elif q.startswith("UPDATE verificaciones_marca SET confirmacion_error"):
            b.verif[params["id"]] = {"enviada": False, "error": params["err"]}
        else:
            raise AssertionError(f"consulta no prevista en la base falsa: {q[:80]}")

    def fetchone(self):
        return self._res[0] if self._res else None


class Conexion:
    def __init__(self, base):
        self.b = base

    def cursor(self, cursor_factory=None):
        return Cursor(self.b)

    def commit(self):
        pass


def fabrica(base):
    @contextmanager
    def conexion():
        yield Conexion(base)
    return conexion


def encolar(base, para="x@y.com", al_enviar=None, cuenta="prospectos"):
    with mock.patch.object(mails_cola, "proximo_reinicio", return_value=base.ahora - dt.timedelta(minutes=3)):
        return mails_cola.encolar(Cursor(base), cuenta=cuenta, remitente="a@b.com", responder_a="", para=[para],
                                  asunto="Asunto", html="<p>", texto="t", adjuntos=[{"filename": "p.pdf", "content": b"%PDF"}],
                                  clave_mail="prospecto_oposicion", acta="4790000", origen="ficha", encolado_por="tom",
                                  al_enviar=al_enviar)


class Cola(unittest.TestCase):
    def setUp(self):
        self.b = Base()
        self.con = fabrica(self.b)

    def procesar(self, enviar):
        with mock.patch.object(mails_cola, "proximo_reinicio", return_value=self.b.ahora + dt.timedelta(days=1)):
            return mails_cola.procesar(self.con, enviar=enviar, pausa=0, ahora=self.b.ahora)

    def test_enviar_o_encolar_guarda_en_la_cola_si_no_hay_cupo(self):
        def sin_cupo(*a, **k):
            raise mc.CupoAgotado("prospectos", "daily_quota_exceeded")
        with mock.patch.object(mc, "enviar", side_effect=sin_cupo):
            r = mails_cola.enviar_o_encolar(self.con, cuenta="prospectos", remitente="a@b.com", responder_a="",
                                            para=["x@y.com"], asunto="A", html="<p>", texto="t")
        self.assertTrue(r["en_cola"])
        self.assertEqual(len(self.b.cola), 1)

    def test_enviar_o_encolar_no_encola_si_sale(self):
        with mock.patch.object(mc, "enviar", return_value="re_1"):
            r = mails_cola.enviar_o_encolar(self.con, cuenta="prospectos", remitente="a@b.com", responder_a="",
                                            para=["x@y.com"], asunto="A", html="<p>", texto="t")
        self.assertEqual(r, {"en_cola": False, "id": "re_1"})
        self.assertEqual(self.b.cola, [])

    def test_sale_en_orden_con_adjuntos_y_completa_el_envio_de_la_ficha(self):
        self.b.envios[7] = {"resend_id": None}
        encolar(self.b, para="uno@x.com", al_enviar={"tabla": "mails_envios", "id": 7})
        encolar(self.b, para="dos@x.com")
        mandados = []

        def enviar(cuenta, rem, rta, para, asunto, html, texto, adj):
            mandados.append((para[0], adj))
            return f"re_{len(mandados)}"
        r = self.procesar(enviar)
        self.assertEqual(r["enviados"], 2)
        self.assertEqual([m[0] for m in mandados], ["uno@x.com", "dos@x.com"])
        self.assertEqual(mandados[0][1], [{"filename": "p.pdf", "content": b"%PDF"}])
        self.assertEqual(self.b.envios[7]["resend_id"], "re_1")

    def test_si_sigue_sin_cupo_todo_espera_al_proximo_reinicio(self):
        encolar(self.b, para="uno@x.com")
        encolar(self.b, para="dos@x.com")
        llamadas = []

        def sin_cupo(*a, **k):
            llamadas.append(1)
            raise mc.CupoAgotado("prospectos", "daily_quota_exceeded")
        r = self.procesar(sin_cupo)
        self.assertEqual(len(llamadas), 1)   # no insiste con el resto
        self.assertEqual(r["esperando_cupo"], 1)
        self.assertTrue(all(f["estado"] == "pendiente" and f["intentar_desde"] > self.b.ahora for f in self.b.cola))

    def test_baja_mientras_esperaba_cancela_y_borra_el_envio_de_la_ficha(self):
        self.b.envios[3] = {"resend_id": None}
        encolar(self.b, para="baja@x.com", al_enviar={"tabla": "mails_envios", "id": 3})
        self.b.bajas.add("baja@x.com")
        r = self.procesar(lambda *a: self.fail("no tenía que mandar"))
        self.assertEqual(r["cancelados"], 1)
        self.assertEqual(self.b.cola[0]["estado"], "cancelado")
        self.assertNotIn(3, self.b.envios)

    def test_otros_errores_reintentan_y_al_tercero_queda_en_error(self):
        encolar(self.b, al_enviar={"tabla": "verificaciones_marca", "id": 9})

        def falla(*a, **k):
            raise ValueError("Resend rechazó el envío (500)")
        for _ in range(mails_cola.MAX_INTENTOS):
            self.b.cola[0]["intentar_desde"] = self.b.ahora   # que le toque de nuevo
            self.procesar(falla)
        self.assertEqual(self.b.cola[0]["estado"], "error")
        self.assertEqual(self.b.cola[0]["intentos"], mails_cola.MAX_INTENTOS)
        self.assertIn("No salió de la cola", self.b.verif[9]["error"])

    def test_confirmacion_de_la_web_se_marca_enviada_al_salir(self):
        encolar(self.b, al_enviar={"tabla": "verificaciones_marca", "id": 4})
        self.procesar(lambda *a: "re_x")
        self.assertEqual(self.b.verif[4], {"enviada": True, "error": None})

    def test_al_enviar_solo_acepta_tablas_conocidas(self):
        with self.assertRaises(ValueError):
            encolar(self.b, al_enviar={"tabla": "marcas", "id": 1})


if __name__ == "__main__":
    unittest.main()
