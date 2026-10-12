"""clase_de_pagina: la clase sale del campo CLASE de TITULARIDAD, nunca de texto libre.

Caso real (acta 4782038, DULCE VICIO): la oposición cita "ACTA N° 4250133 REGISTRADA EN LA
CLASE 43" antes del campo real (CLASE: 30) y el panel guardaba 43.
Correr: cd scripts && python3 -m unittest test_clase_de_pagina
"""
import unittest

from validar_leads import clase_de_pagina

CAMPO = '<label class="input"> CLASE: <span class="text-danger"> 30</span> </label>'
OPOS = ("<script>opos = JSON.parse('[{\"Fundamento\":\"MARCA \\u0027VICIO\\u0027 , ACTA N° 4250133 "
        "REGISTRADA EN LA CLASE 43 EN LA REPÚBLICA ARGENTINA\"}]');</script>")


class ClaseDePagina(unittest.TestCase):
    def test_campo_real(self):
        self.assertEqual(clase_de_pagina(CAMPO), 30)

    def test_texto_de_oposicion_antes_del_campo_no_gana(self):
        self.assertEqual(clase_de_pagina(OPOS + CAMPO), 30)

    def test_solo_texto_libre_da_none(self):
        self.assertIsNone(clase_de_pagina(OPOS))

    def test_clase_de_un_digito(self):
        self.assertEqual(clase_de_pagina('<label class="input">CLASE:<span class="text-danger">9</span></label>'), 9)


if __name__ == "__main__":
    unittest.main()
