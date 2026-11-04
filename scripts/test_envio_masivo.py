"""
Pruebas del envío masivo escalonado del mail de oposición / vista (panel/envio_masivo.py) y de cómo lo
trata la cola de mails (panel/mails_cola.py), sin red ni base. Correr con:
    cd scripts && python3 -m unittest test_envio_masivo -v
"""

import datetime as dt
import random
import unittest
from unittest import mock

import test_mails_cola as tmc   # deja armado el reemplazo de psycopg2 y la base falsa de la cola
from test_mails_cola import Base, Cursor, fabrica

import envio_masivo as em  # noqa: E402
import mails_cola  # noqa: E402

AR = em.TZ_AR
UTC = dt.timezone.utc


def p(**k):
    return em.normalizar_parametros({"variar": False, **k})


def ar(*a):
    return dt.datetime(*a, tzinfo=AR)


class Parametros(unittest.TestCase):
    def test_valores_por_defecto(self):
        x = em.normalizar_parametros(None)
        self.assertEqual((x["intervalo_minutos"], x["hora_desde"], x["hora_hasta"], x["max_por_dia"]), (4, 9, 19, 60))
        self.assertTrue(x["solo_habiles"])
        self.assertFalse(x["incluir_contactadas"])

    def test_franja_no_puede_pasar_de_las_21(self):
        with self.assertRaises(ValueError):
            em.normalizar_parametros({"hora_desde": 10, "hora_hasta": 22})
        with self.assertRaises(ValueError):
            em.normalizar_parametros({"hora_desde": 15, "hora_hasta": 15})

    def test_maximo_por_dia_bajo_el_cupo_de_resend(self):
        with self.assertRaises(ValueError):
            em.normalizar_parametros({"max_por_dia": 100})
        with self.assertRaises(ValueError):
            em.normalizar_parametros({"intervalo_minutos": 0})

    def test_inicio_sin_zona_es_hora_argentina(self):
        x = em.normalizar_parametros({"inicio": "2026-11-05T10:30"})
        self.assertEqual(x["inicio"], ar(2026, 11, 5, 10, 30))


