"""
Pruebas del programador de procesos del panel (panel/programador.py). La parte pura (cron, ventana de gracia,
coherencia con los .yml) corre sin base; la que usa la base necesita TEST_DATABASE_URL (una base de prueba) y un
GitHub falso. Correr con:
    cd scripts && [TEST_DATABASE_URL=postgresql://...] python3 -m unittest test_programador -v
"""

import contextlib
import datetime as dt
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import programador as pg  # noqa: E402

RAIZ = os.path.join(os.path.dirname(__file__), "..")
WORKFLOWS = os.path.join(RAIZ, ".github", "workflows")
DSN = os.environ.get("TEST_DATABASE_URL")
UTC = dt.timezone.utc


def utc(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=UTC)


def yml(nombre):
    with open(os.path.join(WORKFLOWS, nombre), encoding="utf-8") as f:
        return f.read()


class Cron(unittest.TestCase):
    def test_dias_de_la_semana_como_en_github(self):
        expr = "5 2 * * 0,1,2,4,5,6"   # todas las noches menos las de «miércoles UTC» (martes de noche en Argentina)
        self.assertTrue(pg.coincide(expr, utc(2026, 10, 27, 2, 5)))     # martes UTC
        self.assertFalse(pg.coincide(expr, utc(2026, 10, 28, 2, 5)))    # miércoles UTC
        self.assertTrue(pg.coincide(expr, utc(2026, 11, 1, 2, 5)))      # domingo (0)
        self.assertFalse(pg.coincide(expr, utc(2026, 10, 27, 2, 6)))
        self.assertTrue(pg.coincide("0 17 * * 3", utc(2026, 10, 28, 17, 0)))   # miércoles 14:00 ART
        self.assertFalse(pg.coincide("0 17 * * 3", utc(2026, 10, 29, 17, 0)))

    def test_rangos_pasos_y_domingo_como_7(self):
        self.assertTrue(pg.coincide("*/15 * * * 1-5", utc(2026, 10, 26, 3, 45)))
        self.assertFalse(pg.coincide("*/15 * * * 1-5", utc(2026, 10, 25, 3, 45)))   # domingo
        self.assertTrue(pg.coincide("0 0 * * 7", utc(2026, 10, 25, 0, 0)))

    def test_no_acepta_dia_del_mes_ni_mes(self):
        with self.assertRaises(ValueError):
            pg.coincide("0 0 1 * *", utc(2026, 10, 1, 0, 0))

    def test_ultima_programada_dentro_de_la_gracia(self):
        self.assertEqual(pg.ultima_programada("0 21 * * *", utc(2026, 10, 28, 0, 10), 360), utc(2026, 10, 27, 21, 0))
        self.assertIsNone(pg.ultima_programada("0 21 * * *", utc(2026, 10, 28, 3, 1), 360))
        self.assertEqual(pg.ultima_programada("0 21 * * *", utc(2026, 10, 28, 3, 0, ), 360), utc(2026, 10, 27, 21, 0))

    def test_proxima(self):
        self.assertEqual(pg.proxima("0 17 * * 3", utc(2026, 10, 28, 17, 0)), utc(2026, 11, 4, 17, 0))
        self.assertEqual(pg.proxima("10 2 * * *", utc(2026, 10, 27, 12, 0)), utc(2026, 10, 28, 2, 10))


