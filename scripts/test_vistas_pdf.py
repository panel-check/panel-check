"""
Pruebas de vistas_pdf.py (sin red). Correr con:
    cd scripts && python3 -m unittest test_vistas_pdf -v

Los textos son los de las vistas reales de INPI del 17/10/2026 (actas 4797016, 4797046,
4797082 y la vista formal de logotipos), tal cual salen del PDF: palabras pegadas y
bloques mezclados.
"""

import unittest

from vistas_pdf import (
    descargar_texto_pdf, filas_de_vista, parsear_vista, plazo_legible,
    sumar_dias_habiles, vencimiento_desde_texto,
)

FONDO_2B = (
    "Visto elexpediente bajo análisis se notificaalsolicitante que:Elsigno pretendido seencuentraalcanzado porel/ los "
    "ART. 2 Bdela Ley 22.362 y modificatorias, por lo tanto deberá,en el plazo indicado másabajo, manifestar lo queestimecorresponder. "
    "La presente vistase notifica por 30 días corridos deacuerdo ala Resolución INPI P 123/2019,anexo,artículo 5°, pudiendo hacer uso "
    "elsolicitante delasextensionesautomáticas previstasen elartículo 6° dela misma norma,abonando elarancelcorrespondiente(Cod. 770.001)."
)
FONDO_3A_ANTECEDENTE = (
    "Visto elexpediente bajo análisis se notificaalsolicitante que:acta_antecedente: 4011896; denom:FELICIANO Elsigno pretendido seencuentra "
    "alcanzado porel/ los ART. 3 Adela Ley 22.362 ymodificatorias, por lo tanto deberá,en el plazo indicado másabajo, manifestar lo queestime "
    "corresponder. La presente vistase notifica por 30 díascorridos deacuerdo ala Resolución INPI P 123/2019,anexo,artículo 5°, pudiendo hacer uso el "
    "solicitante delasextensionesautomáticas previstasen elartículo 6° dela misma norma,abonando elarancelcorrespondiente(Cod. 770.001)."
)
FONDO_2A = (
    "Visto elexpediente bajo análisis se notificaalsolicitante que:Elsigno pretendido seencuentraalcanzado porel/ los ART. 2 INC. A) dela Ley 22.362 "
    "ymodificatorias, por lo tanto deberá,en el plazo indicado másabajo, manifestar lo queestimecorresponder. La presente vistase notifica por 30 días "
    "corridos deacuerdo ala Resolución INPI P 123/2019,anexo,artículo 5°"
)
FORMAL = (
    "Se hacesaberalinteresado que deberáacompañar nuevos logotipos, sin alterarenmodo alguno la marcasolicitada,en dondetodo elsigno marcario "
    "solicitado seaprecieen formaclaraafin de unacorrecta publicación. A) En las Solicitudes de marcas detipo mixto o figurativo, deberán acompañarse "
    "las imágenes respectivasen un archivo detipo JPGo BMP deentre 200 y 300 DPIcuyo tamaño no excedalos 2 MB. Asimismo, deberáabonarseelarancel "
    "correspondientealacontestación de vistaformal vigenteestablecido en el punto 7.5 código 750001. "
    "Notifíquese: porel plazo de 10 (diez) días hábiles bajo apercibimiento de dictar resolución denegatoria,conformealo establecido en el "
    "artículo 12 del decreto 242/19."
)


