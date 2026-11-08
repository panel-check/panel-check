"""Pruebas del reporte de corridas por Telegram (scripts/reporte_corrida.py).
cd scripts && python3 -m unittest test_reporte_corrida"""

import datetime as dt
import re
import unittest
from pathlib import Path

import reporte_corrida as rc

RAIZ = Path(__file__).resolve().parent.parent
WORKFLOWS = RAIZ / ".github" / "workflows"


def _nombre(archivo: Path) -> str:
    n = re.search(r"^name:\s*(.+)$", archivo.read_text(encoding="utf-8"), re.M).group(1).strip()
    return n[1:-1] if n.startswith('"') else n


class TestListaDeWorkflows(unittest.TestCase):
    def test_escucha_todos_los_workflows_menos_el_propio(self):
        texto = (WORKFLOWS / rc.PROPIO).read_text(encoding="utf-8")
        bloque = texto.split("workflows:", 1)[1].split("workflow_dispatch", 1)[0]
        escuchados = set(re.findall(r'^\s+- "(.+)"$', bloque, re.M))
        esperados = {_nombre(f) for f in WORKFLOWS.glob("*.yml") if f.name != rc.PROPIO}
        self.assertEqual(esperados - escuchados, set(), "Hay workflows que no se reportan: sumarlos a la lista")
        self.assertEqual(escuchados - esperados, set(), "Hay nombres en la lista que ya no existen")


class TestTiempos(unittest.TestCase):
    def test_lento(self):
        _, alertas = rc.analizar_tiempos(3600, 0, None, [1200, 1300, 1250, 1400])
        self.assertTrue(any("veces lo normal" in a for a in alertas))

    def test_normal_no_alerta(self):
        normal, alertas = rc.analizar_tiempos(1300, 30, None, [1200, 1300, 1250])
        self.assertEqual(alertas, [])
        self.assertIn("lo normal", normal)

    def test_poco_mas_lento_pero_corto_no_alerta(self):
        # 2× la mediana pero solo 2 minutos más: no vale la pena avisar.
        _, alertas = rc.analizar_tiempos(240, 0, None, [120, 120, 120])
        self.assertEqual(alertas, [])

    def test_demasiado_rapido(self):
        _, alertas = rc.analizar_tiempos(60, 0, None, [3600, 3500, 3700])
        self.assertTrue(any("más rápido" in a for a in alertas))

    def test_sin_historial(self):
        normal, alertas = rc.analizar_tiempos(500, 0, None, [100])
        self.assertIn("historial", normal)
        self.assertEqual(alertas, [])

    def test_espera_y_demora(self):
        _, alertas = rc.analizar_tiempos(100, 20 * 60, 30 * 60, [])
        self.assertEqual(len(alertas), 2)


class TestMetricas(unittest.TestCase):
    HIST = [{"leads_nuevos": 30, "bloqueos": 0}, {"leads_nuevos": 28}, {"leads_nuevos": 35}]

    def test_cae_a_cero(self):
        _, alertas = rc.comparar_metricas({"leads_nuevos": 0}, self.HIST, {"leads_nuevos": "Leads nuevos"})
        self.assertEqual(len(alertas), 1)
        self.assertIn("Leads nuevos: 0", alertas[0])

    def test_dentro_de_lo_normal(self):
        lineas, alertas = rc.comparar_metricas({"leads_nuevos": 27}, self.HIST)
        self.assertEqual(alertas, [])
        self.assertEqual(lineas[0][2], 30)

    def test_suba_fuerte(self):
        _, alertas = rc.comparar_metricas({"leads_nuevos": 120}, self.HIST)
        self.assertIn("más que lo normal", alertas[0])

    def test_bloqueos_siempre_alertan(self):
        _, alertas = rc.comparar_metricas({"bloqueos": 2}, [])
        self.assertIn("INPI", alertas[0])

    def test_no_compara_identificadores(self):
        _, alertas = rc.comparar_metricas({"ultima_acta": 1}, [{"ultima_acta": 4800000}] * 3)
        self.assertEqual(alertas, [])

    def test_etiquetas_salen_del_panel(self):
        et = rc.etiquetas_registro()
        self.assertEqual(et["escanear_actas.yml"]["leads_nuevos"], "Leads nuevos")


class TestOrigenYLog(unittest.TestCase):
    def test_origen(self):
        prog = dt.datetime(2026, 11, 8, 21, 0, tzinfo=dt.timezone.utc)
        self.assertTrue(rc.origen_de({"event": "workflow_dispatch"}, prog).startswith("programada"))
        self.assertEqual(rc.origen_de({"event": "workflow_dispatch", "triggering_actor": {"login": "kom"}}, None), "manual")
        self.assertTrue(rc.origen_de({"event": "workflow_dispatch",
                                      "triggering_actor": {"login": "github-actions[bot]"}}, None).startswith("encadenada"))

    def test_extraer_error(self):
        log = "\n".join([f"2026-11-08T23:00:0{i}.0000000Z linea {i}" for i in range(9)] + [
            "2026-11-08T23:00:10.0Z Traceback (most recent call last):",
            '2026-11-08T23:00:10.0Z   File "x.py", line 3',
            "2026-11-08T23:00:10.0Z KeyError: 'acta'",
            "2026-11-08T23:00:11.0Z ##[error]Process completed with exit code 1.",
        ])
        txt = rc.extraer_error(log)
        self.assertIn("KeyError", txt)
        self.assertNotIn("2026-11-08T", txt)
        self.assertNotIn("linea 2", txt)


class TestMensaje(unittest.TestCase):
    def test_arma_mensaje_html(self):
        ahora = dt.datetime.now(dt.timezone.utc)
        d = {
            "nombre": "Oposiciones <test>", "conclusion": "failure", "intento": 1, "origen": "manual",
            "actor": "kom", "inicio": ahora - dt.timedelta(minutes=40), "fin": ahora, "duracion_s": 2400,
            "espera_s": 120, "normal_txt": "lo normal es ~30 min", "falla": "revisar › Revisar boletines",
            "error": "KeyError: 'acta' & <x>", "lineas_registro": [("Leads revisados", 240, 230.0)],
            "lineas_base": [("Oposiciones detectadas", 12, None)], "solapadas": ["Vigilancia marcaria"],
            "alertas": ["Tardó 1,3 veces lo normal"], "url": "https://github.com/x/actions/runs/1",
        }
        msg = rc.armar_mensaje(d)
        self.assertIn("FALLÓ", msg)
        self.assertIn("&lt;test&gt;", msg)
        self.assertIn("&amp; &lt;x&gt;", msg)
        self.assertIn("normal ~230", msg)
        self.assertIn("Vigilancia marcaria", msg)
        self.assertLess(len(msg), 4096)

    def test_todo_normal(self):
        ahora = dt.datetime.now(dt.timezone.utc)
        msg = rc.armar_mensaje({"nombre": "Feriados", "conclusion": "success", "origen": "programada (06:15)",
                                "inicio": ahora, "fin": ahora, "duracion_s": 40, "espera_s": 0,
                                "sin_base": True, "url": "u"})
        self.assertIn("Todo dentro de lo normal", msg)


if __name__ == "__main__":
    unittest.main()
