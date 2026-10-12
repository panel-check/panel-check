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

    def test_muestras_publicas_solo_nombre_clase_y_estado(self):
        r = vc.evaluar("Nike", [fila(77, "NIKE", "25", estado="Vigente")])
        self.assertEqual(r["samples"], [{"denominacion": "NIKE", "clase": 25, "estado": {"codigo": "concedida", "texto": "Concedida"}}])
        self.assertNotIn("acta", r["samples"][0])  # a la persona nunca se le muestra el acta
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

    def test_fundamento_con_actas_escritas_con_puntos(self):
        # Caso real: «SEPPIA (Acta N° 4.745.291 …)» y la propia marca «(Acta N° 4.788.267)»
        fund = ("En mérito del poder especial conferido por los titulares del signo registrado y prioritario SEPPIA "
                "(Acta N° 4.745.291, concedida en Clase 42), vengo a deducir oposición contra la solicitud de la marca "
                "denominativa SEPIA (Acta N° 4.788.267) en la misma Clase 42")
        r = self.correr({"acta": "4788267", "fundamento_oposicion": fund}, inpi={"4745291": self.ok("SEPPIA", "42")})
        self.assertEqual(r, [{"denominacion": "SEPPIA", "clases": ["42"]}])
        self.assertEqual(self.pedidos, ["4745291"])           # la propia marca no se consulta

    def test_fundamento_con_marca_entre_comillas_y_registro(self):
        # Caso real: «la marca “BRANDLIVE”, Reg. N° 3788090 y 3416267 previamente registrada en la misma Clase 42»
        r = self.correr({"acta": "4769001", "fundamento_oposicion":
                         "La marca publicada es similar y confundible con la marca “BRANDLIVE”, Reg. N° 3788090 y 3416267 previamente registrada en la misma Clase 42."})
        self.assertEqual(r, [{"denominacion": "BRANDLIVE", "clases": []}])
        self.assertEqual(self.pedidos, [])

    def test_nombre_del_oponente_sin_documento(self):
        self.assertEqual(self.ow.nombre_oponente({"oponente_nombre": " BRANDLIVE   S.A. ", "oponente_cuit": "30-1"}), "BRANDLIVE S.A.")
        self.assertEqual(self.ow.nombre_oponente({}), "")
        self.assertEqual(self.ow.nombre_oponente(None), "")

    def test_sin_datos_no_hay_nada_que_mostrar(self):
        self.assertEqual(self.correr({}), [])
        self.assertEqual(self.correr(None), [])

    def test_nunca_se_expone_el_fundamento(self):
        r = self.correr({"actas_marca_oponente": "4094053", "fundamento_oposicion": "texto largo"},
                        inpi={"4094053": self.ok("LUNA", "25")})
        self.assertEqual(set(r[0]), {"denominacion", "clases"})


