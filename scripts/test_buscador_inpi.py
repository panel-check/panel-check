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
        orig = bi.inpi_lead.consultar_ws
        llamadas = []

        def ws(op, campos, **k):
            llamadas.append((op, campos))
            return {"filas": [{"Acta": "4797414", "Titulares": "MARIANO NICOLAS SCARANO 100.00%",
                               "Fecha_Ingreso": "2026-09-29T14:04:25", "Clase": "5", "Denominacion": "APEX LAB",
                               "Tipo_Marca": "Denominativa"}], "total": None, "estado": None}
        bi.inpi_lead.consultar_ws = ws
        try:
            r = bi.buscar_rapido("20-30494454-5")
        finally:
            bi.inpi_lead.consultar_ws = orig
        self.assertEqual(llamadas, [("ConsultaCuitOTitular", {"cuit": "20304944545", "titular": ""})])
        self.assertEqual(r["modo"], "cuit")
        self.assertEqual((r["filas"][0]["acta"], r["filas"][0]["clase"], r["filas"][0]["fecha_ingreso"]), ("4797414", 5, "2026-09-29"))

    def test_cuit_muestra_el_error_del_webservice(self):
        orig = bi.inpi_lead.consultar_ws

        def ws(op, campos, **k):
            raise bi.inpi_lead.ErrorWs("INPI rechazó la consulta: Debe indicar un token de integrador")
        bi.inpi_lead.consultar_ws = ws
        try:
            with self.assertRaisesRegex(bi.BuscadorError, "token de integrador"):
                bi.buscar_cuit("20304944545")
        finally:
            bi.inpi_lead.consultar_ws = orig

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


WS_FILAS = [
    {"Acta": "4809668", "Titulares": "23340220609 HERRERA BERNARDI EMILIANO MARTÍN 100.00%", "Fecha_Ingreso": "2026-10-14T10:00:00",
     "Clase": "41", "Denominacion": "CINE CATÁSTROFE", "Tipo_Marca": "Mixta", "Numero_Resolucion": "", "Estado": ""},
    {"Acta": "2987616", "Titulares": "30521278818 CONSEJO PROFESIONAL 100.00%", "Fecha_Ingreso": "2010-03-18T15:02:11",
     "Clase": "44", "Denominacion": "VETERINARIOS EN CATÁSTROFES.", "Tipo_Marca": "Denominativa",
     "Numero_Resolucion": "2955869", "Estado": "C"},
]


@unittest.skipIf(bi is None, "faltan dependencias del panel")
class ConWebservice(unittest.TestCase):
    """Con INPI_WS_TOKEN, denominación y titular van por el webservice y el resto se filtra en el panel."""

    def setUp(self):
        self.orig = (bi.inpi_lead.token_ws, bi.inpi_lead.consultar_denominacion_ws, bi.inpi_lead.consultar_ws, bi._sesion)
        bi.inpi_lead.token_ws = lambda: "tok"
        self.pedidos = []

        def den(texto, **k):
            self.pedidos.append(("den", texto))
            return {"filas": list(WS_FILAS), "total": 2, "estado": None}
        bi.inpi_lead.consultar_denominacion_ws = den

        def portal(pagina=None):
            raise AssertionError("no tendría que usar el portal")
        bi._sesion = portal

    def tearDown(self):
        bi.inpi_lead.token_ws, bi.inpi_lead.consultar_denominacion_ws, bi.inpi_lead.consultar_ws, bi._sesion = self.orig

    def test_filtra_clase_y_estado_sin_tildes(self):
        r = bi.buscar_avanzada({"denominacion": "catastrofe"})
        self.assertEqual(r["fuente"], "webservice")
        self.assertEqual([f["acta"] for f in r["filas"]], ["4809668", "2987616"])   # más nuevas primero
        self.assertEqual(bi.buscar_avanzada({"denominacion": "catastrofe", "clase": "44"})["total"], 1)
        c = bi.buscar_avanzada({"denominacion": "catastrofe", "tipo_resolucion": "C"})
        self.assertEqual([f["estado"] for f in c["filas"]], ["Concedida"])
        e = bi.buscar_avanzada({"denominacion": "catastrofe", "tipo_resolucion": "E"})
        self.assertEqual([f["acta"] for f in e["filas"]], ["4809668"])
        self.assertEqual(bi.buscar_avanzada({"denominacion": "cine", "denominacion_empieza": True})["total"], 1)

    def test_si_el_webservice_no_trae_nada_reintenta_con_comodines(self):
        llamadas = []

        def den(texto, **k):
            llamadas.append(texto)
            return {"filas": list(WS_FILAS) if "_" in texto else [], "total": None, "estado": None}
        bi.inpi_lead.consultar_denominacion_ws = den
        r = bi.buscar_avanzada({"denominacion": "cine catastrofe"})
        self.assertEqual(llamadas, ["cine catastrofe", "C_N_ C_T_STR_F_"])
        self.assertTrue(r["sin_tildes"])
        self.assertEqual([f["acta"] for f in r["filas"]], ["4809668"])

    def test_solo_titular_usa_consulta_por_nombre(self):
        llamadas = []

        def ws(op, campos, **k):
            llamadas.append((op, campos))
            return {"filas": list(WS_FILAS), "total": None, "estado": None}
        bi.inpi_lead.consultar_ws = ws
        r = bi.buscar_avanzada({"titular": "herrera"})
        self.assertEqual(llamadas, [("ConsultaCuitOTitular", {"cuit": "", "titular": "herrera"})])
        self.assertEqual([f["acta"] for f in r["filas"]], ["4809668"])

    def test_con_fechas_o_vigentes_va_por_el_portal(self):
        usados = []
        bi._sesion = lambda pagina=None: usados.append("portal") or object()
        orig = bi._pedir_grilla
        bi._pedir_grilla = lambda s, ruta, cuerpo: {"total": 0, "rows": []}
        try:
            bi.buscar_avanzada({"denominacion": "zzz", "vigentes": True})
            bi.buscar_avanzada({"denominacion": "zzz", "ingreso_desde": "2026-01-01"})
        finally:
            bi._pedir_grilla = orig
        self.assertEqual(usados, ["portal", "portal"])

    def test_si_el_webservice_falla_usa_el_portal(self):
        def den(texto, **k):
            raise bi.inpi_lead.ErrorWs("no respondió")
        bi.inpi_lead.consultar_denominacion_ws = den
        bi._sesion = lambda pagina=None: object()
        orig = bi._pedir_grilla
        bi._pedir_grilla = lambda s, ruta, cuerpo: {"total": 1, "rows": [FILA_APEX]}
        try:
            r = bi.buscar_avanzada({"denominacion": "apex"})
        finally:
            bi._pedir_grilla = orig
        self.assertEqual((r["fuente"], r["total"]), ("portal", 1))


