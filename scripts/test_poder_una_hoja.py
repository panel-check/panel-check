"""El PDF del poder (el que se manda por mail al completar el formulario y el de «Generar poder para firmar»)
tiene que salir siempre en una sola hoja, aunque el domicilio o el apoderado sean muy largos.

    cd scripts && python3 -m unittest test_poder_una_hoja
"""
import datetime as dt
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

try:
    import pypdf
    import reportlab  # noqa: F401
except ImportError:  # pragma: no cover
    pypdf = None

import poderes


def _hojas(pdf: bytes) -> int:
    return len(pypdf.PdfReader(io.BytesIO(pdf)).pages)


@unittest.skipIf(pypdf is None, "falta reportlab o pypdf")
class PoderUnaHoja(unittest.TestCase):
    def _datos(self, tipo, otorgante_extra=""):
        cliente = {"nombre": "Juan Carlos Pérez", "dni": "30123456", "cuit": "30712345678",
                   "domicilio": "Avenida Rivadavia 12345, piso 3, departamento B" + otorgante_extra,
                   "localidad": "Ramos Mejía", "provincia": "Buenos Aires", "tipo_persona": tipo}
        return poderes.propuesta(cliente, dt.date(2026, 11, 2))

    def test_caso_normal_una_hoja(self):
        for tipo in ("fisica", "juridica"):
            self.assertEqual(_hojas(poderes.generar_pdf(self._datos(tipo), poderes.APODERADO_DEFAULT)), 1)

    def test_caso_normal_mantiene_el_diseno(self):
        # si entra con el diseño de siempre, no se toca nada
        d = self._datos("fisica")
        normal, hojas = poderes._armar_pdf(d, poderes.APODERADO_DEFAULT, *poderes._AJUSTES_UNA_HOJA[0])
        self.assertEqual(hojas, 1)
        self.assertEqual(len(poderes.generar_pdf(d, poderes.APODERADO_DEFAULT)), len(normal))

    def test_domicilio_y_apoderado_muy_largos_entran_en_una_hoja(self):
        largo = ", barrio Parque Los Robles, entre las calles San Martín y Belgrano" * 8
        apoderado = poderes.APODERADO_DEFAULT + ", y Fulano de Tal, agente de propiedad industrial, " * 4
        for tipo in ("fisica", "juridica"):
            d = self._datos(tipo, largo)
            sin_ajuste, hojas = poderes._armar_pdf(d, apoderado, *poderes._AJUSTES_UNA_HOJA[0])
            self.assertGreater(hojas, 1)  # con el diseño normal se iba a dos hojas
            self.assertEqual(_hojas(poderes.generar_pdf(d, apoderado)), 1)


@unittest.skipIf(pypdf is None, "falta reportlab o pypdf")
class PoderSimpleMarcas(unittest.TestCase):
    def _datos(self, extra="", genero="neutro"):
        cliente = {"nombre": "Juan Carlos Pérez", "dni": "30123456",
                   "domicilio": "Avenida Rivadavia 12345, piso 3, departamento B" + extra,
                   "localidad": "Ramos Mejía", "provincia": "Buenos Aires"}
        d = poderes.propuesta_simple(cliente, dt.date(2026, 11, 9))
        d["genero"] = genero
        return d

    def test_una_hoja_normal_y_largo(self):
        self.assertEqual(_hojas(poderes.generar_pdf_simple(self._datos())), 1)
        largo = ", barrio Parque Los Robles, entre las calles San Martín y Belgrano" * 10
        self.assertEqual(_hojas(poderes.generar_pdf_simple(self._datos(largo))), 1)

    def test_texto_del_modelo(self):
        d = self._datos()
        txt = pypdf.PdfReader(io.BytesIO(poderes.generar_pdf_simple(d))).pages[0].extract_text()
        for frase in ("El/La abajo firmante Juan Carlos Pérez", "Matrícula INPI N.º 2906", "representarlo/a",
                      "del/de la mandante", "a los 9 días del mes de noviembre de 2026", "ACLARACIÓN:", "DNI:"):
            self.assertIn(frase, " ".join(txt.split()))

    def test_genero(self):
        p = poderes._simple_parrafos(self._datos(genero="femenino"))
        self.assertTrue(p[0].startswith("La abajo firmante"))
        self.assertIn("representarla", p[0])
        self.assertIn("de la mandante", p[1])

    def test_sexo_del_formulario(self):
        base = {"nombre": "Ana López", "dni": "28999111", "domicilio": "Corrientes 1234", "provincia": "CABA"}
        for sexo, esperado in (("Masculino", "El abajo"), ("Femenino", "La abajo"), ("Otro", "El/La abajo"), ("", "El/La abajo")):
            d = poderes.propuesta_simple(dict(base, sexo=sexo), dt.date(2026, 11, 9))
            self.assertTrue(poderes._simple_parrafos(d)[0].startswith(esperado), sexo)

    def test_primer_dia_y_docx(self):
        d = self._datos(); d["fecha"] = "2026-12-01"
        self.assertIn("al 1 día del mes de diciembre", poderes._simple_lugar_fecha(d))
        self.assertTrue(poderes.generar_docx_simple(d).startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