class ParsearVista(unittest.TestCase):
    def test_fondo_art_2_b(self):
        r = parsear_vista(FONDO_2B)
        self.assertEqual(r["tipo"], "fondo")
        self.assertEqual(r["articulos"], ["Art. 2 inc. b)"])
        self.assertEqual(r["plazos"], [(30, "corridos")])
        self.assertTrue(r["extensiones_automaticas"])
        self.assertEqual(r["antecedentes"], [])

    def test_fondo_art_3_a_con_antecedente(self):
        r = parsear_vista(FONDO_3A_ANTECEDENTE)
        self.assertEqual(r["tipo"], "fondo")
        self.assertEqual(r["articulos"], ["Art. 3 inc. a)"])
        self.assertEqual(r["antecedentes"], ["4011896 (FELICIANO)"])
        self.assertEqual(r["plazos"], [(30, "corridos")])

    def test_fondo_art_2_inc_a_con_parentesis(self):
        r = parsear_vista(FONDO_2A)
        self.assertEqual(r["articulos"], ["Art. 2 inc. a)"])
        self.assertEqual(r["plazos"], [(30, "corridos")])

    def test_formal_logotipos(self):
        r = parsear_vista(FORMAL)
        self.assertEqual(r["tipo"], "formal")
        self.assertEqual(r["plazos"], [(10, "habiles")])
        self.assertEqual(r["articulos"], [])  # "artículo 12 del decreto" no es un artículo de la Ley 22.362

    def test_fondo_y_formal_juntas(self):
        r = parsear_vista(FONDO_2A + " " + FORMAL)
        self.assertEqual(r["tipo"], "fondo_y_formal")
        self.assertEqual(r["plazos"], [(30, "corridos"), (10, "habiles")])
        self.assertEqual(plazo_legible(r), "30 días corridos + 10 días hábiles")

    def test_texto_desconocido_no_rompe(self):
        r = parsear_vista("texto cualquiera sin nada")
        self.assertEqual((r["tipo"], r["plazos"], r["articulos"]), ("desconocido", [], []))
        self.assertEqual(parsear_vista("")["tipo"], "desconocido")


class Vencimiento(unittest.TestCase):
    def test_dias_habiles_saltea_fines_de_semana(self):
        # miércoles 07/10/2026 + 10 hábiles = miércoles 21/10 (sin descontar el feriado del 12/10)
        self.assertEqual(sumar_dias_habiles("2026-10-07", 10), "2026-10-21")
        self.assertEqual(sumar_dias_habiles("2026-10-09", 1), "2026-10-12")  # viernes -> lunes

    def test_fondo_30_corridos_desde_la_notificacion(self):
        r = parsear_vista(FONDO_2B)
        self.assertEqual(vencimiento_desde_texto(r, "2026-10-07", "2026-10-05"), ("2026-11-06", False))
        self.assertEqual(vencimiento_desde_texto(r, "2026-10-14", None), ("2026-11-13", False))

    def test_formal_10_habiles(self):
        self.assertEqual(vencimiento_desde_texto(parsear_vista(FORMAL), "2026-10-07", None), ("2026-10-21", False))

    def test_con_los_dos_plazos_se_toma_el_que_vence_antes(self):
        r = parsear_vista(FONDO_2A + " " + FORMAL)
        self.assertEqual(vencimiento_desde_texto(r, "2026-10-07", None), ("2026-10-21", False))

    def test_sin_notificacion_parte_de_la_vista_y_queda_estimado(self):
        r = parsear_vista(FONDO_2B)
        self.assertEqual(vencimiento_desde_texto(r, None, "2026-10-05"), ("2026-11-04", True))

    def test_sin_plazo_no_hay_vencimiento(self):
        self.assertEqual(vencimiento_desde_texto(parsear_vista("nada"), "2026-10-07", None), (None, False))


class Grilla(unittest.TestCase):
    def test_filas_de_vista_mas_nueva_primero_y_desde_la_fecha(self):
        archivos = [
            {"Indice": "Recibo de Ingreso", "Fecha": "28/09/2026"},
            {"Indice": "Vista de Marcas", "Fecha": "05/10/2026", "NombreGde": "IF-1"},
            {"Indice": "Vista de Marcas", "Fecha": "12/10/2026", "NombreGde": "IF-2"},
            {"Indice": "Vista de Marcas", "Fecha": "01/01/2026", "NombreGde": "IF-0"},
        ]
        self.assertEqual([f["NombreGde"] for f in filas_de_vista(archivos, "2026-09-28")], ["IF-2", "IF-1"])

    def test_descargar_sin_ruta_devuelve_vacio_sin_red(self):
        self.assertEqual(descargar_texto_pdf(None, {"Indice": "Vista de Marcas"}), "")


if __name__ == "__main__":
    unittest.main()
