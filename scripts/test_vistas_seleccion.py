"""
Qué marcas elige la revisión de vistas (scripts/probar_vistas_preboletin.py), probado contra un Postgres real.
No usa la base de producción: necesita TEST_DATABASE_URL (una base vacía de prueba); sin eso se saltea. Correr con:
    cd scripts && TEST_DATABASE_URL=postgresql://... python3 -m unittest test_vistas_seleccion -v
"""

import datetime as dt
import os
import unittest

import psycopg2
import psycopg2.extras

import probar_vistas_preboletin as pv

DSN = os.environ.get("TEST_DATABASE_URL")
HOY = dt.date(2026, 10, 28)  # miércoles
PARAMS = {"hoy": HOY.isoformat(), "dias_min": 3, "dias_venc": 7, "cada_vista": 1, "cada_joven": 3,
          "dias_jovenes": 30, "cada": 7}

TABLA = """
CREATE TEMP TABLE marcas (
    acta TEXT PRIMARY KEY, denominacion TEXT, denominacion_inpi TEXT, titular TEXT,
    es_lead BOOLEAN DEFAULT true, boletin TEXT, contactado BOOLEAN, fecha_presentacion TIMESTAMPTZ,
    vista_grilla_estado TEXT, vista_estado TEXT, estado_tramite TEXT,
    tuvo_vista BOOLEAN, vista_sirve BOOLEAN, vista_revisada_en TIMESTAMPTZ,
    vista_vencimiento_calculado DATE, vista_fecha_vencimiento DATE, vista_detalle TEXT
)
"""


def ar(dias_atras, hora="12:00"):
    """Instante de Argentina: `dias_atras` días antes de HOY a esa hora (SQL literal con zona)."""
    d = HOY - dt.timedelta(days=dias_atras)
    return f"{d.isoformat()} {hora}-03"


