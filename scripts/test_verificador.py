"""
Pruebas del verificador de marca de la web (panel/verificador_core.py), sin red ni base.
    cd scripts && python3 -m unittest test_verificador -v

INPI se simula con una sesión falsa: se prueba qué hace el verificador con cada tipo de
respuesta (bien, vacía, error, basura), que es lo que más importa — una lista vacía solo puede
significar «no hay marcas» si INPI contestó bien; si no, el veredicto tiene que ser «error».
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import verificador_core as vc  # noqa: E402


def fila(acta, denominacion, clase=25, estado="En trámite"):
    return {"acta": str(acta), "denominacion": denominacion, "clase": clase, "estado": estado}


class Evaluar(unittest.TestCase):
    def test_sin_filas_es_disponible(self):
        r = vc.evaluar("Zorzal Azul", [])
        self.assertEqual(r["verdict"], "disponible")
        self.assertEqual((r["exactCount"], r["similarCount"], r["samples"]), (0, 0, []))

    def test_marca_identica_es_no_disponible(self):
        r = vc.evaluar("Nike", [fila(1, "NIKE", 25), fila(2, "NIKE", 9)])
        self.assertEqual(r["verdict"], "no_disponible")
        self.assertEqual(r["exactCount"], 2)

    def test_acentos_y_mayusculas_no_cuentan(self):
        self.assertEqual(vc.evaluar("Café del Sol", [fila(1, "CAFE DEL SOL")])["verdict"], "no_disponible")

    def test_igual_sin_espacios_es_identica(self):
        self.assertEqual(vc.evaluar("Bali Stone", [fila(1, "BALISTONE")])["verdict"], "no_disponible")

    def test_suena_igual_es_similar_no_identica(self):
        r = vc.evaluar("Kasa", [fila(1, "CASA")])
        self.assertEqual(r["verdict"], "con_similares")
        self.assertEqual((r["exactCount"], r["similarCount"]), (0, 1))

    def test_contenida_es_similar(self):
        self.assertEqual(vc.evaluar("Smarties", [fila(1, "SMARTIES CONSULTORA")])["verdict"], "con_similares")

    def test_nada_parecido_es_disponible(self):
        self.assertEqual(vc.evaluar("Zorzal", [fila(1, "GORRION")])["verdict"], "disponible")

    def test_palabras_genericas_se_ignoran(self):
        sin = vc.evaluar("Pan Casero", [fila(1, "PAN CASERO SA")])
        con = vc.evaluar("Pan Casero", [fila(1, "PAN CASERO SA")], genericas=frozenset({"SA"}))
        self.assertEqual(sin["verdict"], "con_similares")
        self.assertEqual(con["verdict"], "no_disponible")

    def test_una_identica_entre_muchas_parecidas_manda(self):
        r = vc.evaluar("Luna", [fila(1, "LUNAS DEL SUR"), fila(2, "LUNA"), fila(3, "LUNA NUEVA")])
        self.assertEqual(r["verdict"], "no_disponible")
        self.assertEqual(r["muestras"][0]["denominacion"], "LUNA")  # la más parecida primero

    def test_muestras_publicas_solo_nombre_y_clase(self):
        r = vc.evaluar("Nike", [fila(77, "NIKE", "25", estado="Vigente")])
        self.assertEqual(r["samples"], [{"denominacion": "NIKE", "clase": 25}])
        self.assertEqual(r["muestras"][0]["acta"], "77")  # el equipo sí ve el acta y el estado
        self.assertEqual(r["muestras"][0]["estado"], "Vigente")

    def test_muestras_publicas_topeadas_y_sin_clase_desconocida(self):
        filas = [fila(i, f"NIKE {i}", 25) for i in range(30)] + [fila(99, "NIKE", None)]
        r = vc.evaluar("Nike", filas)
        self.assertLessEqual(len(r["samples"]), vc.MUESTRAS_PUBLICAS)
        self.assertTrue(all(isinstance(s["clase"], int) for s in r["samples"]))
        self.assertLessEqual(len(r["muestras"]), vc.MUESTRAS_INTERNAS)

    def test_posible_mas_cuando_inpi_llena_la_pagina(self):
        llena = [fila(i, f"SOL {i}") for i in range(vc.LIMITE_INPI)]
        self.assertTrue(vc.evaluar("Sol", llena)["posible_mas"])
        self.assertFalse(vc.evaluar("Sol", llena[:3])["posible_mas"])

    def test_filas_raras_no_rompen(self):
        r = vc.evaluar("Nike", [{}, {"denominacion": None}, {"denominacion": ""}])
        self.assertEqual(r["verdict"], "disponible")


class Validacion(unittest.TestCase):
    def datos(self, **kw):
        base = dict(marca="Mi Marca", actividad="Ropa", nombre="Ana", email="ANA@Correo.com", telefono="", web="")
        base.update(kw)
        return vc.validar_lead(**base)

    def test_datos_validos_se_limpian(self):
        d = self.datos(marca="  Mi   Marca\n", email="ANA@Correo.com ")
        self.assertEqual((d["marca"], d["email"]), ("Mi Marca", "ana@correo.com"))

    def test_errores_con_mensaje_para_la_persona(self):
        for campo, valor, texto in [("marca", "a", "marca"), ("marca", "!!!", "letras"), ("actividad", "  ", "dedica"),
                                    ("nombre", "", "llamás"), ("email", "ana@", "e-mail"), ("email", "ana @x.com", "e-mail")]:
            with self.subTest(campo=campo, valor=valor):
                with self.assertRaises(ValueError) as cm:
                    self.datos(**{campo: valor})
                self.assertIn(texto, str(cm.exception))

    def test_se_cortan_los_largos_y_los_caracteres_de_control(self):
        d = self.datos(marca="A" * 500, actividad="x\x00y\x1fz", web="w" * 999)
        self.assertEqual(len(d["marca"]), 80)
        self.assertEqual(d["actividad"], "x y z")
        self.assertEqual(len(d["web"]), 200)

    def test_adicional(self):
        d = vc.validar_adicional("¿Cuánto sale?", "Ana", "ana@x.com")
        self.assertEqual(d["pregunta"], "¿Cuánto sale?")
        with self.assertRaises(ValueError):
            vc.validar_adicional("hola", "Ana", "ana@x.com")
        with self.assertRaises(ValueError):
            vc.validar_adicional("una pregunta larga", "", "ana@x.com")

    def test_codigo_largo_y_unico(self):
        a, b = vc.nuevo_codigo(), vc.nuevo_codigo()
        self.assertNotEqual(a, b)
        self.assertTrue(vc.CODIGO_RE.match(a))
        self.assertFalse(vc.CODIGO_RE.match("../etc/passwd"))
        self.assertFalse(vc.CODIGO_RE.match("abc"))

    def test_clave_de_cache_ignora_espacios_acentos_y_mayusculas(self):
        self.assertEqual(vc.clave_cache("Bali Stone"), vc.clave_cache("BALISTONE"))
        self.assertEqual(vc.clave_cache("Café"), vc.clave_cache("cafe"))

    def test_variantes_de_busqueda(self):
        self.assertEqual(vc.variantes_de_busqueda("Bali Stone"), ["Bali Stone", "BaliStone"])
        self.assertEqual(vc.variantes_de_busqueda("Nike"), ["Nike"])


class Origenes(unittest.TestCase):
    def setUp(self):
        self._viejo = os.environ.pop("VERIFICADOR_ORIGENES", None)

    def tearDown(self):
        os.environ.pop("VERIFICADOR_ORIGENES", None)
        if self._viejo is not None:
            os.environ["VERIFICADOR_ORIGENES"] = self._viejo

    def test_por_defecto_solo_la_web_de_smarties(self):
        self.assertTrue(vc.origen_permitido("https://smartiesconsultora.com.ar"))
        self.assertTrue(vc.origen_permitido("https://www.smartiesconsultora.com.ar/pagina?x=1"))
        self.assertFalse(vc.origen_permitido("https://smartiesconsultora.com.ar.malo.com"))
        self.assertFalse(vc.origen_permitido("http://smartiesconsultora.com.ar"))  # sin https no
        self.assertFalse(vc.origen_permitido("https://otro.com"))
        self.assertFalse(vc.origen_permitido(""))
        self.assertFalse(vc.origen_permitido("null"))

    def test_se_puede_cambiar_por_variable(self):
        os.environ["VERIFICADOR_ORIGENES"] = "https://prueba.ejemplo.com/ , https://b.com"
        self.assertTrue(vc.origen_permitido("https://prueba.ejemplo.com"))
        self.assertFalse(vc.origen_permitido("https://smartiesconsultora.com.ar"))

    def test_solo_las_rutas_del_verificador_son_cruzadas(self):
        self.assertTrue(vc.es_ruta_cruzada("/api/publico/verificador/consulta"))
        self.assertTrue(vc.es_ruta_cruzada("/api/publico/verificador/abc123xyz"))
        for ruta in ["/api/marcas", "/api/login", "/api/publico/formulario/persona-fisica", "/api/consultas-web", "/"]:
            self.assertFalse(vc.es_ruta_cruzada(ruta), ruta)


class RespuestaFalsa:
    def __init__(self, status=200, datos=None, texto_invalido=False):
        self.status_code = status
        self._datos = datos
        self._invalido = texto_invalido

    def json(self):
        if self._invalido:
            raise ValueError("no es JSON")
        return self._datos


class SesionFalsa:
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.pedidos = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.pedidos.append(json)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class BuscarEnInpi(unittest.TestCase):
    def setUp(self):
        self._sleep = time.sleep
        time.sleep = lambda s: None  # sin esperas reales entre reintentos

    def tearDown(self):
        time.sleep = self._sleep

    def buscar(self, *respuestas):
        s = SesionFalsa(*respuestas)
        return vc._buscar_una(s, "https://inpi.test", "NIKE", 5), s

    def test_respuesta_buena(self):
        rows, s = self.buscar(RespuestaFalsa(200, {"rows": [{"Acta": 1}]}))
        self.assertEqual(rows, [{"Acta": 1}])
        self.assertEqual(s.pedidos[0]["Denominacion"], "NIKE")
        self.assertEqual(s.pedidos[0]["TipoBusquedaDenominacion"], "1")

    def test_sin_resultados_pero_inpi_contesto_bien(self):
        rows, _ = self.buscar(RespuestaFalsa(200, {"rows": []}))
        self.assertEqual(rows, [])

    def test_respuestas_malas_son_error_y_no_lista_vacia(self):
        malas = [
            RespuestaFalsa(500, {}),
            RespuestaFalsa(403, {}),
            RespuestaFalsa(200, texto_invalido=True),   # página de bloqueo (HTML)
            RespuestaFalsa(200, {"total": 0}),          # falta «rows»
            RespuestaFalsa(200, {"rows": None}),
            RespuestaFalsa(200, ["x"]),
        ]
        for mala in malas:
            with self.subTest(mala=vars(mala)):
                with self.assertRaises(vc.ErrorInpi):
                    self.buscar(mala, RespuestaFalsa(mala.status_code, mala._datos, mala._invalido))

    def test_reintenta_una_vez_y_se_recupera(self):
        rows, s = self.buscar(RespuestaFalsa(500, {}), RespuestaFalsa(200, {"rows": [{"Acta": 2}]}))
        self.assertEqual(rows, [{"Acta": 2}])
        self.assertEqual(len(s.pedidos), 2)

    def test_error_de_conexion_es_error_inpi(self):
        import requests
        with self.assertRaises(vc.ErrorInpi):
            self.buscar(requests.ConnectionError("x"), requests.Timeout("y"))


class TopeGlobal(unittest.TestCase):
    def test_pasado_el_tope_por_minuto_no_se_molesta_a_inpi(self):
        vc._ventana.clear()
        try:
            for _ in range(vc.PEDIDOS_INPI_POR_MINUTO):
                vc._reservar_pedido()
            with self.assertRaises(vc.ErrorInpi):
                vc._reservar_pedido()
        finally:
            vc._ventana.clear()


class Mails(unittest.TestCase):
    def consulta(self, **kw):
        base = {"marca": "Mi Marca", "veredicto": "con_similares", "actividad": "Ropa", "nombre": "Ana", "email": "ana@x.com",
                "telefono": "11 5555-5555", "web": "", "muestras": [{"denominacion": "MI MARCA PLUS", "clase": 25, "estado": "En trámite", "acta": "123"}]}
        base.update(kw)
        return base

    def test_aviso_con_los_datos(self):
        cuerpo, texto = vc.armar_mail_aviso(self.consulta(), "https://panel.ejemplo.com")
        for esperado in ["Mi Marca", "Ana", "ana@x.com", "11 5555-5555", "MI MARCA PLUS", "https://panel.ejemplo.com/consultas-web"]:
            self.assertIn(esperado, cuerpo)
        self.assertIn("Con marcas similares", texto)

    def test_nada_de_lo_que_escribe_la_persona_se_ejecuta_en_el_mail(self):
        mal = '<script>alert(1)</script>'
        cuerpo, _ = vc.armar_mail_aviso(self.consulta(marca=mal, nombre=mal, actividad=mal, web=mal, email='a"><b>@x.com'),
                                        "https://panel.ejemplo.com")
        self.assertNotIn("<script>", cuerpo)
        self.assertNotIn("<b>@x.com", cuerpo)
        cuerpo, _ = vc.armar_mail_adicional({"nombre": mal, "email": mal, "pregunta": mal}, mal, "abc", "https://p.com")
        self.assertNotIn("<script>", cuerpo)

    def test_los_asuntos_se_reconocen_como_consultas_web(self):
        import re
        rx = re.compile(r"^Consulta web", re.I)
        self.assertTrue(rx.search(vc.asunto_aviso("Mi Marca", "disponible")))
        self.assertTrue(rx.search(vc.asunto_adicional("Mi Marca")))


if __name__ == "__main__":
    unittest.main()
