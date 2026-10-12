"""
Arma wordpress/verificador-pagina-completa.html: la página entera del verificador en UN solo archivo para pegar en
WordPress (título + herramienta + textos con preguntas frecuentes).

Une, en este orden:
  wordpress/verificador-pagina-hero.html       título y bajada (con el marcador <!--HERRAMIENTA-->)
  wordpress/verificador-buscador.html          el buscador (no es el formulario del home)
  wordpress/verificador-pagina-contenido.html  explicaciones, estados, preguntas frecuentes

Hay que volver a correrlo cada vez que se cambie alguno de esos tres archivos:
    python3 scripts/armar_pagina_verificador.py
(scripts/test_paginas_wordpress.py avisa si el archivo armado quedó desactualizado.)
"""

import re
from pathlib import Path

WP = Path(__file__).resolve().parent.parent / "wordpress"

ENCABEZADO = """<!--
  PÁGINA COMPLETA DEL VERIFICADOR — smartiesconsultora.com.ar/consulta-marca-inpi/
  (archivo ARMADO: no se edita a mano; se cambian los archivos de origen y se corre scripts/armar_pagina_verificador.py)

  CÓMO PONERLA (WordPress + Elementor), son 4 pasos:
    1. Páginas → Añadir nueva → título «Consulta de marcas INPI» (dirección /consulta-marca-inpi/).
    2. Ajustes de la página → Diseño → plantilla «Elementor ancho completo».
    3. Editar con Elementor → un contenedor a ANCHO COMPLETO, con margen y relleno en 0 → un widget «HTML» → pegar TODO este archivo.
       Ya trae el título (H1), la herramienta y los textos: no hay que agregar nada más.
    4. Plugin de SEO (Yoast / Rank Math): título, descripción y frase clave de docs/seo-verificador.md. Esta página SÍ se indexa.
  Después: Search Console → Inspección de URL → Solicitar indexación.
-->
"""


def sin_comentario_inicial(texto: str) -> str:
    return re.sub(r"\A\s*<!--.*?-->\s*", "", texto, count=1, flags=re.S)


def armar() -> str:
    leer = lambda n: (WP / n).read_text(encoding="utf-8")
    hero = sin_comentario_inicial(leer("verificador-pagina-hero.html"))
    herramienta = sin_comentario_inicial(leer("verificador-buscador.html"))
    contenido = sin_comentario_inicial(leer("verificador-pagina-contenido.html"))
    if "<!--HERRAMIENTA-->" not in hero:
        raise SystemExit("Falta el marcador <!--HERRAMIENTA--> en verificador-pagina-hero.html")
    return ENCABEZADO + hero.replace("<!--HERRAMIENTA-->", herramienta.strip()) + "\n" + contenido


if __name__ == "__main__":
    destino = WP / "verificador-pagina-completa.html"
    destino.write_text(armar(), encoding="utf-8")
    print(f"Listo: {destino} ({destino.stat().st_size} bytes)")
