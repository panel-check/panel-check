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


class Confirmacion(unittest.TestCase):
    """Mail que recibe la persona al completar el formulario (plantilla «consulta_web_confirmacion»)."""

    def fila(self, **kw):
        base = {"marca": "Todo Tenis Inclusivo", "nombre": "TOMAS PEREZ", "veredicto": "disponible", "codigo": "V5enrbdPdLad",
                "email": "tomas@x.com"}
        base.update(kw)
        return base

    def render(self, v):
        import mails_core as mc
        cat = mc.CATALOGO[vc.CLAVE_MAIL_CONFIRMACION]
        return mc.render_prospecto(cat["asunto"], cat["cuerpo"], vc.datos_confirmacion(v), cat["encabezado"], simple=mc.es_simple(vc.CLAVE_MAIL_CONFIRMACION))

    def test_la_plantilla_es_valida_y_no_va_a_los_leads(self):
        import mails_core as mc
        cat = mc.CATALOGO[vc.CLAVE_MAIL_CONFIRMACION]
        self.assertEqual(cat["grupo"], "web")           # no aparece entre las plantillas para mandar a leads
        self.assertTrue(cat["editable"])
        mc.validar_config(vc.CLAVE_MAIL_CONFIRMACION, {"cuenta": cat["cuenta"], "remitente": cat["remitente"], "responder_a": cat["responder_a"],
                                                      "asunto": cat["asunto"], "cuerpo": cat["cuerpo"], "encabezado": cat["encabezado"]})

    def test_mail_con_el_resultado_el_link_y_el_analisis_sin_cargo(self):
        asunto, html_, texto = self.render(self.fila())
        self.assertIn("Todo Tenis Inclusivo", asunto)
        for esperado in ["Hola Tomas:", "sin cargo", "en los próximos días", "https://smartiesconsultora.com.ar/resultado/?c=V5enrbdPdLad",
                         "no encontramos en INPI marcas con un nombre igual o parecido"]:
            self.assertIn(esperado, html_.replace("<strong>", "").replace("</strong>", ""))
        self.assertIn("sin cargo", texto)
        self.assertNotIn("{{", html_ + texto + asunto)

    def test_cada_resultado_tiene_su_frase(self):
        for ver in ("disponible", "con_similares", "no_disponible", "error", "pendiente", None):
            self.assertTrue(vc.frase_resultado(ver))
        self.assertNotEqual(vc.frase_resultado("disponible"), vc.frase_resultado("no_disponible"))
        self.assertIn("a mano", vc.frase_resultado("error"))

    def test_nombre_de_pila(self):
        self.assertEqual(vc.nombre_de_pila("Tomás Pérez"), "Tomás")
        self.assertEqual(vc.nombre_de_pila("TOMAS"), "Tomas")
        self.assertEqual(vc.nombre_de_pila("ana maría"), "Ana")
        self.assertEqual(vc.nombre_de_pila(""), "")
        self.assertEqual(vc.nombre_de_pila("1234 abc"), "")

    def test_sin_nombre_el_saludo_sale_sin_coma(self):
        _, html_, _ = self.render(self.fila(nombre=""))
        self.assertIn("Hola:", html_)

    def test_nada_de_lo_que_escribe_la_persona_se_ejecuta(self):
        mal = "<script>alert(1)</script>"
        asunto, html_, texto = self.render(self.fila(marca=mal, nombre=mal))
        self.assertNotIn("<script>", html_)

    def test_el_asunto_se_reconoce_en_enviados(self):
        import mails_core as mc
        asunto, _, _ = self.render(self.fila())
        self.assertEqual(mc.tipo_por_asunto(asunto, "prospectos"), vc.CLAVE_MAIL_CONFIRMACION)

    def test_datos_de_ejemplo_solo_para_el_grupo_web(self):
        import mails_core as mc
        d = mc.datos_de_ejemplo_web(vc.CLAVE_MAIL_CONFIRMACION, {"marca": "Real", "nombre": "Ana"})
        self.assertEqual(d["marca"], "Real")
        self.assertTrue(d["codigo"] and d["resultado"])
        self.assertIsNone(mc.datos_de_ejemplo_web("prospecto_oposicion", None))


