"""Pruebas de la definición del formulario de Persona física (servicio solicitado).
Correr: cd scripts && python3 -m unittest test_formularios"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))
import formularios_core as fc  # noqa: E402

BASE = {
    "email": "a@b.com", "nombre": "Gómez María", "nacionalidad": "Argentina", "dni": "30111222",
    "cuit": "27301112229", "domicilio": "Calle 1", "localidad": "CABA", "provincia": "Buenos Aires",
    "codigo_postal": "1000", "estado_civil": "Soltero/a",
}
NUEVA = {"servicio": fc.SERVICIO_NUEVA, "empresa_nombre": "Luna", "rubro": "Gastronomía",
         "actividad_descripcion": "Venta de café", "web_redes": "@luna", "nueva_tiene_logo": "NO"}
OPOSICION = {"servicio": fc.SERVICIO_OPOSICION, "marca_nombre": "Luna", "tiene_logo": "NO"}


def _png():
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


PNG = _png()


def validar(extra, archivos=None):
    return fc.validar("persona-fisica", {**BASE, **extra}, archivos or {})


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

    def test_no_se_mezclan_las_respuestas_de_la_otra_opcion(self):
        datos, validos = validar({**OPOSICION, "empresa_nombre": "X", "rubro": "Y", "nueva_tiene_logo": "SI"},
                                 {"nueva_logo": ("l.png", "image/png", PNG)})
        self.assertNotIn("empresa_nombre", datos)
        self.assertNotIn("rubro", datos)
        self.assertEqual(validos, [])
        datos, _ = validar({**NUEVA, "marca_nombre": "Z", "tiene_logo": "SI"})
        self.assertNotIn("marca_nombre", datos)

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

    def test_persona_juridica_no_cambia(self):
        ids = [c["id"] for c in fc.FORMULARIOS["persona-juridica"]["campos"]]
        self.assertNotIn("servicio", ids)


if __name__ == "__main__":
    unittest.main()
