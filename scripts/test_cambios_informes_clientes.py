"""Pruebas de los cambios del 12/11/2026: UBICACIÓN ACTUAL del expediente (Clientes → «Último movimiento
INPI»), archivar informes (Más → Informes) y el filtro «No contactar» de Leads.
Correr: cd scripts && python3 -m unittest test_cambios_informes_clientes"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))

# HTML real del portal (acta 4759596, 12/11/2026), recortado.
HTML_UBICACION = """
<a class="accordion-toggle collapsed" data-toggle="collapse" href="#collapse-ten">
    UBICACIÓN DEL EXPEDIENTE
</a></h4></div>
<div id="collapse-ten" class="panel-collapse collapse in"><div class="panel-body"><section>
    <label class="input">
        UBICACIÓN ACTUAL: <span class="text-danger">2026-09-30 Divisi&#243;n Notificaciones</span>
    </label>
</section></div></div>
"""


class TestUbicacion(unittest.TestCase):
    def setUp(self):
        try:
            import inpi_lead
        except ImportError as e:
            self.skipTest(f"falta una dependencia del panel: {e}")
        self.il = inpi_lead

    def test_lee_sector_y_fecha(self):
        r = self.il.ubicacion_expediente(HTML_UBICACION)
        self.assertEqual(r, {"ubicacion_actual": "División Notificaciones", "ubicacion_fecha": "2026-09-30"})

    def test_acento_como_entidad_y_sin_fecha(self):
        r = self.il.ubicacion_expediente('UBICACI&#211;N ACTUAL: <span class="x">Divisi&#243;n Resoluciones</span>')
        self.assertEqual(r, {"ubicacion_actual": "División Resoluciones", "ubicacion_fecha": None})

    def test_sin_seccion(self):
        self.assertEqual(self.il.ubicacion_expediente("<html>nada</html>"),
                         {"ubicacion_actual": None, "ubicacion_fecha": None})

    def test_viaja_con_datos_de_expediente(self):
        d = self.il.datos_de_expediente(HTML_UBICACION)
        self.assertEqual(d["ubicacion_actual"], "División Notificaciones")


class TestArchivarInformes(unittest.TestCase):
    def setUp(self):
        try:
            import informes_api
        except ImportError as e:
            self.skipTest(f"falta una dependencia del panel: {e}")
        self.ia = informes_api
        t = dt.datetime(2026, 11, 12, 10, 0)
        interno = {"caso": {"marca_pretendida": "X"}}
        self.filas = [
            {"id": 3, "tipo": "interno", "marca": "ALFA", "datos": interno, "generado_en": t, "cliente_id": 1, "cliente_nombre": "A",
             "archivado_en": None},
            {"id": 2, "tipo": "interno", "marca": "ALFA", "datos": interno, "generado_en": t - dt.timedelta(days=1), "cliente_id": 1,
             "cliente_nombre": "A", "archivado_en": t, "archivado_por": "Tomi"},
            {"id": 1, "tipo": "interno", "marca": "BETA", "datos": interno, "generado_en": t, "cliente_id": 1, "cliente_nombre": "A",
             "archivado_en": t, "archivado_por": "Pame"},
        ]

    def test_filtros(self):
        sin = self.ia.agrupar_informes(self.filas, archivados="")
        self.assertEqual([g["marca"] for g in sin], ["ALFA"])  # ALFA tiene un informe nuevo sin archivar: vuelve
        solo = self.ia.agrupar_informes(self.filas, archivados="solo")
        self.assertEqual([g["marca"] for g in solo], ["BETA"])
        self.assertEqual(solo[0]["archivado_por"], "Pame")
        todos = self.ia.agrupar_informes(self.filas)
        self.assertEqual(sorted(g["marca"] for g in todos), ["ALFA", "BETA"])
        self.assertEqual(sorted(next(g for g in todos if g["marca"] == "ALFA")["ids"]), [2, 3])


class TestNoContactar(unittest.TestCase):
    def setUp(self):
        try:
            import app
        except Exception as e:  # noqa: BLE001 — sin dependencias o sin base
            self.skipTest(f"no se pudo importar app.py: {e}")
        self.app = app

    def _where(self, **kw):
        base = {k: None for k in ("boletin", "clase", "es_lead", "contactado", "no_contactar", "tuvo_oposicion",
                                  "tipo_oposicion", "vista_pre", "notificacion", "fecha_desde", "fecha_hasta",
                                  "fecha_dias", "tiene_email", "tiene_titular", "tiene_clase", "estado_marca",
                                  "multiclase_min", "multiclase_a", "multiclase_b", "multiclase_titular", "q")}
        base.update(kw)
        return self.app._consulta_marcas(base)["where_sql"]

    def test_sin_contactar_deja_afuera_las_descartadas(self):
        self.assertIn("no_contactar_en IS NULL", self._where(contactado=False))
        self.assertNotIn("no_contactar_en", self._where(contactado=True))
        self.assertIn("no_contactar_en IS NOT NULL", self._where(no_contactar=True))


if __name__ == "__main__":
    unittest.main()