class Horarios(unittest.TestCase):
    def test_de_a_uno_con_el_intervalo(self):
        hs = em.calcular_horarios(3, p(intervalo_minutos=5), ar(2026, 11, 4, 10, 0, 20))
        self.assertEqual(hs, [ar(2026, 11, 4, 10, 1), ar(2026, 11, 4, 10, 6), ar(2026, 11, 4, 10, 11)])

    def test_fuera_de_la_franja_espera_al_dia_siguiente(self):
        hs = em.calcular_horarios(2, p(intervalo_minutos=30, hora_desde=9, hora_hasta=19), ar(2026, 11, 4, 18, 45))
        self.assertEqual(hs, [ar(2026, 11, 4, 18, 45), ar(2026, 11, 5, 9, 0)])

    def test_antes_de_la_franja_arranca_a_la_hora_de_inicio(self):
        hs = em.calcular_horarios(1, p(), ar(2026, 11, 4, 6, 12))
        self.assertEqual(hs, [ar(2026, 11, 4, 9, 0)])

    def test_viernes_tarde_pasa_al_lunes(self):
        hs = em.calcular_horarios(1, p(), ar(2026, 11, 6, 20, 0))   # viernes 20:00
        self.assertEqual(hs, [ar(2026, 11, 9, 9, 0)])
        hs = em.calcular_horarios(1, p(solo_habiles=False), ar(2026, 11, 6, 20, 0))
        self.assertEqual(hs, [ar(2026, 11, 7, 9, 0)])

    def test_maximo_por_dia(self):
        hs = em.calcular_horarios(5, p(intervalo_minutos=1, max_por_dia=2), ar(2026, 11, 4, 10, 0))
        self.assertEqual([h.date() for h in hs], [dt.date(2026, 11, 4)] * 2 + [dt.date(2026, 11, 5)] * 2 + [dt.date(2026, 11, 6)])

    def test_respeta_lo_ya_ocupado_del_dia_bajo_el_cupo(self):
        ocup = {dt.date(2026, 11, 4): em.LIMITE_GLOBAL_DIA - 1}
        hs = em.calcular_horarios(3, p(intervalo_minutos=1), ar(2026, 11, 4, 10, 0), ocup)
        self.assertEqual(hs[0].date(), dt.date(2026, 11, 4))
        self.assertEqual(hs[1], ar(2026, 11, 5, 9, 0))

    def test_inicio_posterior(self):
        hs = em.calcular_horarios(1, p(inicio="2026-11-05T11:00"), ar(2026, 11, 4, 10, 0))
        self.assertEqual(hs, [ar(2026, 11, 5, 11, 0)])

    def test_variacion_al_azar_dentro_del_50_por_ciento(self):
        hs = em.calcular_horarios(50, em.normalizar_parametros({"intervalo_minutos": 4, "solo_habiles": False,
                                                                "hora_desde": 0, "hora_hasta": 21, "max_por_dia": 95}),
                                  ar(2026, 11, 4, 9, 0), rng=random.Random(1))
        pasos = {int((b - a).total_seconds() // 60) for a, b in zip(hs, hs[1:])}
        self.assertTrue(pasos <= {4, 5, 6}, pasos)
        self.assertGreater(len(pasos), 1)

    def test_nunca_dos_en_el_mismo_minuto_y_siempre_en_orden(self):
        hs = em.calcular_horarios(200, p(intervalo_minutos=1), ar(2026, 11, 4, 9, 0))
        self.assertEqual(len(set(hs)), 200)
        self.assertEqual(hs, sorted(hs))
        for h in hs:
            self.assertTrue(9 <= h.hour < 19 and h.weekday() < 5)

    def test_resumen(self):
        hs = em.calcular_horarios(3, p(intervalo_minutos=60, max_por_dia=2), ar(2026, 11, 4, 10, 0))
        r = em.resumen_horarios(hs)
        self.assertEqual(r["dias"], 2)
        self.assertEqual([d["cantidad"] for d in r["por_dia"]], [2, 1])


class Orden(unittest.TestCase):
    def test_el_mismo_destinatario_no_recibe_dos_seguidos(self):
        items = [{"email": "a@x.com", "acta": 1}, {"email": "A@x.com", "acta": 2}, {"email": "b@x.com", "acta": 3},
                 {"email": "c@x.com", "acta": 4}, {"email": "a@x.com", "acta": 5}]
        self.assertEqual([i["acta"] for i in em.ordenar_espaciando(items)], [1, 3, 4, 2, 5])


class ColaConLotes(unittest.TestCase):
    """La cola manda los mails de un lote por su hora, los valida justo antes y avisa cuando salieron."""

    def setUp(self):
        self.b = Base()
        self.con = fabrica(self.b)
        self.ganchos = dict(mails_cola.GANCHOS)

    def tearDown(self):
        mails_cola.GANCHOS.update(self.ganchos)

    def encolar_de_lote(self, minutos, lote=7, acta="4790000"):
        tmc.encolar(self.b, al_enviar={"tabla": "mails_envios", "id": len(self.b.cola) + 1})
        f = self.b.cola[-1]
        self.b.envios[f["id"]] = {"resend_id": None}
        f.update(lote_id=lote, acta=acta, intentar_desde=self.b.ahora + dt.timedelta(minutes=minutos))
        return f

    def procesar(self, enviar):
        with mock.patch.object(mails_cola, "proximo_reinicio", return_value=self.b.ahora + dt.timedelta(days=1)):
            return mails_cola.procesar(self.con, enviar=enviar, pausa=0, ahora=self.b.ahora)

    def test_solo_sale_el_que_ya_llego_a_su_hora(self):
        a = self.encolar_de_lote(0)
        b = self.encolar_de_lote(5)
        despues = []
        mails_cola.GANCHOS.update(validar=lambda cur, f: None, despues=lambda cur, f, rid: despues.append((f["id"], rid)))
        r = self.procesar(lambda *a, **k: "rid-1")
        self.assertEqual(r["enviados"], 1)
        self.assertEqual((a["estado"], b["estado"]), ("enviado", "pendiente"))
        self.assertEqual(despues, [(a["id"], "rid-1")])

    def test_si_ya_no_corresponde_se_cancela_solo(self):
        a = self.encolar_de_lote(0)
        mails_cola.GANCHOS.update(validar=lambda cur, f: "la oposición ya no está pendiente.", despues=None)
        enviar = mock.Mock()
        r = self.procesar(enviar)
        enviar.assert_not_called()
        self.assertEqual(r["cancelados"], 1)
        self.assertEqual(a["estado"], "cancelado")
        self.assertIn("ya no está pendiente", a["ultimo_error"])
        self.assertNotIn(a["id"], self.b.envios)   # no queda «ya se le mandó»

    def test_un_error_despues_de_mandar_no_frena_la_cola(self):
        a = self.encolar_de_lote(0)
        c = self.encolar_de_lote(0, acta="4790001")

        def falla(cur, f, rid):
            raise RuntimeError("crm caído")
        mails_cola.GANCHOS.update(validar=None, despues=falla)
        r = self.procesar(lambda *x, **k: "rid")
        self.assertEqual(r["enviados"], 2)
        self.assertEqual((a["estado"], c["estado"]), ("enviado", "enviado"))

    def test_sin_cupo_el_lote_se_reprograma_con_sus_reglas(self):
        a = self.encolar_de_lote(0)
        tmc.encolar(self.b)                        # uno común, mandado desde la ficha
        comun = self.b.cola[-1]
        comun["intentar_desde"] = self.b.ahora + dt.timedelta(minutes=30)

        def sin_cupo(*x, **k):
            raise tmc.mc.CupoAgotado("prospectos", "daily_quota_exceeded")
        with mock.patch.object(em, "reprogramar") as rep:
            r = self.procesar(sin_cupo)
        self.assertEqual(r["esperando_cupo"], 1)
        rep.assert_called_once()
        self.assertEqual(rep.call_args[0][1], 7)
        self.assertEqual(a["estado"], "pendiente")
        self.assertEqual(comun["intentar_desde"], self.b.ahora + dt.timedelta(days=1))   # el común, al reinicio
        self.assertEqual(a["intentar_desde"], self.b.ahora)                              # el del lote lo mueve reprogramar


if __name__ == "__main__":
    unittest.main()
