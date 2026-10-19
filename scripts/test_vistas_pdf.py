"""
Pruebas de vistas_pdf.py (sin red). Correr con:
    cd scripts && python3 -m unittest test_vistas_pdf -v

Los textos son los de las vistas reales de INPI del 17/10/2026 (actas 4797016, 4797046,
4797082 y la vista formal de logotipos), tal cual salen del PDF: palabras pegadas y
bloques mezclados.
"""

import unittest

from vistas_pdf import (
    descargar_texto_pdf, filas_de_vista, parsear_vista, plazo_legible, problemas_de_lectura,
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


# Textos reales del 17/10/2026 (recortados a lo que se vio en el log de la corrida; el resto del PDF no se conoce).
PARIS_6TER = (
    'Visto el expediente bajo análisis se notifica al solicitante que: El término "PATAGONIA" ha obtenido el reconocimiento el 30/9/2022 '
    'como Símbolo/Emblema de Estado Nacional en organismo pertinente para nuestro país conforme Tratado Internacional art. 6 Ter Convenio '
    'Unión de Paris, por lo que se encontraría vedado el registro para otro titular. En virtud de ello se corre vista a los fines que estime '
    'corresponder bajo apercibimiento que, en caso de falta de respuesta o respuesta que no supere la ob'
)
PARIS_6BIS = (
    'Visto el expediente bajo análisis se notifica al solicitante que: El signo bajo análisis podría constituir la reproducción, imitación o '
    'traducción, -susceptibles de crear confusión- de una marca notoriamente conocida, a saber "NIKE" motivo por el cual deberá expresar las '
    'razones que hagan a su derecho, dentro del plazo que más abajo se indica. Conforme al Convenio de París (Aprobado por la Ley 17.011), '
    'artículo 6bis, ap1 "Los países de la Unión se comprometen, bien de oficio, si la '
)
CLASIFICACION = (
    'Se le hace saber al solicitante que deberá presentar escrito limitando correctamente los productos/servicios a distinguir, toda vez que '
    'la expresión "STOCK" resulta imprecisa a los fines de una correcta descripción de los productos/servicios que quiere proteger. Por tanto '
    'es necesario que especifique que productos incluye tal término. Se la hace saber también que deberá eliminar los siguientes productos/ '
    'servicios por no corresponder a la clase solicitada: "FACTURACIÓN; ADMINISTRACIÓN DE NEGOCIO'
)


# Formato «Dese vista…» (actas 4800811 ANIMA y 4802093 SOCIALMENTE, corrida del 18/10/2026). Reconstruido a
# partir de lo que se vio en el panel: antecedente con denominación y la providencia con el artículo escrito.
DESE_VISTA = (
    "acta_antecedente: 4632039; denom: ANIMA. . Dese vista al interesado de lo establecido en el artículo 3, "
    "inciso a), de la Ley 22.362 por el plazo de 30 días corridos. NOTIFÍQUESE. FERREIRA LIA CAROLINA - 27230862759"
)


class DeseVista(unittest.TestCase):
    def test_articulo_escrito_con_palabras(self):
        r = parsear_vista(DESE_VISTA)
        self.assertEqual((r["tipo"], r["articulos"]), ("fondo", ["Art. 3 inc. a)"]))
        self.assertEqual(r["plazos"], [(30, "corridos")])

    def test_antecedente_no_arrastra_el_resto_del_texto(self):
        self.assertEqual(parsear_vista(DESE_VISTA)["antecedentes"], ["4632039 (ANIMA)"])

    def test_sin_antecedente_tambien_se_reconoce(self):
        r = parsear_vista("Dese vista al interesado de lo establecido en el artículo 2, inciso b) de la Ley 22.362. NOTIFÍQUESE.")
        self.assertEqual((r["tipo"], r["articulos"], r["antecedentes"]), ("fondo", ["Art. 2 inc. b)"], []))

    def test_la_forma_abreviada_sigue_andando(self):
        self.assertEqual(parsear_vista(FONDO_3A_ANTECEDENTE)["antecedentes"], ["4011896 (FELICIANO)"])
        self.assertEqual(parsear_vista(FONDO_3A_ANTECEDENTE)["articulos"], ["Art. 3 inc. a)"])

    def test_el_decreto_no_se_confunde_con_la_ley(self):
        self.assertEqual(parsear_vista("conforme al artículo 12, inciso a) del decreto 242/19")["articulos"], [])


# Textos reales de la corrida del 18/10/2026 (recortados al comienzo que muestra el log).
PLAZO_30 = (" La presente vista se notifica por 30 días corridos de acuerdo a la Resolución INPI P 123/2019, anexo, artículo 5°, "
            "pudiendo hacer uso el solicitante de las extensiones automáticas previstas en el artículo 6° de la misma norma, "
            "abonando el arancel correspondiente (Cod. 770.001). NOTIFÍQUESE")
DOS_INCISOS = "Dese vista al interesado de lo establecido en el artículo 2°, incisos a) y b), de la Ley 22.362. Notifíquese." + PLAZO_30
INC_E_Y_ART_4 = "Dese vista al interesado de lo establecido en los artículos 3º inc. e) y 4º de la Ley 22.362. Notifíquese." + PLAZO_30
GRADO_Y_DASH = ("acta_antecedente: 4794747 – denom: ALBOR. Dese vista al interesado de lo establecido en el artículo 3°, inciso a), "
                "de la Ley 22.362. Notifíquese." + PLAZO_30)
ART_MINUSCULA = ("acta_antecedente: 4794746 - denom: ALBOR. En virtud de encontrarse alcanzada por la prohibición del art. 3 inc. a) "
                 "de la Ley 22.362, cúrsese vista al solicitante a efectos de que manifieste lo que estime corresponder a su derecho, "
                 "bajo apercibimiento de denegatoria. PEREYRA DIEGO ARIEL - 20292482478")
OFENSIVOS = ("Dese vista al interesado de lo establecido en el artículo 3°, inciso e) y 4° de la Ley 22.362 (DI-2024-141-APN-DNM#INPI – "
             "Disposición 141/24: signos alta o manifiestamente ofensivos). Notifíquese." + PLAZO_30)


class FormatosReales(unittest.TestCase):
    def test_dos_incisos_juntos(self):
        r = parsear_vista(DOS_INCISOS)
        self.assertEqual((r["tipo"], r["articulos"], r["plazos"]), ("fondo", ["Art. 2 inc. a)", "Art. 2 inc. b)"], [(30, "corridos")]))

    def test_inciso_y_otro_articulo(self):
        self.assertEqual(parsear_vista(INC_E_Y_ART_4)["articulos"], ["Art. 3 inc. e)", "Art. 4"])
        self.assertEqual(parsear_vista(OFENSIVOS)["articulos"], ["Art. 3 inc. e)", "Art. 4"])

    def test_grado_y_guion_largo_en_el_antecedente(self):
        r = parsear_vista(GRADO_Y_DASH)
        self.assertEqual((r["tipo"], r["articulos"], r["antecedentes"]), ("fondo", ["Art. 3 inc. a)"], ["4794747 (ALBOR)"]))

    def test_art_en_minuscula_sin_plazo(self):
        r = parsear_vista(ART_MINUSCULA)
        self.assertEqual((r["tipo"], r["articulos"], r["antecedentes"], r["plazos"]), ("fondo", ["Art. 3 inc. a)"], ["4794746 (ALBOR)"], []))

    def test_el_articulo_de_la_resolucion_no_se_cuela(self):
        # «artículo 5°» y «artículo 6°» son de la Resolución P 123/2019, no de la Ley
        for t in (DOS_INCISOS, GRADO_Y_DASH, OFENSIVOS):
            self.assertFalse([a for a in parsear_vista(t)["articulos"] if a in ("Art. 5", "Art. 6")])

    def test_letra_en_minuscula_sin_parentesis(self):
        # acta 4805739: «porel/ los ART. 3 j dela Ley 22.362»
        t = FONDO_2B.replace("ART. 2 Bdela", "ART. 3 j dela")
        self.assertIn("ART. 3 j dela Ley", t)
        r = parsear_vista(t)
        self.assertEqual((r["tipo"], r["articulos"], r["plazos"]), ("fondo", ["Art. 3 inc. j)"], [(30, "corridos")]))

    def test_articulo_sin_letra_sigue_sin_inventarla(self):
        r = parsear_vista(FONDO_2B.replace("ART. 2 Bdela", "ART. 3 dela"))
        self.assertEqual(r["articulos"], ["Art. 3"])
        r = parsear_vista(FONDO_2B.replace("ART. 2 Bdela", "ART. 3 de la"))
        self.assertEqual(r["articulos"], ["Art. 3"])

    def test_cursese_vista_sin_plazo_no_es_un_problema(self):
        r = parsear_vista(ART_MINUSCULA)
        self.assertTrue(r["sin_plazo_escrito"])
        self.assertEqual(problemas_de_lectura(r), [])
        self.assertFalse(parsear_vista(FONDO_2B)["sin_plazo_escrito"])

    def test_motivo_sin_puntos(self):
        r = parsear_vista('Convenio de París artículo 6bis marca notoriamente conocida, a saber "...........RAPSODIA............" otro')
        self.assertEqual(r["motivo"], "RAPSODIA")


class ProblemasDeLectura(unittest.TestCase):
    def test_una_vista_clara_no_da_problemas(self):
        for t in (FONDO_2B, FONDO_3A_ANTECEDENTE, FORMAL, DESE_VISTA):
            self.assertEqual(problemas_de_lectura(parsear_vista(t)), [], t[:40])

    def test_pdf_ilegible(self):
        self.assertEqual(len(problemas_de_lectura(None)), 1)
        self.assertEqual(len(problemas_de_lectura({})), 1)

    def test_texto_desconocido_sin_plazo(self):
        p = problemas_de_lectura(parsear_vista("texto raro que no dice nada"))
        self.assertEqual(len(p), 2)  # no se reconoce el tipo + no hay plazo

    def test_fondo_sin_plazo_y_sin_cursese_se_avisa(self):
        r = parsear_vista("Dese vista al interesado de lo establecido en el artículo 3, inciso a), de la Ley 22.362.")
        self.assertEqual(r["tipo"], "fondo")
        self.assertEqual(problemas_de_lectura(r), ["no se encontró el plazo para contestar"])

    def test_antecedente_con_texto_de_mas(self):
        r = parsear_vista(FONDO_3A_ANTECEDENTE)
        r["antecedentes"] = ["4632039 (" + "x" * 100 + ")"]
        self.assertEqual(problemas_de_lectura(r), ["la marca anterior parece traer texto de más"])


class TiposNuevos(unittest.TestCase):
    def test_convenio_de_paris_6_ter_emblema_de_estado(self):
        r = parsear_vista(PARIS_6TER)
        self.assertEqual((r["tipo"], r["articulos"], r["motivo"]), ("convenio_paris", ["Convenio de París, art. 6 ter"], "PATAGONIA"))

    def test_convenio_de_paris_6_bis_marca_notoria(self):
        r = parsear_vista(PARIS_6BIS)
        self.assertEqual((r["tipo"], r["articulos"], r["motivo"]), ("convenio_paris", ["Convenio de París, art. 6 bis"], "NIKE"))
        # "Ley 17.011" (el Convenio) no se confunde con un artículo de la Ley 22.362
        self.assertFalse([a for a in r["articulos"] if a.startswith("Art.")])

    def test_formal_de_clasificacion_no_es_de_logotipos(self):
        r = parsear_vista(CLASIFICACION)
        self.assertEqual((r["tipo"], r["motivo"]), ("clasificacion", "STOCK"))

    def test_los_plazos_se_leen_igual_en_los_tipos_nuevos(self):
        r = parsear_vista(PARIS_6TER + " La presente vista se notifica por 30 días corridos de acuerdo a la Resolución INPI P 123/2019")
        self.assertEqual((r["tipo"], r["plazos"]), ("convenio_paris", [(30, "corridos")]))
        r = parsear_vista(CLASIFICACION + " Notifíquese: por el plazo de 10 (diez) días hábiles bajo apercibimiento de dictar resolución denegatoria")
        self.assertEqual((r["tipo"], r["plazos"]), ("clasificacion", [(10, "habiles")]))

    def test_los_logotipos_siguen_siendo_formal(self):
        self.assertEqual(parsear_vista(FORMAL)["tipo"], "formal")
        self.assertEqual(parsear_vista(FORMAL)["motivo"], "")


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
