"""
Pruebas de scripts/feriados.py, sin red ni base. Correr con:
    cd scripts && python3 -m unittest test_feriados -v

El JSON de ejemplo copia la estructura que lee la página de argentina.gob.ar
(national-holidays.js: mainEntity.itemListElement[].item con name, startDate y
additionalProperty.value); los nombres y fechas son de ejemplo.
"""

import copy
import unittest

import feriados as fe


def item(fecha, nombre, tipo):
    return {"@type": "ListItem", "item": {"name": nombre, "startDate": fecha, "additionalProperty": {"name": "type", "value": tipo}}}


FERIADOS_2026 = [
    ("2026-01-01", "Año Nuevo", "inamovible"), ("2026-02-16", "Carnaval", "inamovible"), ("2026-02-17", "Carnaval", "inamovible"),
    ("2026-03-24", "Día Nacional de la Memoria", "inamovible"), ("2026-04-02", "Día del Veterano", "inamovible"),
    ("2026-04-03", "Viernes Santo", "inamovible"), ("2026-05-01", "Día del Trabajador", "inamovible"),
    ("2026-05-25", "Revolución de Mayo", "inamovible"), ("2026-06-15", "Paso a la Inmortalidad de Güemes", "trasladable"),
    ("2026-07-09", "Día de la Independencia", "inamovible"), ("2026-07-10", "Día no laborable con fines turísticos", "turistico"),
    ("2026-10-12", "Día de la Diversidad Cultural", "trasladable"), ("2026-12-08", "Inmaculada Concepción", "inamovible"),
    ("2026-12-25", "Navidad", "inamovible"), ("2026-04-01", "Día no laborable", "no_laborable"),
]


def json_de(feriados=FERIADOS_2026, **extra):
    return {"inLanguage": "es-AR", "mainEntity": {"itemListElement": [item(*f) for f in feriados]}, **extra}


class Parseo(unittest.TestCase):
    def test_lee_fecha_nombre_y_tipo_ordenado_por_fecha(self):
        r = fe.parsear_feriados(json_de(), 2026)
        self.assertEqual(len(r), 15)
        self.assertEqual(r[0], {"fecha": "2026-01-01", "motivo": "Año Nuevo", "tipo": "inamovible"})
        self.assertEqual([x["fecha"] for x in r], sorted(x["fecha"] for x in r))

    def test_acepta_fecha_con_hora_y_tipo_en_mayusculas(self):
        datos = json_de()
        datos["mainEntity"]["itemListElement"][0] = item("2026-01-01T00:00:00-03:00", "  Año   Nuevo ", "Inamovible")
        r = fe.parsear_feriados(datos, 2026)
        self.assertEqual(r[0], {"fecha": "2026-01-01", "motivo": "Año Nuevo", "tipo": "inamovible"})

    def test_dos_entradas_el_mismo_dia_manda_la_que_descuenta(self):
        datos = json_de(FERIADOS_2026 + [("2026-07-10", "Feriado puente", "especial")])
        r = {x["fecha"]: x for x in fe.parsear_feriados(datos, 2026)}
        self.assertEqual(r["2026-07-10"]["tipo"], "especial")
        self.assertIn("Feriado puente", r["2026-07-10"]["motivo"])

    def test_si_cambia_el_formato_falla_en_voz_alta(self):
        for roto in ({}, {"mainEntity": {}}, {"mainEntity": {"itemListElement": []}}, [], {"mainEntity": {"itemListElement": [{"x": 1}]}}):
            with self.assertRaises(ValueError):
                fe.parsear_feriados(roto, 2026)

    def test_idioma_distinto(self):
        with self.assertRaises(ValueError):
            fe.parsear_feriados(json_de(inLanguage="en-US"), 2026)

    def test_el_archivo_del_anio_trae_el_anio_nuevo_siguiente(self):
        # caso real (27/10/2026): el JSON de 2026 incluye el 01/01/2027
        r = fe.parsear_feriados(json_de(FERIADOS_2026 + [("2027-01-01", "Año nuevo", "inamovible")]), 2026)
        self.assertEqual(r[-1], {"fecha": "2027-01-01", "motivo": "Año nuevo", "tipo": "inamovible"})

    def test_fecha_de_un_anio_lejano(self):
        with self.assertRaises(ValueError):
            fe.parsear_feriados(json_de(FERIADOS_2026 + [("2030-01-01", "Año Nuevo", "inamovible")]), 2026)

    def test_fecha_invalida_o_sin_tipo(self):
        with self.assertRaises(ValueError):
            fe.parsear_feriados(json_de(FERIADOS_2026 + [("26/10/2026", "X", "inamovible")]), 2026)
        with self.assertRaises(ValueError):
            fe.parsear_feriados(json_de(FERIADOS_2026 + [("2026-11-23", "X", "")]), 2026)

    def test_muy_pocos_feriados_es_un_error(self):
        with self.assertRaises(ValueError):
            fe.parsear_feriados(json_de(FERIADOS_2026[:3]), 2026)
        # los de otro año no cuentan para el mínimo
        with self.assertRaises(ValueError):
            fe.parsear_feriados(json_de(FERIADOS_2026[:5] + [(f"2027-0{m}-01", "X", "inamovible") for m in range(1, 9)]), 2026)

    def test_solo_algunos_tipos_descuentan_dias_habiles(self):
        self.assertEqual(set(fe.TIPOS_INHABILES), {"inamovible", "trasladable", "especial"})
        self.assertNotIn("turistico", fe.TIPOS_INHABILES)
        self.assertNotIn("no_laborable", fe.TIPOS_INHABILES)


