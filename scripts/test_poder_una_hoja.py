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


if __name__ == "__main__":
    unittest.main()