class Pendientes(unittest.TestCase):
    def test_hoy_el_escaneo_atrasado_sigue_pendiente_pasadas_3_horas(self):
        d = pg.pendientes(utc(2026, 10, 28, 0, 10), set())
        self.assertIn("escanear_actas.yml", [x["workflow"] for x in d])

    def test_lo_ya_hecho_no_vuelve(self):
        d = pg.pendientes(utc(2026, 10, 28, 0, 10), {("escanear_actas.yml", utc(2026, 10, 27, 21, 0))})
        self.assertNotIn("escanear_actas.yml", [x["workflow"] for x in d])

    def test_orden_por_hora_programada(self):
        # a las 02:30 UTC (23:30 ART) tocaron cartera 01:35, sin verificar 02:05 y oposiciones 02:10
        d = pg.pendientes(utc(2026, 10, 27, 2, 30), set(), [p for p in pg.PROGRAMADOS if p["workflow"] in
                          ("revisar_oposiciones.yml", "revisar_cartera.yml", "reintentar_sin_verificar.yml")])
        self.assertEqual([x["workflow"] for x in d], ["revisar_cartera.yml", "reintentar_sin_verificar.yml", "revisar_oposiciones.yml"])

    def test_inputs_de_la_revision_diaria_de_vistas(self):
        d = {x["workflow"]: x for x in pg.pendientes(utc(2026, 10, 27, 21, 10), set())}
        self.assertEqual(d["probar_vistas_preboletin.yml"]["inputs"]["hasta_hora"], "22:40")
        self.assertEqual(d["probar_vistas_preboletin.yml"]["inputs"]["guardar"], "true")


class CoherenciaConLosWorkflows(unittest.TestCase):
    def test_el_carril_de_inpi_es_el_de_los_yml(self):
        reales = {f for f in os.listdir(WORKFLOWS) if f.endswith(".yml") and re.search(r"group:\s*inpi-lane", yml(f))}
        self.assertEqual(set(pg.CARRIL_INPI), reales)

    def test_los_programados_ya_no_tienen_schedule_en_github(self):
        for p in pg.PROGRAMADOS:
            self.assertNotRegex(yml(p["workflow"]), r"(?m)^\s*schedule:", f"{p['workflow']} sigue con horario de GitHub (se dispararía dos veces)")
            self.assertIn("workflow_dispatch:", yml(p["workflow"]))

    def test_solo_verificar_despliegue_conserva_su_horario(self):
        con_schedule = {f for f in os.listdir(WORKFLOWS) if f.endswith(".yml") and re.search(r"(?m)^\s*schedule:", yml(f))}
        self.assertEqual(con_schedule, {"verificar_despliegue.yml"})

    def test_los_inputs_que_se_pasan_existen_en_el_workflow(self):
        for p in pg.PROGRAMADOS:
            for k in p.get("inputs", {}):
                self.assertRegex(yml(p["workflow"]), rf"(?m)^      {k}:", f"{p['workflow']} no tiene el input {k}")

    def test_todos_los_crons_se_entienden_y_son_unicos_por_workflow(self):
        self.assertEqual(len({p["workflow"] for p in pg.PROGRAMADOS}), len(pg.PROGRAMADOS))
        for p in pg.PROGRAMADOS:
            self.assertIsNotNone(pg.proxima(p["cron"], utc(2026, 10, 27, 0, 0)), p["workflow"])

    def test_la_tarjeta_de_automatizaciones_lee_los_horarios_de_aca(self):
        with open(os.path.join(RAIZ, "panel", "app.py"), encoding="utf-8") as f:
            app = f.read()
        for p in pg.PROGRAMADOS:
            self.assertIn(f'programador.cron_de("{p["workflow"]}")', app, p["workflow"])
        # y no queda ningún cron escrito a mano de un proceso programado
        for p in pg.PROGRAMADOS:
            self.assertNotIn(f'"cron": "{p["cron"]}"', app)


class GitHubFalso:
    def __init__(self):
        self.corrieron = set()     # workflows con una corrida ya hecha
        self.ocupado = False
        self.rechaza = False
        self.explota_en_corrio = False
        self.disparos = []

    def corrio_desde(self, workflow, desde):
        if self.explota_en_corrio:
            raise RuntimeError("GitHub no responde")
        return workflow in self.corrieron

    def carril_ocupado(self):
        return self.ocupado

    def disparar(self, workflow, inputs):
        if self.rechaza:
            return False, "GitHub devolvió 403: Resource not accessible by integration"
        self.disparos.append((workflow, inputs))
        return True, "disparado"


