# SEO del verificador de marcas

Qué buscamos posicionar, con qué páginas y qué falta confirmar. Las páginas viven en `wordpress/` (se pegan en Elementor, cada archivo explica cómo).

## Idea

Quien busca «consulta marca inpi» hoy encuentra primero las páginas del propio INPI, que responden a una persona que ya sabe qué es una acta o una clase. Nosotros no vamos a ganarle a INPI por autoridad, pero sí podemos responder mejor a la intención real: **«¿mi nombre está libre y qué pasa con las marcas parecidas?»**. Por eso la página es una herramienta que se usa sin dejar datos, y los textos explican lo que INPI no explica (estados, clases, qué hacer con marcas parecidas).

## Páginas

| Página | Dirección | Se indexa | Archivo |
|---|---|---|---|
| Verificador (herramienta + contenido) | `/consulta-marca-inpi/` | Sí | `verificador-home.html` (herramienta) + `verificador-pagina-contenido.html` |
| Guía de estados | `/estados-de-una-marca/` | Sí | `guia-estados-marca.html` |
| Resultado | `/resultado/` | **No** (noindex en el plugin de SEO) | `landing-resultado.html` |
| Home | `/` | Sí | herramienta `verificador-home.html` |

La herramienta es la misma en el home y en `/consulta-marca-inpi/`. Las dos mandan a `/resultado/`.

## Mapa de palabras

Son hipótesis por intención de búsqueda, **no volúmenes medidos** (ver «Pendiente»).

| Intención | Búsquedas probables | Página |
|---|---|---|
| Principal | consulta marca inpi · consulta de marcas inpi · buscar marca inpi | `/consulta-marca-inpi/` |
| Disponibilidad | verificar marca · ver si una marca está registrada · cómo saber si una marca está registrada · buscador de marcas argentina | `/consulta-marca-inpi/` |
| Estados | marca en trámite · marca publicada inpi · marca concedida · marca denegada · qué es el preboletín | `/estados-de-una-marca/` |
| Más adelante | cómo registrar una marca · oposición de marca · estado de trámite por acta | páginas nuevas (no incluidas) |

## Etiquetas para el plugin de SEO

**`/consulta-marca-inpi/`**
- Frase clave: `consulta marca inpi`
- Título SEO (≈60 caracteres): `Consulta de marcas INPI: verificá si tu marca está libre`
- Meta descripción (≈155): `Buscá tu marca en la base del INPI gratis y sin dejar tus datos. Mirá qué marcas parecidas hay, en qué clase y en qué estado está cada una.`
- H1: `Consulta de marcas INPI: verificá si tu nombre está disponible`
- Preguntas frecuentes con datos estructurados (FAQPage) ya incluidas.

**`/estados-de-una-marca/`**
- Frase clave: `estados de una marca inpi`
- Título SEO: `Estados de una marca en el INPI: qué significa cada uno`
- Meta descripción: `Marca en trámite, publicada, con oposición, concedida o denegada: qué significa cada estado en el INPI y qué implica si querés registrar tu nombre.`
- H1: `Estados de una marca en el INPI: qué significa cada uno`

**`/resultado/`**: noindex (cada resultado es personal).

## Cómo está resuelto lo técnico

- Un solo H1 por página (lo pone Elementor en el encabezado); el contenido usa H2/H3.
- Las preguntas frecuentes están en el HTML visible **y** en el JSON-LD del final: tienen que coincidir. `scripts/test_paginas_wordpress.py` lo comprueba.
- Enlaces internos: el verificador ↔ la guía de estados; el resultado enlaza la guía (`GUIA_URL`); la home enlaza el verificador.
- Los textos no llevan precios ni plazos del trámite (se desactualizan). El plazo de oposición no se menciona con número.
- Después de publicar: Search Console → Inspección de URL → Solicitar indexación, y agregar las dos direcciones al sitemap (el plugin lo hace solo).
- La búsqueda se hace con JavaScript contra el panel; Google indexa el texto de la página, no los resultados de cada búsqueda (que además no deben indexarse).

## Pendiente de confirmar

1. **Res. INPI 297/2026** (solicitudes desde el 1/3/2026): cambia el procedimiento de oposición. Antes de escribir una guía de oposición, o de mencionar plazos, verificar en el Boletín Oficial. Hoy el sistema usa 30 días desde la publicación (`PLAZO_OPOSICION_DIAS`); si cambia, se cambia ahí y en `estado_marca.py`.
2. **Palabras que usa INPI en «Estado»**: correr el workflow «Probar verificador de marca (manual)» con algunas marcas y mirar «Estados vistos». Lo que no reconocemos sale como «otro» con el texto de INPI; si aparecen estados frecuentes, se agregan a `estado_marca.py`.
3. **Cómo nos presentamos**: los textos dicen «nuestro equipo». No se afirma que seamos agentes de la propiedad industrial; si querés decirlo, confirmar el término correcto con Pame.
4. **Volúmenes de búsqueda reales**: hace falta Google Keyword Planner (requiere una cuenta de Google Ads, no hace falta pagar anuncios) o Search Console con la página ya publicada. Con eso se ajusta el mapa de palabras. No se midió.
5. **Plazos y costos**: no figuran en las páginas a propósito. Si se agregan, ponerles fecha y revisarlos.

## Cómo medir

- Search Console: consultas y posición de `/consulta-marca-inpi/` y `/estados-de-una-marca/` (a las 4–8 semanas de indexadas).
- Panel → Más → Consultas web: búsquedas hechas y cuántas dejaron sus datos (el filtro «Contacto» separa «dejaron sus datos» de «solo buscaron»).
- Tasa de contacto = consultas con datos ÷ búsquedas totales; es el número a mejorar con los textos del resultado.