@unittest.skipIf(bi is None, "faltan dependencias del panel")
class ClienteWebservice(unittest.TestCase):
    """inpi_lead.consultar_ws: manda el token y lee filas, total y errores de INPI."""

    class _Resp:
        def __init__(self, texto, status=200):
            self.text, self.status_code = texto, status

    def setUp(self):
        self.orig_post = bi.inpi_lead.requests.post
        self.env = os.environ.get("INPI_WS_TOKEN")
        os.environ["INPI_WS_TOKEN"] = "tok<1>"

    def tearDown(self):
        bi.inpi_lead.requests.post = self.orig_post
        if self.env is None:
            os.environ.pop("INPI_WS_TOKEN", None)
        else:
            os.environ["INPI_WS_TOKEN"] = self.env

    def test_lee_filas_y_manda_el_token(self):
        enviado = {}

        def post(url, data=None, headers=None, timeout=None):
            enviado.update(cuerpo=data.decode(), accion=headers["SOAPAction"])
            return self._Resp('<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
                              '<ConsultaDenominacionResponse xmlns="http://tempuri.org/"><ConsultaDenominacionResult><total>1</total>'
                              '<estado>Disponible</estado><rows><GrillaMarcas><Acta>4809668</Acta><Denominacion>CINE CATÁSTROFE</Denominacion>'
                              '<Clase>41</Clase><Estado>C</Estado></GrillaMarcas></rows></ConsultaDenominacionResult>'
                              '</ConsultaDenominacionResponse></soap:Body></soap:Envelope>')
        bi.inpi_lead.requests.post = post
        r = bi.inpi_lead.consultar_denominacion_ws("CINE & CO")
        self.assertIn("<Denominacion>CINE &amp; CO</Denominacion>", enviado["cuerpo"])
        self.assertIn("<tokenIntegrador>tok&lt;1&gt;</tokenIntegrador>", enviado["cuerpo"])
        self.assertEqual(enviado["accion"], '"http://tempuri.org/ConsultaDenominacion"')
        self.assertEqual((r["total"], r["estado"], r["filas"][0]["Acta"], r["filas"][0]["Denominacion"]),
                         (1, "Disponible", "4809668", "CINE CATÁSTROFE"))

    def test_error_de_inpi_y_falta_de_token(self):
        bi.inpi_lead.requests.post = lambda *a, **k: self._Resp(
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><soap:Fault><faultcode>soap:Client</faultcode>'
            '<faultstring>Debe indicar un token de integrador para operar el método [ConsultaDenominacion].</faultstring>'
            '</soap:Fault></soap:Body></soap:Envelope>', 500)
        with self.assertRaisesRegex(bi.inpi_lead.ErrorWs, "token de integrador"):
            bi.inpi_lead.consultar_denominacion_ws("X")
        self.assertEqual(bi.inpi_lead.consultar_titular_ws(cuit="1"), [])   # la función vieja sigue devolviendo lista
        os.environ["INPI_WS_TOKEN"] = ""
        with self.assertRaisesRegex(bi.inpi_lead.ErrorWs, "INPI_WS_TOKEN"):
            bi.inpi_lead.consultar_ws("ConsultaCuitOTitular", {"cuit": "1"})


if __name__ == "__main__":
    unittest.main()