class CursorFalso:
    """Responde el SELECT de sincronizar() con `filas` y anota lo demás."""

    def __init__(self, filas):
        self.filas, self.sql = filas, []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.filas

    def de(self, verbo):
        return [p for s, p in self.sql if s.startswith(verbo)]


class Sincronizacion(unittest.TestCase):
    def setUp(self):
        self.pagina = fe.parsear_feriados(json_de(), 2026)
        self.guardados = [(f["fecha"], f["motivo"], f["tipo"], True) for f in self.pagina]

    def test_sin_cambios_solo_marca_que_se_vio(self):
        cur = CursorFalso(self.guardados)
        self.assertEqual(fe.sincronizar(cur, 2026, self.pagina), [])
        self.assertEqual(len(cur.de("UPDATE feriados SET visto_en")), 15)
        self.assertEqual(cur.de("INSERT"), [])

    def test_el_anio_nuevo_siguiente_no_se_duplica_ni_se_quita(self):
        con_2027 = fe.parsear_feriados(json_de(FERIADOS_2026 + [("2027-01-01", "Año nuevo", "inamovible")]), 2026)
        guardados = self.guardados + [("2027-01-01", "Año nuevo", "inamovible", True)]
        cur = CursorFalso(guardados)
        self.assertEqual(fe.sincronizar(cur, 2026, con_2027), [])
        self.assertEqual(cur.de("INSERT"), [])
        # y si después el archivo 2026 ya no lo trae, no se da de baja desde acá (lo maneja el archivo 2027)
        cur = CursorFalso(guardados)
        self.assertEqual(fe.sincronizar(cur, 2026, self.pagina), [])
        self.assertFalse(any("activo = false" in s for s, _ in cur.sql))

    def test_primera_vez_agrega_todo(self):
        cur = CursorFalso([])
        cambios = fe.sincronizar(cur, 2026, self.pagina)
        self.assertEqual(len(cambios), 15)
        self.assertTrue(all(c.startswith("AGREGADO") for c in cambios))
        self.assertEqual(len(cur.de("INSERT")), 15)

    def test_feriado_nuevo(self):
        pagina = fe.parsear_feriados(json_de(FERIADOS_2026 + [("2026-11-23", "Día de la Soberanía", "trasladable")]), 2026)
        cur = CursorFalso(self.guardados)
        cambios = fe.sincronizar(cur, 2026, pagina)
        self.assertEqual(cambios, ["AGREGADO 2026-11-23 Día de la Soberanía (trasladable)"])

    def test_feriado_trasladado_se_ve_como_uno_quitado_y_uno_agregado(self):
        sin = [f for f in FERIADOS_2026 if f[0] != "2026-06-15"] + [("2026-06-17", "Paso a la Inmortalidad de Güemes", "trasladable")]
        cur = CursorFalso(self.guardados)
        cambios = fe.sincronizar(cur, 2026, fe.parsear_feriados(json_de(sin), 2026))
        self.assertEqual(len(cambios), 2)
        self.assertTrue(any(c.startswith("AGREGADO 2026-06-17") for c in cambios))
        self.assertTrue(any(c.startswith("QUITADO 2026-06-15") for c in cambios))
        # el quitado no se borra: queda inactivo
        self.assertEqual(cur.de("DELETE"), [])
        self.assertTrue(any("activo = false" in s for s, _ in cur.sql))

    def test_cambio_de_tipo(self):
        cambiado = [("2026-07-10", n, "especial") if f == "2026-07-10" else (f, n, t) for f, n, t in FERIADOS_2026]
        cur = CursorFalso(self.guardados)
        cambios = fe.sincronizar(cur, 2026, fe.parsear_feriados(json_de(cambiado), 2026))
        self.assertEqual(len(cambios), 1)
        self.assertTrue(cambios[0].startswith("MODIFICADO 2026-07-10"))
        self.assertIn("antes:", cambios[0])

    def test_feriado_que_vuelve_a_aparecer_se_reactiva(self):
        guardados = [(f, m, t, a and f != "2026-12-08") for f, m, t, a in self.guardados]
        cambios = fe.sincronizar(CursorFalso(guardados), 2026, self.pagina)
        self.assertEqual(len(cambios), 1)
        self.assertTrue(cambios[0].startswith("MODIFICADO 2026-12-08"))

    def test_un_feriado_ya_inactivo_que_sigue_ausente_no_vuelve_a_avisarse(self):
        guardados = self.guardados + [("2026-03-23", "Puente", "turistico", False)]
        self.assertEqual(fe.sincronizar(CursorFalso(guardados), 2026, self.pagina), [])


