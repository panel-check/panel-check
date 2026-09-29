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


def _bloque(texto: str, inicio: str, fin_alternativas: list) -> str:
    patron_inicio = re.escape(inicio)
    patron_fin = "|".join(re.escape(f) for f in fin_alternativas)
    m = re.search(f"{patron_inicio}(.*?)(?:{patron_fin}|$)", texto, re.S)
    return m.group(1).strip() if m else "(no encontrado)"


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

    texto = _texto_sin_tags(r.text)

    secciones = ["DATOS GENERALES", "TITULARIDAD", "GESTION DEL TRAMITE",
                 "PUBLICACION", "OPOSICIONES", "VISTAS Y NOTIFICACIONES",
                 "RESOLUCION", "DICTAMENES DE RECURSOS"]

    for i, nombre in enumerate(secciones):
        # match con o sin tilde (INPI a veces usa Ñ/Ó en mayúscula sin tilde en el texto plano)
        candidatos = [s2 for s2 in secciones if s2 != nombre]
        idx = texto.upper().find(nombre)
        if idx == -1:
            print(f"::notice::SECCION {nombre}: no encontrada")
            continue
        siguientes = [texto.upper().find(c, idx + len(nombre)) for c in candidatos]
        siguientes = [x for x in siguientes if x != -1]
        fin = min(siguientes) if siguientes else idx + 1500
        bloque = texto[idx:fin].strip()
        bloque = bloque[:900]  # límite prudente por annotation
        linea = bloque.replace("\n", " | ")
        print(f"::notice::SECCION {nombre}: {linea}")

    print(f"::notice::acta {acta} inspeccionada OK")


if __name__ == "__main__":
    main()