class VariasOposiciones(unittest.TestCase):
    """La página muestra TODAS las oposiciones de la marca (oposicion_web.oposiciones), sin red ni base."""

    def setUp(self):
        import oposicion_web as ow
        self.ow = ow
        ow._fallos.clear()
        self.snap_guardado = None
        self.pedidos = []

    def correr(self, fila, snap=(None, False), inpi=None, marcas=None):
        marcas = marcas or {}

        def consultar(acta):
            self.pedidos.append(("op", acta))
            return inpi if inpi is not None else {"estado_consulta": "error", "error": "caído", "items": []}

        def guardar(acta, items):
            self.snap_guardado = items

        def leer(actas):
            return {a: {"estado": "ok", **marcas[a]} for a in actas if a in marcas}

        return self.ow.oposiciones(fila, None, None, snapshot=lambda a: snap, guardar_snapshot=guardar, consultar=consultar,
                                   leer=leer, guardar=lambda *a: None, pausa=0,
                                   consultar_marca=lambda a: {"estado_consulta": "error", "error": "sin red en los tests"})

    ITEMS = [
        {"oponente": "ANA PEREZ", "fundamento": "titular de la Acta N° 4.745.291 en clase 42"},
        {"oponente": "LUNA SA", "fundamento": "con la marca “LUNA”, Reg. N° 3788090"},
    ]

    def test_muestra_cada_oposicion_con_su_oponente_y_su_marca(self):
        r = self.correr({"acta": "4788267", "oponente_nombre": "ANA PEREZ"},
                        inpi={"estado_consulta": "ok", "items": self.ITEMS},
                        marcas={"4745291": {"denominacion": "SEPPIA", "clase": "42"}})
        self.assertEqual(r, [{"oponente": "ANA PEREZ", "marcas": [{"denominacion": "SEPPIA", "clases": ["42"]}]},
                             {"oponente": "LUNA SA", "marcas": [{"denominacion": "LUNA", "clases": []}]}])
        self.assertEqual(self.snap_guardado, self.ITEMS)                       # queda guardado para no volver a pedirlo

    def test_usa_lo_guardado_si_es_reciente(self):
        r = self.correr({"acta": "1"}, snap=(self.ITEMS, True), marcas={"4745291": {"denominacion": "SEPPIA", "clase": "42"}})
        self.assertEqual(len(r), 2)
        self.assertEqual(self.pedidos, [])

    def test_si_inpi_cae_usa_lo_guardado_aunque_sea_viejo(self):
        r = self.correr({"acta": "1"}, snap=(self.ITEMS, False))
        self.assertEqual([o["oponente"] for o in r], ["ANA PEREZ", "LUNA SA"])
        self.assertEqual(self.pedidos, [("op", "1")])

    def test_si_inpi_cae_y_no_hay_nada_guardado_usa_la_oposicion_del_panel(self):
        r = self.correr({"acta": "1", "oponente_nombre": "BRANDLIVE S.A.", "marca_oponente_denominacion": "BRANDLIVE"})
        self.assertEqual(r, [{"oponente": "BRANDLIVE S.A.", "marcas": [{"denominacion": "BRANDLIVE", "clases": []}]}])

    def test_las_columnas_de_la_marca_no_se_mezclan_cuando_hay_varias(self):
        # actas_marca_oponente es de UNA oposición: con dos, cada una se lee de su propio fundamento
        r = self.correr({"acta": "1", "actas_marca_oponente": "9999999"}, snap=(self.ITEMS, True),
                        marcas={"9999999": {"denominacion": "OTRA", "clase": "1"}})
        self.assertNotIn("OTRA", str(r))

    def test_una_sola_oposicion_sin_nombre_toma_el_del_panel(self):
        r = self.correr({"acta": "1", "oponente_nombre": "ANA PEREZ"}, snap=([{"oponente": None, "fundamento": "x"}], True))
        self.assertEqual(r, [{"oponente": "ANA PEREZ", "marcas": []}])

    def test_sin_nada_devuelve_vacio_y_nunca_el_fundamento(self):
        self.assertEqual(self.correr({"acta": "1"}), [])
        r = self.correr({"acta": "1"}, snap=(self.ITEMS, True))
        self.assertNotIn("fundamento", str(r))

    def test_no_insiste_con_inpi_tras_un_fallo(self):
        self.correr({"acta": "7"})
        self.correr({"acta": "7"})
        self.assertEqual(self.pedidos, [("op", "7")])


import estado_marca as em  # noqa: E402
from datetime import date, datetime  # noqa: E402