@unittest.skipUnless(DSN, "falta TEST_DATABASE_URL (base de prueba)")
class Seleccion(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg2.connect(DSN)
        with self.conn.cursor() as cur:
            cur.execute(TABLA)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def marca(self, acta, presentada=10, revisada=None, **extra):
        cols = {"acta": acta, "fecha_presentacion": ar(presentada), "vista_revisada_en": revisada, **extra}
        with self.conn.cursor() as cur:
            cur.execute(f"INSERT INTO marcas ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", list(cols.values()))

    def elegidas(self, repaso=False, **cambios):
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(pv.sql_candidatas(repaso), {**PARAMS, **cambios})
            return [r["acta"] for r in cur.fetchall()]

    # ---- antigüedad mínima
    def test_minimo_de_3_dias_desde_la_presentacion(self):
        self.marca("A2", presentada=2)
        self.marca("A3", presentada=3)
        self.assertEqual(self.elegidas(), ["A3"])

    # ---- nunca dos veces el mismo día
    def test_una_marca_revisada_hoy_no_vuelve_a_salir_sea_cual_sea_su_tipo(self):
        self.marca("joven", presentada=10, revisada=ar(0, "08:00"))
        self.marca("vieja", presentada=90, revisada=ar(0, "08:00"))
        self.marca("vista", presentada=10, revisada=ar(0, "08:00"), tuvo_vista=True, vista_sirve=True, vista_estado="notificada_en_plazo")
        self.marca("nunca_paso", presentada=10)
        self.assertEqual(self.elegidas(), ["nunca_paso"])

    def test_el_dia_se_cuenta_en_hora_argentina_no_en_utc(self):
        # revisada ayer 23:30 ART = hoy 02:30 UTC: para la base ya es «hoy», para Argentina es ayer -> corresponde revisarla
        self.marca("ayer_noche", revisada=ar(1, "23:30"), tuvo_vista=True, vista_sirve=True)
        # revisada hoy 00:30 ART = hoy 03:30 UTC: es hoy en los dos relojes -> no
        self.marca("hoy_madrugada", revisada=ar(0, "00:30"), tuvo_vista=True, vista_sirve=True)
        # revisada ayer 21:30 ART = hoy 00:30 UTC (la corrida de la tarde de ayer): corresponde
        self.marca("ayer_tarde", revisada=ar(1, "21:30"), tuvo_vista=True, vista_sirve=True)
        self.assertEqual(sorted(self.elegidas()), ["ayer_noche", "ayer_tarde"])

    def test_la_corrida_de_ayer_tarde_no_se_adelanta_ni_se_salta_un_dia(self):
        # con intervalo de 1 día la de ayer 20:30 vuelve a salir hoy aunque hoy sea más temprano (las horas no cuentan)
        self.marca("ayer", revisada=ar(1, "20:30"), tuvo_vista=True, vista_sirve=True)
        self.assertEqual(self.elegidas(), ["ayer"])

    def test_intervalos_por_tipo_de_marca(self):
        self.marca("joven_2d", presentada=10, revisada=ar(2))
        self.marca("joven_3d", presentada=10, revisada=ar(3))
        self.marca("vieja_6d", presentada=60, revisada=ar(6))
        self.marca("vieja_7d", presentada=60, revisada=ar(7))
        self.marca("vista_ayer", presentada=60, revisada=ar(1), tuvo_vista=True, vista_sirve=True)
        self.assertEqual(sorted(self.elegidas()), ["joven_3d", "vieja_7d", "vista_ayer"])

    def test_la_corrida_se_puede_repetir_el_mismo_dia_sin_revisar_nada_de_nuevo(self):
        for i in range(5):
            self.marca(f"M{i}", presentada=20 + i)
        primera = self.elegidas()
        self.assertEqual(len(primera), 5)
        with self.conn.cursor() as cur:  # lo que hace guardar_resultado al terminar cada marca
            cur.execute("UPDATE marcas SET vista_revisada_en = %s", (ar(0, "20:15"),))
        self.assertEqual(self.elegidas(), [])
        self.assertEqual(self.elegidas(repaso=True), [])

    # ---- lo que no se vuelve a revisar
    def test_cerradas_no_se_revisan_nunca_mas(self):
        self.marca("contestada", vista_estado="resuelta", tuvo_vista=True)
        self.marca("denegada", vista_estado="denegada", tuvo_vista=True)
        self.marca("con_apoderado", vista_estado="con_apoderado", tuvo_vista=True)
        self.marca("denegada_por_estado", estado_tramite="Denegada")
        self.marca("orden", vista_grilla_estado="orden_publicacion")
        self.marca("publicada", vista_grilla_estado="publicada")
        self.marca("grilla_denegada", vista_grilla_estado="vista_denegada")
        self.marca("ya_publicada_en_boletin", boletin="11200")
        self.marca("no_lead", es_lead=False)
        self.marca("abierta")
        self.assertEqual(self.elegidas(), ["abierta"])
        self.assertEqual(self.elegidas(repaso=True), [])

    def test_vencida_hace_mas_de_7_dias_sale_de_la_diaria_pero_el_repaso_la_sigue_45(self):
        self.marca("venc_8", tuvo_vista=True, vista_sirve=True, vista_estado="plazo_vencido",
                   vista_fecha_vencimiento=HOY - dt.timedelta(days=8))
        self.marca("venc_5", tuvo_vista=True, vista_sirve=True, vista_estado="plazo_vencido",
                   vista_fecha_vencimiento=HOY - dt.timedelta(days=5))
        self.assertEqual(self.elegidas(), ["venc_5"])
        self.assertEqual(sorted(self.elegidas(repaso=True)), ["venc_5"])
        self.assertEqual(sorted(self.elegidas(repaso=True, dias_venc=45)), ["venc_5", "venc_8"])

    # ---- contactadas
    def test_las_contactadas_no_van_en_la_diaria_pero_si_en_el_repaso_si_tuvieron_vista(self):
        self.marca("contactada_con_vista", contactado=True, tuvo_vista=True, vista_sirve=True, vista_estado="plazo_vencido")
        self.marca("contactada_sin_vista", contactado=True)
        self.marca("no_contactada_con_vista", tuvo_vista=True, vista_sirve=True, vista_estado="sin_notificar")
        self.assertEqual(self.elegidas(), ["no_contactada_con_vista"])
        self.assertEqual(sorted(self.elegidas(repaso=True)), ["contactada_con_vista", "no_contactada_con_vista"])

    def test_el_repaso_no_incluye_marcas_que_nunca_tuvieron_vista(self):
        self.marca("sin_vista")
        self.assertEqual(self.elegidas(repaso=True), [])

    def test_el_repaso_no_exige_antiguedad_minima(self):
        self.marca("reciente", presentada=1, tuvo_vista=True, vista_sirve=True)
        self.assertEqual(self.elegidas(repaso=True), ["reciente"])

    # ---- orden
    def test_primero_las_vistas_sin_contestar_y_despues_las_menos_revisadas(self):
        self.marca("vieja_nunca", presentada=60)
        self.marca("joven_hace_5d", presentada=10, revisada=ar(5))
        self.marca("joven_hace_9d", presentada=10, revisada=ar(9))
        self.marca("vista", presentada=60, revisada=ar(1), tuvo_vista=True, vista_sirve=True)
        self.assertEqual(self.elegidas(), ["vista", "vieja_nunca", "joven_hace_9d", "joven_hace_5d"])

    def test_una_corrida_cortada_sigue_por_las_que_no_alcanzo(self):
        for i in range(6):
            self.marca(f"M{i}", presentada=30 + i)
        todas = self.elegidas()
        hechas = todas[:2]  # la corrida se corta a las 22:40 con 2 marcas hechas
        with self.conn.cursor() as cur:
            cur.execute("UPDATE marcas SET vista_revisada_en = %s WHERE acta = ANY(%s)", (ar(0, "20:30"), hechas))
        restantes = self.elegidas()
        self.assertEqual(restantes, todas[2:])
        self.assertFalse(set(restantes) & set(hechas))

    # ---- denegadas conocidas
    def test_cerrar_denegadas_conocidas(self):
        self.marca("den", tuvo_vista=True, vista_sirve=True, vista_estado="plazo_vencido", estado_tramite="Denegada")
        self.marca("viva", tuvo_vista=True, vista_sirve=True, vista_estado="sin_notificar")
        self.marca("sin_vista", estado_tramite="Denegada")
        self.assertEqual(pv.cerrar_denegadas_conocidas(self.conn), 1)
        with self.conn.cursor() as cur:
            cur.execute("SELECT acta, vista_estado, vista_sirve FROM marcas WHERE acta IN ('den', 'viva') ORDER BY acta")
            self.assertEqual(cur.fetchall(), [("den", "denegada", False), ("viva", "sin_notificar", True)])
        self.assertEqual(pv.cerrar_denegadas_conocidas(self.conn), 0)  # idempotente


class SinBase(unittest.TestCase):
    def test_denegada_en_el_expediente(self):
        self.assertTrue(pv._denegada({"estado_tramite": "Denegada"}))
        self.assertTrue(pv._denegada({"estado_tramite": " denegada "}))
        self.assertFalse(pv._denegada({"estado_tramite": "Concedida"}))
        self.assertFalse(pv._denegada({"estado_tramite": None}))
        self.assertFalse(pv._denegada({}))

    def test_el_sql_de_la_corrida_diaria_y_el_del_repaso_usan_parametros_con_nombre(self):
        for repaso in (False, True):
            self.assertNotIn("now()", pv.sql_candidatas(repaso).replace("vista_revisada_en", ""))
            self.assertIn("%(hoy)s", pv.sql_candidatas(repaso))


if __name__ == "__main__":
    unittest.main()
