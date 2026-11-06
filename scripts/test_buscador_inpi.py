"""
Pruebas del Buscador INPI (panel/buscador_inpi.py) sin pedirle nada a INPI: las respuestas de las
grillas se simulan con el formato real del portal (visto el 06/11/2026).

    cd scripts && python3 -m unittest test_buscador_inpi
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))

try:
    import buscador_inpi as bi  # noqa: E402
except ImportError as e:   # faltan dependencias del panel (bs4, pdfplumber)
    bi = None
    _MOTIVO = str(e)

FILA_APEX = {"Acta": 4753802, "Titulares": "30711122989    XANTANA SRL 100.00%", "Fecha_Ingreso": "/Date(1784832923000)/",
             "Clase": 5, "Denominacion": "APEX PADEL", "Tipo_Marca": "Denominativa", "Numero_Resolucion": "3955971",
             "Estado": "C", "Fecha_Vencimiento": "/Date(2105665200000)/"}
FILA_CINE = {"Acta": 4809668, "Titulares": "23340220609    HERRERA BERNARDI EMILIANO MARTÍN 100.00%",
             "Fecha_Ingreso": "/Date(1793828918000)/", "Clase": 41, "Denominacion": "CINE CATÁSTROFE",
             "Tipo_Marca": "Mixta", "Numero_Resolucion": None, "Estado": None,
             "Fecha_Vencimiento": "/Date(-62135586000000)/"}


@unittest.skipIf(bi is None, "faltan dependencias del panel")
class Texto(unittest.TestCase):
    def test_comparable_y_patron(self):
        self.assertEqual(bi.comparable("Cine  Catástrofe!"), "CINE CATASTROFE")
        self.assertEqual(bi.patron_sin_tildes("cine catastrofe"), "C_N_ C_T_STR_F_")
        self.assertEqual(bi.patron_sin_tildes("ÑANDÚ"), "Ñ_ND_")

    def test_fechas(self):
        self.assertEqual(bi._fecha_dotnet("/Date(1790701465000)/"), "2026-09-29")
        self.assertIsNone(bi._fecha_dotnet("/Date(-62135586000000)/"))
        self.assertIsNone(bi._fecha_dotnet(""))
        self.assertEqual(bi.fecha_ddmmaaaa("2026-11-05"), "05/11/2026")
        self.assertEqual(bi.fecha_ddmmaaaa("05/11/2026"), "05/11/2026")
        self.assertEqual(bi.fecha_ddmmaaaa(""), "")
        with self.assertRaises(bi.BuscadorError):
            bi.fecha_ddmmaaaa("5 de noviembre")

    def test_fila_marca(self):
        f = bi.fila_marca(FILA_APEX)
        self.assertEqual((f["acta"], f["cuit"], f["titulares"], f["estado"], f["clase"]),
                         ("4753802", "30711122989", "XANTANA SRL 100.00%", "Concedida", 5))
        self.assertEqual(f["vencimiento"], "2036-09-22")
        g = bi.fila_marca(FILA_CINE)
        self.assertIsNone(g["estado"]); self.assertIsNone(g["vencimiento"])
        self.assertEqual(bi.estado_legible("Concedida"), "Concedida")
        self.assertEqual(bi.estado_legible("NU"), "Nula")

    def test_varios_titulares(self):
        t = "20111111111  PEREZ JUAN 50.00% 27222222222 GOMEZ ANA 50.00%"
        self.assertEqual(bi._titulares_limpios(t), "PEREZ JUAN 50.00% · GOMEZ ANA 50.00%")


@unittest.skipIf(bi is None, "faltan dependencias del panel")
class Busquedas(unittest.TestCase):
    def setUp(self):
        self.pedidos = []
        self._orig = (bi._sesion, bi._pedir_grilla)
        bi._sesion = lambda pagina=None: object()

    def tearDown(self):
        bi._sesion, bi._pedir_grilla = self._orig

    def simular(self, respuestas):
        def pedir(s, ruta, cuerpo):
            self.pedidos.append((ruta, cuerpo))
            return respuestas.pop(0)
        bi._pedir_grilla = pedir

    def test_avanzada_con_tilde_reintenta_con_comodines_y_filtra(self):
        otra = dict(FILA_CINE, Acta=1, Denominacion="CANE CATASTRAFE")   # entra por los «_» pero no coincide
        self.simular([{"total": 0, "rows": []}, {"total": 2, "rows": [FILA_CINE, otra]}])
        r = bi.buscar_avanzada({"denominacion": "cine catastrofe"})
        self.assertEqual(self.pedidos[0][1]["Denominacion"], "cine catastrofe")
        self.assertEqual(self.pedidos[1][1]["Denominacion"], "C_N_ C_T_STR_F_")
        self.assertEqual(self.pedidos[1][1]["limit"], bi.TOPE_SIN_TILDES)
        self.assertTrue(r["sin_tildes"])
        self.assertEqual([f["acta"] for f in r["filas"]], ["4809668"])

    def test_avanzada_arma_el_cuerpo_como_el_portal(self):
        self.simular([{"total": 1, "rows": [FILA_APEX]}])
        r = bi.buscar_avanzada({"denominacion": "APEX", "denominacion_empieza": True, "clase": "5",
                                "tipo_resolucion": "C", "vigentes": True, "ingreso_desde": "2026-01-01", "limit": 20})
        c = self.pedidos[0][1]
        self.assertEqual((c["TipoBusquedaDenominacion"], c["Clase"], c["Tipo_Resolucion"], c["vigentes"],
                          c["Fecha_IngresoDesde"], c["limit"], c["TipoBusquedaTitular"]),
                         ("0", "5", "C", True, "01/01/2026", 20, "1"))
        self.assertFalse(r["sin_tildes"])
        self.assertEqual(len(self.pedidos), 1)

    def test_avanzada_validaciones(self):
        for p in ({"denominacion": "a"}, {"denominacion": "APEX", "clase": "50"}, {"denominacion": "APEX", "tipo_resolucion": "X"}):
            with self.assertRaises(bi.BuscadorError, msg=str(p)):
                bi.buscar_avanzada(p)
        self.assertEqual(self.pedidos, [])

    def test_rapido_acta_y_resolucion(self):
        self.simular([{"total": 0, "rows": []}, {"total": 1, "rows": [FILA_APEX]}])
        r = bi.buscar_rapido("3955971")
        self.assertEqual(self.pedidos[0][1], {"Acta": "3955971", "Resolucion": ""})
        self.assertEqual(self.pedidos[1][1], {"Acta": "", "Resolucion": "3955971"})
        self.assertTrue(r["por_resolucion"])
        self.assertEqual(r["filas"][0]["acta"], "4753802")

    def test_rapido_cuit_usa_el_webservice(self):
        orig = bi.inpi_lead.consultar_titular_ws
        bi.inpi_lead.consultar_titular_ws = lambda cuit="", titular="", **k: [
            {"Acta": "4797414", "Titulares": "MARIANO NICOLAS SCARANO 100.00%", "Fecha_Ingreso": "2026-09-29T14:04:25",
             "Clase": "5", "Denominacion": "APEX LAB", "Tipo_Marca": "Denominativa"}] if cuit == "20304944545" else []
        try:
            r = bi.buscar_rapido("20-30494454-5")
        finally:
            bi.inpi_lead.consultar_titular_ws = orig
        self.assertEqual(r["modo"], "cuit")
        self.assertEqual((r["filas"][0]["acta"], r["filas"][0]["clase"], r["filas"][0]["fecha_ingreso"]), ("4797414", 5, "2026-09-29"))

    def test_rapido_texto_es_denominacion(self):
        self.simular([{"total": 1, "rows": [FILA_APEX]}])
        r = bi.buscar_rapido("apex padel")
        self.assertEqual(r["modo"], "avanzada")
        self.assertEqual(self.pedidos[0][0], "/MarcasConsultas/GrillaMarcasAvanzada")

    def test_renovaciones(self):
        self.simular([{"total": 367, "rows": [{"Acta": 3559533, "Fecha_Presentacion": "/Date(1793828918000)/",
                                               "Nro_Clase": 10, "Nro_Resolucion": 2897070, "Renovadora": 4822142}]}])
        r = bi.buscar_renovaciones("2026-11-01", "2026-11-05", 10, 0)
        self.assertEqual(self.pedidos[0][1]["FechaPresentacionDesde"], "01/11/2026")
        self.assertEqual((r["total"], r["filas"][0]["renovadora"], r["filas"][0]["numero_resolucion"]), (367, "4822142", "2897070"))
        with self.assertRaises(bi.BuscadorError):
            bi.buscar_renovaciones("", "2026-11-05")


if __name__ == "__main__":
    unittest.main()