class EstadoDeLaMarca(unittest.TestCase):
    HOY = date(2026, 10, 11)

    def est(self, inpi="", db=None):
        return em.estado_publico(inpi, db, self.HOY)

    def codigo(self, inpi="", db=None):
        e = self.est(inpi, db)
        return e["codigo"] if e else None

    def test_la_resolucion_del_panel_manda_sobre_todo(self):
        self.assertEqual(self.codigo("En trámite", {"estado_tramite": "Concedida", "boletin": None}), "concedida")
        self.assertEqual(self.codigo("", {"estado_tramite": "Denegada", "tuvo_oposicion": True}), "denegada")
        self.assertEqual(self.codigo("", {"estado_tramite": "Concedida", "tuvo_oposicion": True}), "concedida")

    def test_lo_que_dice_inpi_se_interpreta(self):
        for texto, esperado in [("Vigente", "concedida"), ("CONCEDIDA", "concedida"), ("Registrada", "concedida"),
                                ("Denegada", "denegada"), ("Abandonada", "inactiva"), ("Caducidad", "inactiva"),
                                ("Vencida", "inactiva"), ("En trámite", "en_tramite"), ("Publicada", "publicada")]:
            with self.subTest(texto=texto):
                self.assertEqual(self.codigo(texto), esperado)

    def test_no_vigente_no_es_concedida(self):
        self.assertEqual(self.codigo("No vigente"), "inactiva")

    def test_acentos_y_mayusculas_no_cuentan(self):
        self.assertEqual(self.codigo("  concedída "), "concedida")

    def test_con_oposicion_hasta_que_se_levanta(self):
        base = {"boletin": "11116", "fecha_publicacion": date(2026, 8, 1), "tuvo_oposicion": True}
        self.assertEqual(self.codigo("", dict(base, estado_oposicion="notificada_en_plazo")), "con_oposicion")
        self.assertEqual(self.codigo("", dict(base, estado_oposicion="levantada")), "publicada")

    def test_publicada_dice_si_todavia_corre_el_plazo_de_oposicion(self):
        e = self.est("", {"boletin": "11116", "fecha_publicacion": date(2026, 10, 1)})
        self.assertEqual((e["codigo"], e["texto"]), ("publicada", em.TEXTO_EN_PLAZO))
        vieja = self.est("", {"boletin": "11116", "fecha_publicacion": date(2026, 8, 1)})
        self.assertEqual(vieja, {"codigo": "publicada", "texto": "Publicada"})
        sin_fecha = self.est("", {"boletin": "11116"})
        self.assertEqual(sin_fecha, {"codigo": "publicada", "texto": "Publicada"})

    def test_el_limite_del_plazo_es_el_dia_30(self):
        dia30 = date.fromordinal(self.HOY.toordinal() - em.PLAZO_OPOSICION_DIAS)
        dia31 = date.fromordinal(self.HOY.toordinal() - em.PLAZO_OPOSICION_DIAS - 1)
        self.assertEqual(self.est("", {"boletin": "1", "fecha_publicacion": dia30})["texto"], em.TEXTO_EN_PLAZO)
        self.assertEqual(self.est("", {"boletin": "1", "fecha_publicacion": dia31})["texto"], "Publicada")

    def test_fecha_con_hora_tambien_sirve(self):
        e = self.est("", {"boletin": "1", "fecha_publicacion": datetime(2026, 10, 5, 3, 0)})
        self.assertEqual(e["texto"], em.TEXTO_EN_PLAZO)

    def test_preboletin_es_una_marca_del_panel_sin_boletin(self):
        e = self.est("En trámite", {"boletin": None, "fuente": "escaneo_directo", "fecha_publicacion": None})
        self.assertEqual((e["codigo"], e["texto"]), ("en_tramite", em.TEXTO_SIN_PUBLICAR))

    def test_marca_que_el_panel_no_tiene_usa_solo_inpi(self):
        self.assertEqual(self.est("En trámite"), {"codigo": "en_tramite", "texto": "En trámite"})

    def test_estado_desconocido_se_muestra_tal_cual_y_sin_adivinar(self):
        e = self.est("  Algo\x00 RARO que escribió INPI y es muy muy muy muy largo  ")
        self.assertEqual(e["codigo"], "otro")
        self.assertLessEqual(len(e["texto"]), em.MAX_TEXTO_OTRO)
        self.assertNotIn("\x00", e["texto"])
        self.assertTrue(e["texto"].startswith("Algo"))

    def test_sin_nada_no_hay_estado(self):
        self.assertIsNone(self.est("", None))
        self.assertIsNone(self.est(None, {}))