class MailOposicion(unittest.TestCase):
    """Mail de oposición a un lead: botón principal (WhatsApp) + botón secundario «Más información» a /oposicion/."""

    def render(self, **extra):
        import mails_core as mc
        cat = mc.CATALOGO["prospecto_oposicion"]
        datos = {"titular": "PEREZ JORGE", "marca": "CONTAFACIL PRO", "codigo": "AbC123xyz_-9", **extra}
        return mc.render_prospecto(cat["asunto"], cat["cuerpo"], datos, cat["encabezado"])

    def test_tiene_el_boton_de_mas_informacion_con_el_codigo_del_envio(self):
        _, html_, texto = self.render()
        self.assertIn("https://smartiesconsultora.com.ar/oposicion/?e=AbC123xyz_-9", html_)
        self.assertIn("Más información sobre la oposición", html_)
        self.assertIn("https://smartiesconsultora.com.ar/oposicion/?e=AbC123xyz_-9", texto)
        self.assertNotIn("~", texto)
        self.assertNotIn(">~", html_)

    def test_el_secundario_tiene_borde_y_el_principal_relleno(self):
        _, html_, _ = self.render()
        self.assertEqual(html_.count("border:2px solid #133465"), 1)           # solo el secundario
        self.assertIn("wa.me/5491155890784", html_)                            # el principal sigue estando
        principal = html_.index("Quiero asesorarme sobre la oposición")
        secundario = html_.index("Más información sobre la oposición")
        self.assertLess(principal, secundario)                                  # WhatsApp primero

    def test_los_otros_botones_no_cambian(self):
        import mails_core as mc
        _, html_, _ = mc.render_prospecto("x", "[Ir](https://a.com)", {}, "T")
        self.assertIn("background:#133465", html_)
        self.assertNotIn("border:2px solid", html_)

    def test_la_vista_previa_completa_el_codigo_de_ejemplo(self):
        import mails_core as mc
        d = mc.datos_de_ejemplo_web("prospecto_oposicion", {"marca": "X"})
        self.assertEqual(d["marca"], "X")
        self.assertTrue(d["codigo"])
        self.assertEqual(mc.datos_de_ejemplo_web("prospecto_oposicion", None), None)

    def test_el_codigo_valido_para_el_endpoint(self):
        self.assertTrue(vc.CODIGO_RE.match("AbC123xyz_-9"))
        import secrets
        for _ in range(50):
            self.assertTrue(vc.CODIGO_RE.match(secrets.token_urlsafe(9)))


