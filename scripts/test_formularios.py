"""Pruebas de la definición de los formularios de Persona física y jurídica (servicio solicitado).
Correr: cd scripts && python3 -m unittest test_formularios"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))
import formularios_core as fc  # noqa: E402

BASE = {
    "email": "a@b.com", "nombre": "Gómez María", "nacionalidad": "Argentina", "dni": "30111222",
    "cuit": "27301112229", "domicilio": "Calle 1", "localidad": "CABA", "provincia": "Buenos Aires",
    "codigo_postal": "1000", "estado_civil": "Soltero/a", "sexo": "Femenino", "cantidad_titulares": "1",
}
NUEVA = {"servicio": fc.SERVICIO_NUEVA, "empresa_nombre": "Luna", "rubro": "Gastronomía",
         "actividad_descripcion": "Venta de café", "web_redes": "@luna", "nueva_tiene_logo": "NO"}
OPOSICION = {"servicio": fc.SERVICIO_OPOSICION, "marca_nombre": "Luna", "tiene_logo": "NO"}
VISTAS = {"servicio": fc.SERVICIO_VISTAS, "marca_nombre": "Luna"}


def _png():
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


PNG = _png()


def validar(extra, archivos=None):
    return fc.validar("persona-fisica", {**BASE, **extra}, archivos or {})


class PoderManual(unittest.TestCase):
    def test_el_poder_no_se_manda_solo(self):
        # Desde el 16/11/2026 el poder del formulario de Persona Física lo manda el equipo a mano.
        self.assertFalse(fc.ENVIAR_PODER_AUTOMATICO)


class ServicioPersonaFisica(unittest.TestCase):
    def test_servicio_va_despues_del_domicilio_comercial_y_es_obligatorio(self):
        ids = [c["id"] for c in fc.FORMULARIOS["persona-fisica"]["campos"]]
        self.assertGreater(ids.index("servicio"), ids.index("domicilio_comercial"))
        with self.assertRaises(fc.ErrorFormulario) as e:
            validar({})
        self.assertIn("¿Qué servicio solicitás?", str(e.exception))

    def test_nueva_marca_pide_sus_campos(self):
        datos, _ = validar(NUEVA)
        self.assertEqual(datos["empresa_nombre"], "Luna")
        for falta, etiqueta in [("empresa_nombre", "Nombre de la empresa"), ("rubro", "Rubro"),
                                ("actividad_descripcion", "Descripción de la actividad"), ("web_redes", "Web o Redes"),
                                ("nueva_tiene_logo", "¿Tiene logo?")]:
            with self.assertRaises(fc.ErrorFormulario) as e:
                validar({k: v for k, v in NUEVA.items() if k != falta})
            self.assertIn(etiqueta, str(e.exception))

    def test_nueva_marca_con_logo_exige_archivo(self):
        extra = {**NUEVA, "nueva_tiene_logo": "SI"}
        with self.assertRaises(fc.ErrorFormulario) as e:
            validar(extra)
        self.assertIn("Adjunte su logo", str(e.exception))
        datos, validos = validar(extra, {"nueva_logo": ("logo.png", "image/png", PNG)})
        self.assertEqual([v["campo"] for v in validos], ["nueva_logo"])

    def test_oposicion_pide_nombre_logo_y_descripcion_opcional(self):
        datos, _ = validar(OPOSICION)
        self.assertEqual(datos["marca_nombre"], "Luna")
        self.assertNotIn("marca_descripcion", datos)
        with self.assertRaises(fc.ErrorFormulario):
            validar({k: v for k, v in OPOSICION.items() if k != "marca_nombre"})
        extra = {**OPOSICION, "tiene_logo": "SI"}
        with self.assertRaises(fc.ErrorFormulario):
            validar(extra)
        _, validos = validar(extra, {"logo": ("logo.png", "image/png", PNG)})
        self.assertEqual([v["campo"] for v in validos], ["logo"])

    def test_oposicion_guarda_web_o_redes_opcional(self):
        datos, _ = validar({**OPOSICION, "marca_web_redes": "@luna"})
        self.assertEqual(datos["marca_web_redes"], "@luna")
        self.assertNotIn("marca_web_redes", validar(OPOSICION)[0])

    def test_vistas_es_una_opcion_y_pide_nombre_descripcion_y_web(self):
        servicio = next(c for c in fc.FORMULARIOS["persona-fisica"]["campos"] if c["id"] == "servicio")
        self.assertIn("Respuesta de Vistas", servicio["opciones"])
        datos, _ = validar({**VISTAS, "marca_descripcion": "Venta de café", "marca_web_redes": "www.luna.com"})
        self.assertEqual((datos["marca_nombre"], datos["marca_descripcion"], datos["marca_web_redes"]),
                         ("Luna", "Venta de café", "www.luna.com"))
        self.assertEqual(fc.nombre_marca(datos), "Luna")
        with self.assertRaises(fc.ErrorFormulario) as e:
            validar({"servicio": fc.SERVICIO_VISTAS})
        self.assertIn("Nombre de la marca", str(e.exception))

    def test_vistas_no_pide_logo_ni_campos_de_marca_nueva(self):
        datos, validos = validar({**VISTAS, "tiene_logo": "SI", "web_redes": "@x", "rubro": "Y"},
                                 {"logo": ("l.png", "image/png", PNG)})
        for campo in ("tiene_logo", "web_redes", "rubro"):
            self.assertNotIn(campo, datos)
        self.assertEqual(validos, [])

    def test_avisos_de_datos_iguales_al_inpi(self):
        campos = {c["id"]: c for c in fc.FORMULARIOS["persona-fisica"]["campos"]}
        ids = list(campos)
        self.assertEqual(ids[0], "nota_datos_inpi")  # arriba de los datos personales, siempre visible
        self.assertIn("coincidir con los ingresados", campos["nota_datos_inpi"]["ayuda"])
        recordatorio = campos["nota_coinciden_inpi"]
        for servicio, ve in [(fc.SERVICIO_NUEVA, False), (fc.SERVICIO_OPOSICION, True), (fc.SERVICIO_VISTAS, True)]:
            self.assertEqual(fc.campo_visible(recordatorio, {"servicio": servicio}), ve)
        datos, _ = validar(VISTAS)  # las notas no se guardan como respuestas
        self.assertFalse([k for k in datos if k.startswith("nota_")])

    def test_no_se_mezclan_las_respuestas_de_la_otra_opcion(self):
        datos, validos = validar({**OPOSICION, "empresa_nombre": "X", "rubro": "Y", "nueva_tiene_logo": "SI"},
                                 {"nueva_logo": ("l.png", "image/png", PNG)})
        self.assertNotIn("empresa_nombre", datos)
        self.assertNotIn("rubro", datos)
        self.assertEqual(validos, [])
        datos, _ = validar({**NUEVA, "marca_nombre": "Z", "tiene_logo": "SI", "marca_web_redes": "@z"})
        self.assertNotIn("marca_nombre", datos)
        self.assertNotIn("marca_web_redes", datos)

    def test_nombre_de_marca_y_asunto_del_mail(self):
        self.assertEqual(fc.nombre_marca({"empresa_nombre": "Luna"}), "Luna")
        self.assertEqual(fc.nombre_marca({"marca_nombre": "Sol"}), "Sol")
        for datos, marca in [({**NUEVA, "nombre": "María"}, "Luna"), ({**OPOSICION, "nombre": "María"}, "Luna")]:
            resp = {"formulario": "persona-fisica", "tipo_persona": "fisica", "datos": datos, "cliente_id": None, "cliente_nuevo": True}
            asunto, _, _ = fc.armar_mail(resp, {}, [], "https://panel")
            self.assertIn(f"«{marca}»", asunto)

    def test_condiciones_combinadas(self):
        c = {"si": [{"campo": "a", "valor": "1"}, {"campo": "b", "valor": "2"}]}
        self.assertTrue(fc.campo_visible(c, {"a": "1", "b": "2"}))
        self.assertFalse(fc.campo_visible(c, {"a": "1", "b": "3"}))
        self.assertTrue(fc.campo_visible({"si": {"campo": "a", "valor": "1"}}, {"a": "1"}))
        self.assertTrue(fc.campo_visible({}, {}))

    def test_no_aplica_no_pide_nada_mas(self):
        datos, _ = validar({"servicio": fc.SERVICIO_NO_APLICA})
        self.assertEqual(datos["servicio"], fc.SERVICIO_NO_APLICA)
        visibles = [c["id"] for c in fc.FORMULARIOS["persona-fisica"]["campos"]
                    if fc.campo_visible(c, {**BASE, "servicio": fc.SERVICIO_NO_APLICA})]
        self.assertEqual(visibles[-1], "servicio")
        # aunque mande respuestas de otro servicio, no se guardan
        datos, _ = validar({"servicio": fc.SERVICIO_NO_APLICA, "marca_nombre": "Luna"})
        self.assertNotIn("marca_nombre", datos)

    def test_no_aplica_es_la_ultima_opcion_en_los_dos(self):
        for f in fc.FORMULARIOS.values():
            serv = next(c for c in f["campos"] if c["id"] == "servicio")
            self.assertEqual(serv["opciones"], [fc.SERVICIO_NUEVA, fc.SERVICIO_OPOSICION, fc.SERVICIO_VISTAS,
                                                fc.SERVICIO_NO_APLICA])
            self.assertIn("únicamente si te pedimos que vuelvas a completar", serv["opciones"][-1])


JURIDICA = {"email": "a@b.com", "nombre": "Luna S.R.L.", "cuit": "30712345678", "firmante_nombre": "Gómez María",
            "firmante_cargo": "Socia gerente", "domicilio": "Calle 1, CABA", "provincia": "Ciudad Autónoma de Buenos Aires"}
DOCS = {"constancia_cuit": ("c.png", "image/png", PNG), "estatuto": ("e.png", "image/png", PNG)}


class ServicioPersonaJuridica(unittest.TestCase):
    def _validar(self, extra, archivos=None):
        return fc.validar("persona-juridica", {**JURIDICA, **extra}, {**DOCS, **(archivos or {})})

    def test_servicio_va_al_final_de_los_datos_de_la_empresa_y_es_obligatorio(self):
        ids = [c["id"] for c in fc.FORMULARIOS["persona-juridica"]["campos"]]
        self.assertEqual(ids.index("servicio"), ids.index("estatuto") + 1)
        with self.assertRaises(fc.ErrorFormulario) as e:
            self._validar({})
        self.assertIn("¿Qué servicio solicitás?", str(e.exception))

    def test_mismas_preguntas_que_persona_fisica_segun_el_servicio(self):
        def desde_servicio(slug):
            ids = [c["id"] for c in fc.FORMULARIOS[slug]["campos"]]
            return ids[ids.index("servicio"):]
        self.assertEqual(desde_servicio("persona-juridica"), desde_servicio("persona-fisica"))
        datos, _ = self._validar(NUEVA)
        self.assertEqual(datos["empresa_nombre"], "Luna")
        datos, _ = self._validar(VISTAS)
        self.assertEqual(datos["marca_nombre"], "Luna")
        with self.assertRaises(fc.ErrorFormulario):
            self._validar({"servicio": fc.SERVICIO_OPOSICION})  # falta nombre de la marca y si tiene logo
        datos, _ = self._validar(OPOSICION)
        self.assertEqual(datos["tiene_logo"], "NO")

    def test_la_nota_del_inpi_habla_de_datos_de_la_empresa(self):
        nota = next(c for c in fc.FORMULARIOS["persona-juridica"]["campos"] if c["id"] == "nota_coinciden_inpi")
        self.assertIn("razón social, CUIT y domicilio", nota["ayuda"])
        self.assertNotIn("DNI", nota["ayuda"])

    def test_no_aplica_no_pide_nada_mas(self):
        datos, _ = self._validar({"servicio": fc.SERVICIO_NO_APLICA})
        self.assertEqual(datos["servicio"], fc.SERVICIO_NO_APLICA)
        self.assertEqual(fc.nombre_marca(datos), "")


class VariosTitulares(unittest.TestCase):
    T2 = {"t2_nombre": "Ruiz Pedro", "t2_sexo": "Masculino", "t2_nacionalidad": "Argentina", "t2_dni": "29.888.777",
          "t2_cuit": "20298887771", "t2_domicilio": "Mitre 50", "t2_localidad": "Quilmes", "t2_provincia": "Buenos Aires",
          "t2_codigo_postal": "1878", "t2_estado_civil": "Soltero/a", "t2_porcentaje": "50"}

    def _slug(self):
        return next(k for k, f in fc.FORMULARIOS.items() if f["tipo_persona"] == "fisica")

    def test_pide_datos_del_segundo_titular(self):
        with self.assertRaises(fc.ErrorFormulario) as e:
            fc.validar(self._slug(), {**BASE, **NUEVA, "cantidad_titulares": "2"}, {})
        self.assertIn("Titular 2: Apellido y Nombre", str(e.exception))
        datos, _ = fc.validar(self._slug(), {**BASE, **NUEVA, **self.T2, "cantidad_titulares": "2", "porcentaje": "50"}, {})
        self.assertEqual(datos["t2_dni"], "29888777")
        cot = fc.cotitulares(datos)
        self.assertEqual([c["nombre"] for c in cot], ["Ruiz Pedro"])
        self.assertIn("Ruiz Pedro", fc.datos_cliente(self._slug(), datos)["cotitulares"])
        self.assertEqual(cot[0]["porcentaje"], "50")
        self.assertEqual(fc.datos_cliente(self._slug(), datos)["porcentaje_titularidad"], "50")

    def test_porcentaje_de_titularidad(self):
        base = {**BASE, **NUEVA, **self.T2, "cantidad_titulares": "2"}
        with self.assertRaises(fc.ErrorFormulario) as e:  # con varios titulares es obligatorio
            fc.validar(self._slug(), {k: v for k, v in base.items() if k != "t2_porcentaje"}, {})
        self.assertIn("Titular 2: Porcentaje de titularidad", str(e.exception))
        with self.assertRaises(fc.ErrorFormulario) as e:
            fc.validar(self._slug(), {**base, "porcentaje": "60"}, {})
        self.assertIn("suman 110 %", str(e.exception))
        with self.assertRaises(fc.ErrorFormulario):
            fc.validar(self._slug(), {**base, "porcentaje": "cincuenta"}, {})
        datos, _ = fc.validar(self._slug(), {**base, "porcentaje": "50 %", "t2_porcentaje": "50,0"}, {})
        self.assertEqual((datos["porcentaje"], datos["t2_porcentaje"]), ("50", "50"))
        # con un solo titular no se pregunta
        datos, _ = fc.validar(self._slug(), {**BASE, **NUEVA, "porcentaje": "30"}, {})
        self.assertNotIn("porcentaje", datos)

    def test_hasta_seis_titulares_que_suman_100(self):
        self.assertEqual(fc.MAX_TITULARES, 6)
        extra = {"cantidad_titulares": "6", "porcentaje": "16,7"}
        for n in range(2, 7):
            extra.update({k.replace("t2_", f"t{n}_"): v for k, v in self.T2.items()})
            extra[f"t{n}_nombre"] = f"Titular {n}"
            extra[f"t{n}_porcentaje"] = "16,66"
        datos, _ = fc.validar(self._slug(), {**BASE, **NUEVA, **extra}, {})
        self.assertEqual(len(fc.cotitulares(datos)), 5)

    def test_un_titular_ignora_los_demas(self):
        datos, _ = fc.validar(self._slug(), {**BASE, **NUEVA, **self.T2, "cantidad_titulares": "1"}, {})
        self.assertNotIn("t2_nombre", datos)
        self.assertEqual(fc.cotitulares(datos), [])
        self.assertIsNone(fc.datos_cliente(self._slug(), datos)["cotitulares"])

    def test_cambia_poder_y_aviso(self):
        self.assertTrue(fc.cambia_poder({"t2_dni": "1"}, {"t2_dni": "2"}))
        self.assertTrue(fc.cambia_poder({"cantidad_titulares": "1"}, {"cantidad_titulares": "2"}))
        self.assertTrue(fc.cambia_poder({"t6_nombre": "A"}, {"t6_nombre": "B"}))
        cuerpo = "Hola,\n\nTe adjunto el poder.\n\nSaludos,\nEquipo"
        self.assertEqual(fc.agregar_aviso_titulares(cuerpo, 1), cuerpo)
        con = fc.agregar_aviso_titulares(cuerpo, 3)
        self.assertLess(con.index("3 titulares"), con.index("Saludos"))


if __name__ == "__main__":
    unittest.main()


class CorreccionConLink(unittest.TestCase):
    """Link para que el cliente corrija lo que mandó (sin base: solo las reglas)."""

    def test_al_corregir_el_archivo_ya_guardado_cuenta(self):
        extra = {**OPOSICION, "tiene_logo": "SI"}
        with self.assertRaises(fc.ErrorFormulario):
            validar(extra)
        datos, validos = fc.validar("persona-fisica", {**BASE, **extra}, {}, existentes={"logo"})
        self.assertEqual(validos, [])
        self.assertEqual(datos["tiene_logo"], "SI")

    def test_diferencias_en_orden_y_con_archivos(self):
        antes = {**BASE, **OPOSICION}
        despues = {**antes, "domicilio": "Calle 2", "marca_web_redes": "@luna"}
        cambios = fc.diferencias("persona-fisica", antes, despues, {"logo": "viejo.png"}, {"logo": "nuevo.png"})
        self.assertEqual([c["etiqueta"] for c in cambios], ["Domicilio particular", "Web o Redes", "Adjunte su logo en el tamaño 4x4"])
        self.assertEqual(cambios[0], {"etiqueta": "Domicilio particular", "antes": "Calle 1", "despues": "Calle 2"})
        self.assertEqual(cambios[1]["antes"], "—")
        self.assertEqual(fc.diferencias("persona-fisica", antes, dict(antes)), [])

    def test_solo_los_datos_del_poder_reenvian_el_poder(self):
        antes = {**BASE, **OPOSICION}
        self.assertFalse(fc.cambia_poder(antes, {**antes, "marca_nombre": "Sol", "telefono": "11"}))
        for campo in ("nombre", "dni", "domicilio", "localidad", "provincia", "email"):
            self.assertTrue(fc.cambia_poder(antes, {**antes, campo: "otro"}), campo)

    def test_token_largo_y_distinto(self):
        a, b = fc.nuevo_token(), fc.nuevo_token()
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 30)

    def test_mail_de_correccion_muestra_que_cambio(self):
        resp = {"id": 1, "formulario": "persona-fisica", "tipo_persona": "fisica", "cliente_id": 5, "cliente_nuevo": False,
                "datos": {**BASE, **VISTAS}}
        cambios = [{"etiqueta": "Domicilio particular", "antes": "Calle 1", "despues": "Calle <2>"}]
        asunto, cuerpo, _ = fc.armar_mail(resp, {}, [], "https://panel", cambios)
        self.assertTrue(asunto.startswith("✏️ Formulario corregido: Gómez María"))
        self.assertIn("Qué cambió", cuerpo)
        self.assertIn("Calle &lt;2&gt;", cuerpo)
        asunto, cuerpo, _ = fc.armar_mail(resp, {}, [], "https://panel")
        self.assertTrue(asunto.startswith("📝 Formulario recibido"))
        self.assertNotIn("Qué cambió", cuerpo)


class CambiarDeFormularioAlCorregir(unittest.TestCase):
    """Con el link para corregir, quien se equivocó de formulario (física ↔ jurídica) se pasa al otro."""
    FISICA = {**BASE, **OPOSICION, "telefono": "1155556666", "marca_descripcion": "Café"}

    def test_pasa_los_datos_que_sirven(self):
        a_juridica = fc.datos_para_otro_formulario(self.FISICA, "persona-juridica")
        self.assertEqual(a_juridica["email"], "a@b.com")
        self.assertEqual(a_juridica["telefono"], "1155556666")
        self.assertEqual(a_juridica["domicilio"], "Calle 1")
        self.assertEqual(a_juridica["firmante_nombre"], "Gómez María")  # quien completó firma por la empresa
        self.assertEqual(a_juridica["servicio"], fc.SERVICIO_OPOSICION)
        self.assertEqual(a_juridica["marca_nombre"], "Luna")
        for k in ("nombre", "cuit", "dni", "sexo", "estado_civil"):  # razón social y CUIT son de la empresa
            self.assertNotIn(k, a_juridica)
        a_fisica = fc.datos_para_otro_formulario({**JURIDICA, **VISTAS}, "persona-fisica")
        self.assertEqual(a_fisica["nombre"], "Gómez María")
        self.assertEqual(a_fisica["servicio"], fc.SERVICIO_VISTAS)
        self.assertNotIn("cuit", a_fisica)
        self.assertNotIn("firmante_nombre", a_fisica)

    def test_guarda_con_el_otro_tipo(self):
        class Cur:
            def __init__(self):
                self.sql = []

            def execute(self, q, v=None):
                self.sql.append((q, v))

            def fetchall(self):
                return []

        datos, _ = fc.validar("persona-juridica", {**JURIDICA, **OPOSICION}, DOCS)
        cur = Cur()
        resp = {"id": 7, "formulario": "persona-fisica", "datos": self.FISICA, "cliente_id": 3}
        res = fc.aplicar_edicion(cur, resp, datos, [], "1.1.1.1", slug_nuevo="persona-juridica")
        self.assertEqual(res["cambios"][0], {"etiqueta": "Tipo de formulario", "antes": "Persona física",
                                             "despues": "Persona jurídica"})
        self.assertFalse(res["reenviar_poder"])
        upd_resp = next(v for q, v in cur.sql if q.startswith("UPDATE formularios_respuestas"))
        self.assertIn("persona-juridica", upd_resp)
        self.assertIn("juridica", upd_resp)
        q, v = next((q, v) for q, v in cur.sql if q.startswith("UPDATE clientes"))
        self.assertIn("tipo_persona = %s", q)
        self.assertIn("cotitulares = %s", q)
        self.assertIn("Luna S.R.L.", v)
        self.assertIn("juridica", v)