@unittest.skipUnless(DSN, "falta TEST_DATABASE_URL (base de prueba)")
class ConBase(unittest.TestCase):
    def setUp(self):
        import psycopg2

        @contextlib.contextmanager
        def conexion():
            c = psycopg2.connect(DSN)
            try:
                yield c
            finally:
                c.close()
        self.conexion = conexion
        with conexion() as c:
            with c.cursor() as cur:
                pg.crear_tabla(cur)
                cur.execute("DELETE FROM programador_disparos")
            c.commit()
        self.gh, self.estado, self.t = GitHubFalso(), pg.Estado(), [1000.0]
        self.avisos = []

    def correr(self, ahora, **kw):
        return pg.pasada(self.conexion, self.gh, self.estado, ahora=ahora, reloj=lambda: self.t[0],
                         avisar=lambda w, p, d: self.avisos.append((w, d)), **kw)

    def filas(self):
        with self.conexion() as c:
            with c.cursor() as cur:
                cur.execute("SELECT workflow, estado, intentos FROM programador_disparos ORDER BY programada, workflow")
                return cur.fetchall()

    AHORA = utc(2026, 10, 28, 0, 10)   # 21:10 ART: el escaneo de las 18:00 está atrasado

    def test_dispara_una_vez_y_no_repite(self):
        self.assertIn(("escanear_actas.yml", "ok"), self.correr(self.AHORA))
        self.assertEqual(self.gh.disparos[0][0], "escanear_actas.yml")
        n = len(self.gh.disparos)
        self.t[0] += 600
        self.correr(self.AHORA + dt.timedelta(minutes=10))
        self.assertEqual([d for d in self.gh.disparos if d[0] == "escanear_actas.yml"], [("escanear_actas.yml", {})])
        self.assertLessEqual(len(self.gh.disparos), n + 1)

    def test_si_ya_corrio_desde_la_hora_programada_no_lo_dispara(self):
        self.gh.corrieron = {"escanear_actas.yml"}   # por ejemplo, lanzado a mano a las 21:10
        r = self.correr(self.AHORA)
        self.assertIn(("escanear_actas.yml", "omitido"), r)
        self.assertNotIn("escanear_actas.yml", [d[0] for d in self.gh.disparos])
        self.assertIn(("escanear_actas.yml", "omitido", 1), self.filas())

    def test_carril_ocupado_espera_y_despues_sale(self):
        self.gh.ocupado = True
        self.assertEqual(self.correr(self.AHORA), [])
        self.assertEqual(self.filas(), [])
        self.gh.ocupado = False
        self.assertIn(("escanear_actas.yml", "ok"), self.correr(self.AHORA + dt.timedelta(minutes=1)))

    def test_un_solo_proceso_del_carril_por_vez_y_en_orden(self):
        ahora = utc(2026, 10, 27, 2, 30)   # lunes 23:30 ART: tocaron cartera, sin verificar y oposiciones (y antes, escaneo/vistas)
        self.gh.corrieron = {"escanear_actas.yml", "probar_vistas_preboletin.yml"}
        primero = [r for r in self.correr(ahora) if r[1] == "ok"]
        self.assertEqual([w for w, _ in primero if w in pg.CARRIL_INPI], ["revisar_cartera.yml"])
        self.t[0] += 60       # todavía no pasaron los 2 minutos
        self.assertEqual([w for w, r in self.correr(ahora) if r == "ok" and w in pg.CARRIL_INPI], [])
        self.t[0] += 120
        self.assertEqual([w for w, r in self.correr(ahora) if r == "ok" and w in pg.CARRIL_INPI], ["reintentar_sin_verificar.yml"])
        self.t[0] += 130
        self.assertEqual([w for w, r in self.correr(ahora) if r == "ok" and w in pg.CARRIL_INPI], ["revisar_oposiciones.yml"])

    def test_los_procesos_fuera_del_carril_no_esperan(self):
        self.gh.ocupado = True
        r = self.correr(utc(2026, 10, 27, 10, 40))   # 07:40 ART: avisos de la mañana (hora programada 07:30)
        self.assertIn(("avisos_manana.yml", "ok"), r)

    def test_si_github_rechaza_se_reintenta_y_se_avisa_una_sola_vez(self):
        self.gh.rechaza = True
        ahora = utc(2026, 10, 27, 10, 40)
        for _ in range(4):
            self.correr(ahora)
            with self.conexion() as c:     # simula que pasaron los 2 minutos
                with c.cursor() as cur:
                    cur.execute("UPDATE programador_disparos SET ultimo_intento = now() - interval '3 minutes'")
                c.commit()
        self.assertEqual([f for f in self.filas() if f[0] == "avisos_manana.yml"], [("avisos_manana.yml", "error", 4)])
        # un solo mail por proceso, al tercer intento (además de avisos, esa mañana también tocaban feriados, sin email y sin nombre)
        self.assertEqual([w for w, _ in self.avisos].count("avisos_manana.yml"), 1)
        self.assertEqual(len(self.avisos), len({w for w, _ in self.avisos}))
        self.gh.rechaza = False
        self.assertIn(("avisos_manana.yml", "ok"), self.correr(ahora))
        self.assertIn(("avisos_manana.yml", "ok", 5), self.filas())

    def test_no_reintenta_antes_de_los_2_minutos(self):
        self.gh.rechaza = True
        ahora = utc(2026, 10, 27, 10, 40)
        self.correr(ahora)
        self.gh.rechaza = False
        self.assertNotIn(("avisos_manana.yml", "ok"), self.correr(ahora))

    def test_se_rinde_despues_de_los_intentos_maximos(self):
        self.gh.rechaza = True
        ahora = utc(2026, 10, 27, 10, 40)
        for _ in range(pg.MAX_INTENTOS + 3):
            self.correr(ahora)
            with self.conexion() as c:
                with c.cursor() as cur:
                    cur.execute("UPDATE programador_disparos SET ultimo_intento = now() - interval '3 minutes'")
                c.commit()
        self.assertEqual([f for f in self.filas() if f[0] == "avisos_manana.yml"], [("avisos_manana.yml", "error", pg.MAX_INTENTOS)])

    def test_un_error_de_github_al_consultar_se_reintenta(self):
        self.gh.explota_en_corrio = True
        ahora = utc(2026, 10, 27, 10, 40)
        self.correr(ahora)
        self.assertIn(("avisos_manana.yml", "error", 1), self.filas())
        self.gh.explota_en_corrio = False
        with self.conexion() as c:
            with c.cursor() as cur:
                cur.execute("UPDATE programador_disparos SET ultimo_intento = now() - interval '3 minutes'")
            c.commit()
        self.assertIn(("avisos_manana.yml", "ok"), self.correr(ahora))

    def test_dos_instancias_no_disparan_dos_veces(self):
        with self.conexion() as c:
            with c.cursor() as cur:
                a = pg.reclamar(cur, "feriados.yml", utc(2026, 10, 27, 9, 15))
                c.commit()
                b = pg.reclamar(cur, "feriados.yml", utc(2026, 10, 27, 9, 15))
                c.commit()
        self.assertEqual((a, b), (True, False))

    def test_estado_para_el_panel(self):
        self.correr(self.AHORA)
        with self.conexion() as c:
            with c.cursor() as cur:
                e = pg.estado_para_panel(cur, self.AHORA)
        self.assertEqual(e["ultimos"][0]["estado"], "ok")
        self.assertEqual(len(e["proximos"]), len(pg.PROGRAMADOS))


if __name__ == "__main__":
    unittest.main()