class EvaluarConEstadosYClases(unittest.TestCase):
    def test_cada_marca_encontrada_trae_su_estado_del_panel(self):
        db = {"1": {"boletin": "11116", "fecha_publicacion": date(2026, 8, 1), "tuvo_oposicion": True, "estado_oposicion": "contestada"}}
        r = vc.evaluar("Luna", [fila(1, "LUNA", 25, estado="En trámite")], estados_db=db, hoy=date(2026, 10, 11))
        self.assertEqual(r["samples"][0]["estado"]["codigo"], "con_oposicion")
        self.assertEqual(r["muestras"][0]["estado_codigo"], "con_oposicion")
        self.assertEqual(r["muestras"][0]["estado"], "En trámite")  # lo que dijo INPI se sigue guardando para el equipo

    def test_sin_clases_no_hay_marca_de_tus_clases(self):
        r = vc.evaluar("Luna", [fila(1, "LUNA", 25)])
        self.assertNotIn("enTusClases", r["samples"][0])
        self.assertEqual((r["inClassExact"], r["inClassSimilar"]), (0, 0))

    def test_las_clases_no_cambian_el_veredicto_pero_marcan_cada_marca(self):
        filas = [fila(1, "LUNA", 9), fila(2, "LUNA", 25), fila(3, "LUNAS DEL SUR", 25)]
        con = vc.evaluar("Luna", filas, clases=[25])
        sin = vc.evaluar("Luna", filas)
        self.assertEqual(con["verdict"], sin["verdict"])
        self.assertEqual((con["exactCount"], con["similarCount"]), (sin["exactCount"], sin["similarCount"]))
        marcas = {s["clase"]: s["enTusClases"] for s in con["samples"]}
        self.assertEqual(marcas, {9: False, 25: True})
        self.assertEqual((con["inClassExact"], con["inClassSimilar"]), (1, 1))

    def test_las_de_tus_clases_van_primero(self):
        filas = [fila(1, "LUNA", 9), fila(2, "LUNA", 25)]
        r = vc.evaluar("Luna", filas, clases=[25])
        self.assertEqual([s["clase"] for s in r["samples"]], [25, 9])
        self.assertEqual([s["clase"] for s in vc.evaluar("Luna", filas)["samples"]], [9, 25])  # sin clases, el orden de siempre

    def test_misma_marca_en_otra_clase_sigue_contando_como_identica(self):
        r = vc.evaluar("Luna", [fila(1, "LUNA", 9)], clases=[25])
        self.assertEqual(r["verdict"], "no_disponible")
        self.assertEqual((r["inClassExact"], r["inClassSimilar"]), (0, 0))

    def test_resultado_publico_sin_clases(self):
        r = vc.evaluar("Luna", [fila(1, "LUNA", 25)])
        pub = vc.resultado_publico(r["verdict"], r["exactCount"], r["similarCount"], r["muestras"], [], r["posible_mas"])
        self.assertEqual(set(pub), {"verdict", "exactCount", "similarCount", "samples", "moreResults"})
        self.assertFalse(pub["moreResults"])

    def test_resultado_publico_con_clases_y_pagina_llena(self):
        filas = [fila(i, f"LUNA {i}", 25) for i in range(vc.LIMITE_INPI)]
        r = vc.evaluar("Luna", filas, clases=[25])
        pub = vc.resultado_publico(r["verdict"], r["exactCount"], r["similarCount"], r["muestras"], [25], r["posible_mas"])
        self.assertEqual(pub["classes"], [25])
        self.assertTrue(pub["moreResults"])
        self.assertGreater(pub["inClassSimilar"], 0)

    def test_lo_guardado_alcanza_para_rearmar_el_resultado(self):
        """El link del resultado (GET /{codigo}) se arma solo con lo que quedó en la base."""
        r = vc.evaluar("Luna", [fila(1, "LUNA", 25), fila(2, "LUNA", 9)], clases=[25])
        guardado = json_ida_y_vuelta(r["muestras"])
        pub = vc.resultado_publico(r["verdict"], r["exactCount"], r["similarCount"], guardado, [25])
        self.assertEqual(pub["samples"], r["samples"])
        self.assertEqual(pub["inClassExact"], r["inClassExact"])

    def test_nada_privado_llega_a_la_persona(self):
        r = vc.evaluar("Luna", [fila(1, "LUNA", 25)], clases=[25])
        for s in r["samples"]:
            self.assertEqual(set(s), {"denominacion", "clase", "estado", "enTusClases"})


def json_ida_y_vuelta(x):
    import json
    return json.loads(json.dumps(x))


class ValidacionBusquedaSinDatos(unittest.TestCase):
    def test_solo_el_nombre_alcanza(self):
        self.assertEqual(vc.validar_busqueda("  Mi   Marca "), {"marca": "Mi Marca", "clases": []})

    def test_nombre_invalido(self):
        for malo, texto in [("a", "marca"), ("!!!", "letras"), ("", "marca")]:
            with self.subTest(malo=malo):
                with self.assertRaises(ValueError) as cm:
                    vc.validar_busqueda(malo)
                self.assertIn(texto, str(cm.exception))

    def test_clases_se_limpian(self):
        self.assertEqual(vc.validar_clases([25, "35", 25, "x", 0, 46, None, " 9 "]), [25, 35, 9])

    def test_clases_que_no_son_una_lista_se_ignoran(self):
        for v in (None, "25", 25, {"a": 1}):
            self.assertEqual(vc.validar_clases(v), [])

    def test_demasiadas_clases(self):
        with self.assertRaises(ValueError):
            vc.validar_clases(list(range(1, vc.CLASES_MAX + 2)))
        self.assertEqual(len(vc.validar_clases(list(range(1, vc.CLASES_MAX + 1)))), vc.CLASES_MAX)

    def test_contacto_valido_se_limpia(self):
        d = vc.validar_contacto(" Ropa ", " Ana  Pérez ", "ANA@Correo.com ", "11 5555", "")
        self.assertEqual((d["actividad"], d["nombre"], d["email"]), ("Ropa", "Ana Pérez", "ana@correo.com"))

    def test_contacto_con_errores_en_castellano(self):
        for campos, texto in [(("Ropa", "", "ana@x.com"), "llamás"), (("Ropa", "Ana", "ana@"), "e-mail"), (("", "Ana", "ana@x.com"), "dedica")]:
            with self.subTest(campos=campos):
                with self.assertRaises(ValueError) as cm:
                    vc.validar_contacto(*campos)
                self.assertIn(texto, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