class Descarga(unittest.TestCase):
    class Resp:
        def __init__(self, codigo, cuerpo=None):
            self.status_code, self.cuerpo = codigo, cuerpo

        def raise_for_status(self):
            if self.status_code >= 400:
                import requests
                raise requests.HTTPError(f"HTTP {self.status_code}")

        def json(self):
            return self.cuerpo

    class Sesion:
        def __init__(self, respuestas):
            self.respuestas, self.urls = list(respuestas), []

        def get(self, url, **kw):
            self.urls.append(url)
            return self.respuestas.pop(0)

    def test_pide_el_json_del_anio(self):
        s = self.Sesion([self.Resp(200, {"ok": 1})])
        self.assertEqual(fe.descargar(2026, s), {"ok": 1})
        self.assertEqual(s.urls, ["https://www.argentina.gob.ar/sites/default/files/holidays-2026-es.json"])

    def test_404_es_que_todavia_no_lo_publicaron(self):
        self.assertIsNone(fe.descargar(2027, self.Sesion([self.Resp(404)])))

    def test_reintenta_y_despues_falla(self):
        import time
        espera, time.sleep = time.sleep, lambda s: None
        try:
            s = self.Sesion([self.Resp(500), self.Resp(502), self.Resp(503)])
            with self.assertRaises(RuntimeError):
                fe.descargar(2026, s)
            self.assertEqual(len(s.urls), 3)
            s = self.Sesion([self.Resp(500), self.Resp(200, {"ok": 1})])
            self.assertEqual(fe.descargar(2026, s), {"ok": 1})
        finally:
            time.sleep = espera


if __name__ == "__main__":
    unittest.main()
