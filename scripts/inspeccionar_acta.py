"""
Herramienta de diagnóstico (uso puntual, no forma parte del pipeline): baja
la página completa de /MarcasConsultas/Resultado para un acta puntual y
vuelca, como ::notice:: de GitHub Actions (para poder leerlo por la API sin
depender de blob storage — ver el mismo truco en backfill_fecha_publicacion.py),
el texto (sin tags HTML) de las secciones DATOS GENERALES y TITULARIDAD.

Por qué hace falta: ya tenemos regexes verificados para CARACTER/CUIT
(GESTION DEL TRAMITE / TITULARIDAD) y para TIPO/DISPOSICION/VENCE
(RESOLUCIÓN), pero nunca extrajimos denominación/clase/tipo de
marca/fecha de presentación/titular desde esta página (el pipeline normal
los saca del PDF del boletín, no de acá). Antes de escribir regexes a
ciegas, vemos el texto real.

Uso:
    python3 inspeccionar_acta.py 4534497
"""

import re
import sys

from validar_leads import BASE, crear_sesion, _get_con_reintentos


def _texto_sin_tags(html: str) -> str:
    texto = re.sub(r"<[^>]+>", " ", html)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n\s*\n+", "\n", texto)
    return texto.strip()


def _bloque_seccion(html: str, nombre: str) -> str:
    """El nombre de cada sección aparece dos veces en la página: una en la
    barra de tabs (sin contenido) y otra como título real de la sección,
    con el contenido en un <div> justo después de su </h4> — mismo patrón
    ya verificado para GESTION DEL TRAMITE (ver RE_GESTION en
    validar_leads.py). Devolvemos el ÚLTIMO match, que es el real."""
    patron = re.compile(
        re.escape(nombre) + r".*?</h4>\s*</div>\s*<div[^>]*>(.*?)</div>\s*</div>\s*</div>",
        re.S,
    )
    matches = patron.findall(html)
    if not matches:
        return "(no encontrado con el patrón de tabs; puede que la sección use otra estructura)"
    return _texto_sin_tags(matches[-1])


def main():
    if len(sys.argv) < 2:
        sys.exit("Uso: python3 inspeccionar_acta.py <acta>")
    acta = sys.argv[1]

    s = crear_sesion()
    r = _get_con_reintentos(
        lambda: s.post(
            f"{BASE}/MarcasConsultas/Resultado",
            headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
            data={"acta": acta},
            timeout=30,
        )
    )
    if "Web Page Blocked" in r.text or "Attack ID" in r.text:
        print(f"::error::acta {acta} bloqueada por el WAF de INPI")
        sys.exit(1)

    secciones = ["DATOS GENERALES", "TITULARIDAD", "GESTION DEL TRAMITE",
                 "PUBLICACION", "OPOSICIONES", "VISTAS Y NOTIFICACIONES",
                 "RESOLUCION", "DICTAMENES DE RECURSOS"]

    for nombre in secciones:
        bloque = _bloque_seccion(r.text, nombre)
        bloque = re.sub(r"\s+", " ", bloque).strip()[:900]  # límite prudente por annotation
        print(f"::notice::SECCION {nombre}: {bloque}")

    print(f"::notice::acta {acta} inspeccionada OK")


if __name__ == "__main__":
    main()
