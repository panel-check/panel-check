"""«Verificar de nuevo» (Análisis de marca) tiene que dejar la oposición en `marcas`,
de donde la pestaña Marca saca «⚠ Ver oposición», el estado («sin notificar») y la
fecha de última revisión (🔎). Caso real: acta de la clase 35 verificada en Análisis
(1 oposición) pero la fila seguía solo con «LEAD» y «13/10».

    cd scripts && python3 -m unittest test_sincronizar_marca_oposicion
"""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "panel"))
try:
    import analisis_marca as am
except ImportError as e:  # faltan dependencias del panel (pdfplumber, reportlab…)
    am = None
    MOTIVO = str(e)

HOY = dt.date(2026, 10, 15)


class FakeCur:
    def __init__(self):
        self.llamadas = []

    def execute(self, sql, params=None):
        self.llamadas.append((sql, params))


@unittest.skipIf(am is None, "faltan dependencias del panel")
class ClasificarDesdeExpediente(unittest.TestCase):
    def test_sin_notificar(self):
        op = {"oponente": "ACME SA", "presentacion": "2026-10-10", "notificacion": None,
              "vencimiento": None, "levantamiento": None, "agente_oponente": None}
        r = am.clasificar_desde_expediente([op], dt.date(2026, 9, 25), False, hoy=HOY)
        self.assertEqual(r["estado"], "sin_notificar")
        self.assertTrue(r["sirve"])

    def test_oposicion_anterior_a_la_publicacion_no_cuenta(self):
        op = {"presentacion": "2026-08-01", "notificacion": None, "vencimiento": None, "levantamiento": None}
        self.assertIsNone(am.clasificar_desde_expediente([op], dt.date(2026, 9, 25), False, hoy=HOY))

    def test_sin_oposiciones_no_toca_nada(self):
        self.assertIsNone(am.clasificar_desde_expediente([], dt.date(2026, 9, 25), False, hoy=HOY))

    def test_plazo_vencido_y_en_plazo(self):
        base = {"presentacion": "2026-09-26", "notificacion": "2026-09-30", "levantamiento": None}
        v = am.clasificar_desde_expediente([{**base, "vencimiento": "2026-10-10"}], None, False, hoy=HOY)
        self.assertEqual(v["estado"], "plazo_vencido")
        e = am.clasificar_desde_expediente([{**base, "vencimiento": "2026-10-30"}], None, False, hoy=HOY)
        self.assertEqual(e["estado"], "notificada_en_plazo")

    def test_levantada_gana_y_contestada_se_conserva(self):
        op = {"presentacion": "2026-10-01", "notificacion": None, "vencimiento": None, "levantamiento": None}
        self.assertEqual(am.clasificar_desde_expediente([op], None, False, "contestada", HOY)["estado"], "contestada")
        lev = {**op, "levantamiento": "2026-10-12"}
        r = am.clasificar_desde_expediente([lev], None, False, "contestada", HOY)
        self.assertEqual(r["estado"], "levantada")
        self.assertFalse(r["sirve"])

    def test_con_representante_es_con_apoderado(self):
        op = {"presentacion": "2026-10-01", "notificacion": None, "vencimiento": None, "levantamiento": None}
        r = am.clasificar_desde_expediente([op], None, True, hoy=HOY)
        self.assertEqual((r["estado"], r["sirve"]), ("con_apoderado", False))


@unittest.skipIf(am is None, "faltan dependencias del panel")
class SincronizarMarca(unittest.TestCase):
    OP = {"oponente": "ACME SA", "presentacion": "2099-01-01", "notificacion": None,
          "vencimiento": None, "levantamiento": None, "agente_oponente": None}

    def test_lead_con_oposicion_actualiza_marcas(self):
        cur = FakeCur()
        m = {"acta": "4778444", "es_lead": True, "fecha_publicacion": None, "detalle_oposicion": None,
             "estado_oposicion": None}
        self.assertTrue(am.sincronizar_marca(cur, m, [self.OP], False))
        sql, params = cur.llamadas[0]
        self.assertIn("UPDATE marcas", sql)
        self.assertIn("opo_ultimo_chequeo_en = now()", sql)
        self.assertRegex(params[0], "OPO")        # el detalle activa «Ver oposición»
        self.assertEqual(params[2], "sin_notificar")
        self.assertEqual(params[-1], "4778444")

    def test_detalle_existente_con_opo_se_conserva(self):
        cur = FakeCur()
        m = {"acta": "1", "es_lead": True, "fecha_publicacion": None,
             "detalle_oposicion": "12/10/2026 - Oposicion - OPOSICION ACME", "estado_oposicion": None}
        am.sincronizar_marca(cur, m, [self.OP], False)
        self.assertEqual(cur.llamadas[0][1][0], "12/10/2026 - Oposicion - OPOSICION ACME")

    def test_no_lead_o_sin_oposicion_no_escribe(self):
        cur = FakeCur()
        self.assertFalse(am.sincronizar_marca(cur, {"acta": "1", "es_lead": False}, [self.OP], False))
        self.assertFalse(am.sincronizar_marca(cur, {"acta": "1", "es_lead": True, "fecha_publicacion": None},
                                              [], False))
        self.assertEqual(cur.llamadas, [])


if __name__ == "__main__":
    unittest.main()