class QuienSeOpuso(unittest.TestCase):
    """«¿Quién se opuso?» de la página de oposición: marca del oponente y clases (oposicion_web), sin red ni base."""

    def setUp(self):
        import oposicion_web as ow
        self.ow = ow
        ow._fallos.clear()
        self.guardado = {}
        self.pedidos = []

    def correr(self, fila, cache=None, inpi=None, **kw):
        cache = dict(cache or {})
        inpi = inpi or {}

        def leer(actas):
            return {a: cache[a] for a in actas if a in cache}

        def guardar(acta, estado, datos, logo):
            self.guardado[acta] = estado
            cache[acta] = {"estado": estado, **(datos or {})}

        def consultar(acta):
            self.pedidos.append(acta)
            return inpi.get(acta, {"estado_consulta": "error", "error": "sin respuesta", "datos": None, "logo": None})

        return self.ow.marcas_opositoras(fila, None, None, leer=leer, guardar=guardar, consultar=consultar, pausa=0, **kw)

    def ok(self, den, clase):
        return {"estado_consulta": "ok", "datos": {"denominacion": den, "clase": clase}, "logo": None}

    def test_actas_de(self):
        self.assertEqual(self.ow.actas_de("4094053, 4099176,4094053"), ["4094053", "4099176"])
        self.assertEqual(self.ow.actas_de(None), [])
        self.assertEqual(len(self.ow.actas_de("1111111,2222222,3333333,4444444")), self.ow.MAX_ACTAS)

    def test_agrupa_las_clases_de_la_misma_marca_y_las_ordena(self):
        r = self.ow.agrupar([{"denominacion": "Luna", "clase": "35"}, {"denominacion": "LUNA", "clase": "9"},
                             {"denominacion": "LUNA", "clase": "35"}, {"denominacion": "SOL", "clase": None}])
        self.assertEqual(r, [{"denominacion": "Luna", "clases": ["9", "35"]}, {"denominacion": "SOL", "clases": []}])

    def test_usa_lo_guardado_sin_pedirle_nada_a_inpi(self):
        r = self.correr({"actas_marca_oponente": "4094053"}, cache={"4094053": {"estado": "ok", "denominacion": "LUNA", "clase": "25"}})
        self.assertEqual(r, [{"denominacion": "LUNA", "clases": ["25"]}])
        self.assertEqual(self.pedidos, [])

    def test_trae_de_inpi_lo_que_falta_y_lo_guarda(self):
        r = self.correr({"actas_marca_oponente": "4094053,4099176"},
                        inpi={"4094053": self.ok("LUNA", "25"), "4099176": self.ok("LUNA", "35")})
        self.assertEqual(r, [{"denominacion": "LUNA", "clases": ["25", "35"]}])
        self.assertEqual(self.guardado, {"4094053": "ok", "4099176": "ok"})

    def test_si_inpi_falla_no_levanta_y_no_insiste_enseguida(self):
        fila = {"actas_marca_oponente": "4094053"}
        self.assertEqual(self.correr(fila), [])
        self.assertEqual(self.correr(fila), [])
        self.assertEqual(self.pedidos, ["4094053"])          # la segunda vez no volvió a pedir

    def test_si_inpi_falla_y_hay_nombre_se_muestra_el_nombre(self):
        r = self.correr({"actas_marca_oponente": None, "marca_oponente_denominacion": "BALISTONE"})
        self.assertEqual(r, [{"denominacion": "BALISTONE", "clases": []}])

    def test_marca_inexistente_se_guarda_y_no_se_muestra(self):
        r = self.correr({"actas_marca_oponente": "4094053"},
                        inpi={"4094053": {"estado_consulta": "no_existe", "datos": None, "logo": None}})
        self.assertEqual(r, [])
        self.assertEqual(self.guardado, {"4094053": "no_existe"})

    def test_tope_de_tiempo(self):
        t = iter([0, 0, 100, 100, 100, 100])
        r = self.correr({"actas_marca_oponente": "4094053,4099176"},
                        inpi={"4094053": self.ok("LUNA", "25"), "4099176": self.ok("LUNA", "35")}, reloj=lambda: next(t))
        self.assertEqual(self.pedidos, ["4094053"])           # pasado el presupuesto no se piden más
        self.assertEqual(r, [{"denominacion": "LUNA", "clases": ["25"]}])

    def test_si_las_columnas_estan_vacias_lee_la_marca_del_fundamento(self):
        r = self.correr({"acta": "1", "actas_marca_oponente": None, "marca_oponente_denominacion": None,
                         "fundamento_oposicion": "Somos titulares de las Actas N° 4094053 (clase 25) y 4099176 (clase 35)"},
                        inpi={"4094053": self.ok("LUNA", "25"), "4099176": self.ok("LUNA", "35")})
        self.assertEqual(r, [{"denominacion": "LUNA", "clases": ["25", "35"]}])

    def test_fundamento_con_nombre_y_registro_pero_sin_acta(self):
        r = self.correr({"acta": "1", "fundamento_oposicion": "similar a la marca ALIGN Reg. Nr. 2.345.678 de mi mandante"})
        self.assertEqual(r, [{"denominacion": "ALIGN", "clases": []}])
        self.assertEqual(self.pedidos, [])

    def test_sin_datos_no_hay_nada_que_mostrar(self):
        self.assertEqual(self.correr({}), [])
        self.assertEqual(self.correr(None), [])

    def test_nunca_se_expone_el_fundamento(self):
        r = self.correr({"actas_marca_oponente": "4094053", "fundamento_oposicion": "texto largo"},
                        inpi={"4094053": self.ok("LUNA", "25")})
        self.assertEqual(set(r[0]), {"denominacion", "clases"})


if __name__ == "__main__":
    unittest.main()
