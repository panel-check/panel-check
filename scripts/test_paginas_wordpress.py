"""
Chequeos de las páginas de WordPress (wordpress/*.html), sin red:

  - las preguntas frecuentes de la página del verificador están igual en el HTML visible y en el JSON-LD
    (si no coinciden, Google puede ignorar los datos estructurados);
  - el JSON-LD es JSON válido;
  - el selector de clases del home tiene 45 clases;
  - la landing, el home y la página de contenido usan la misma clave de sessionStorage y la misma dirección de la guía.

Uso: python3 -m unittest test_paginas_wordpress   (desde scripts/)
"""

import html
import json
import re
import unittest
from pathlib import Path

WP = Path(__file__).resolve().parent.parent / "wordpress"


def leer(nombre: str) -> str:
    return (WP / nombre).read_text(encoding="utf-8")


def texto_plano(fragmento: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragmento))).strip()


class PreguntasFrecuentes(unittest.TestCase):
    def setUp(self):
        self.src = leer("verificador-pagina-contenido.html")

    def jsonld(self):
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', self.src, re.S)
        self.assertIsNotNone(m, "falta el JSON-LD")
        return json.loads(m.group(1))

    def visibles(self):
        out = []
        for pregunta, respuesta in re.findall(
            r'<details><summary>(.*?)</summary>\s*<div class="smc-resp">(.*?)</div></details>', self.src, re.S
        ):
            out.append((texto_plano(pregunta), texto_plano(respuesta)))
        return out

    def test_jsonld_valido_y_es_faqpage(self):
        datos = self.jsonld()
        self.assertEqual(datos["@type"], "FAQPage")
        self.assertGreaterEqual(len(datos["mainEntity"]), 5)

    def test_html_y_jsonld_coinciden(self):
        en_json = [(q["name"], q["acceptedAnswer"]["text"]) for q in self.jsonld()["mainEntity"]]
        self.assertEqual(self.visibles(), en_json)


class HomeYLanding(unittest.TestCase):
    def test_buscador_tiene_45_clases(self):
        src = leer("verificador-buscador.html")
        m = re.search(r"var CLASES = \[(.*?)\];", src, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(len(re.findall(r'"[^"]+"', m.group(1))), 45)

    def test_misma_clave_de_sesion_y_tope_de_clases(self):
        home, landing = leer("verificador-buscador.html"), leer("landing-resultado.html")
        clave = lambda s: re.search(r'var PENDIENTE = "([^"]+)"', s).group(1)
        self.assertEqual(clave(home), clave(landing))
        self.assertEqual(re.search(r"var CLASES_MAX = (\d+)", home).group(1), "6")

    def test_la_guia_de_estados_tiene_una_sola_direccion(self):
        landing, contenido, guia = leer("landing-resultado.html"), leer("verificador-pagina-contenido.html"), leer("guia-estados-marca.html")
        direccion = re.search(r'var GUIA_URL = "([^"]+)"', landing).group(1)
        self.assertEqual(direccion, "/estados-de-una-marca/")
        self.assertIn(f'href="{direccion}"', contenido)
        self.assertIn("/consulta-marca-inpi/", guia)

    def test_ninguna_pagina_trae_precios_en_pesos(self):
        for nombre in ("verificador-pagina-contenido.html", "guia-estados-marca.html"):
            self.assertNotRegex(leer(nombre), r"\$\s?\d", nombre)


class PaginaArmada(unittest.TestCase):
    def test_la_pagina_completa_esta_al_dia(self):
        import armar_pagina_verificador as armar

        self.assertEqual(
            leer("verificador-pagina-completa.html"),
            armar.armar(),
            "Falta correr: python3 scripts/armar_pagina_verificador.py",
        )

    def test_la_pagina_completa_trae_un_solo_h1_y_una_sola_herramienta(self):
        src = leer("verificador-pagina-completa.html")
        self.assertEqual(src.count("<h1"), 1)
        self.assertEqual(src.count('id="smv"'), 1)
        self.assertEqual(src.count("application/ld+json"), 1)


if __name__ == "__main__":
    unittest.main()
