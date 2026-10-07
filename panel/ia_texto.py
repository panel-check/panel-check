"""
«Mejorar texto» con IA: reescribe el análisis de marca con mejor redacción sin
cambiar su contenido.

Habla con cualquier servicio que use el formato «chat completions» de OpenAI
(el estándar que copian casi todos), así que el proveedor se cambia solo con
variables de entorno, sin tocar el código:

  IA_API_KEY   la clave del proveedor (sin ella el botón queda deshabilitado)
  IA_API_URL   dirección base. Por defecto Groq: https://api.groq.com/openai/v1
               (OpenRouter: https://openrouter.ai/api/v1 · Together:
               https://api.together.xyz/v1 · Ollama propio: http://host:11434/v1)
  IA_MODEL     nombre del modelo. Por defecto llama-3.3-70b-versatile (Llama de
               código abierto, servido por Groq)

Qué se manda: SOLO el texto que escribió el equipo en el cuadro de análisis (nada
de CUIT, titular ni datos de contacto). Qué NO hace: no guarda nada ni cambia el
texto por su cuenta; devuelve una propuesta que la persona acepta o descarta.

Guardas: se avisa si en la versión mejorada falta algún número del original
(actas, años, fechas) o aparece uno nuevo, porque un modelo puede inventar o
comerse un dato sin darse cuenta.
"""

import os
import re

import requests

URL_POR_DEFECTO = "https://api.groq.com/openai/v1"
MODELO_POR_DEFECTO = "llama-3.3-70b-versatile"
TIMEOUT = 60
LARGO_MIN = 20

PROMPT_SISTEMA = (
    "Sos un editor de textos de un estudio de propiedad industrial de Argentina. "
    "Te paso un análisis de marca escrito por el equipo. Mejorá su redacción: claridad, ortografía, "
    "puntuación, orden de las ideas y un tono profesional (español de Argentina, formal, sin exagerar). "
    "Reglas estrictas:\n"
    "1. No agregues hechos, nombres, números de acta, fechas, clases, normas, jurisprudencia ni conclusiones "
    "que no estén en el texto. No inventes nada.\n"
    "2. No quites información: todo dato del original tiene que seguir en la versión mejorada.\n"
    "3. Conservá tal cual los nombres de marcas y de personas o empresas (con sus mayúsculas), y todos los números.\n"
    "4. Conservá la estructura: los párrafos se separan con una línea en blanco y las viñetas empiezan con «- ».\n"
    "5. Si algo del original es ambiguo, dejalo ambiguo; no lo interpretes.\n"
    "Devolvé SOLO el texto mejorado: sin comentarios, sin títulos nuevos, sin comillas, sin explicaciones."
)

RE_NUMERO = re.compile(r"\d[\d./-]*\d|\d")


class ErrorIA(Exception):
    """Falla de la IA con un mensaje apto para mostrarle a la persona."""


def configurada() -> bool:
    return bool((os.environ.get("IA_API_KEY") or "").strip())


def _config():
    url = (os.environ.get("IA_API_URL") or URL_POR_DEFECTO).strip().rstrip("/")
    modelo = (os.environ.get("IA_MODEL") or MODELO_POR_DEFECTO).strip()
    return url, modelo, (os.environ.get("IA_API_KEY") or "").strip()


def _numeros(texto: str) -> set:
    """Los números del texto, sin puntos ni guiones de formato (4.762.345 = 4762345)."""
    return {re.sub(r"[./-]", "", n) for n in RE_NUMERO.findall(texto or "")}


def advertencias(original: str, mejorado: str) -> list:
    """Avisos para revisar a mano: números que desaparecieron o aparecieron."""
    antes, despues = _numeros(original), _numeros(mejorado)
    avisos = []
    perdidos = sorted(antes - despues)
    nuevos = sorted(despues - antes)
    if perdidos:
        avisos.append("En la versión mejorada falta: " + ", ".join(perdidos[:8]) + " (estaba en tu texto).")
    if nuevos:
        avisos.append("La versión mejorada agregó: " + ", ".join(nuevos[:8]) + " (no estaba en tu texto): revisá que sea correcto.")
    return avisos


def _limpiar(respuesta: str) -> str:
    t = (respuesta or "").strip()
    # Algunos modelos «razonadores» devuelven su razonamiento entre <think>…</think>.
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
    t = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", t).strip()
    if len(t) >= 2 and t[0] in "\"«“" and t[-1] in "\"»”":
        t = t[1:-1].strip()
    return t


def _detalle_error(r) -> str:
    """El motivo que informa el proveedor (sin datos sensibles: solo su mensaje de error),
    para que se pueda diagnosticar desde la pantalla. Vacío si no lo informa."""
    try:
        e = r.json().get("error")
        msg = e.get("message") if isinstance(e, dict) else e
    except (ValueError, AttributeError, TypeError):
        return ""
    msg = " ".join(str(msg or "").split())
    return f" Detalle del proveedor: «{msg[:300]}»" if msg else ""


def mejorar_texto(texto: str) -> dict:
    """Devuelve {"texto": versión mejorada, "advertencias": [...], "modelo": nombre}.
    Levanta ErrorIA con un mensaje claro si no se pudo."""
    if not configurada():
        raise ErrorIA("La IA no está configurada: falta la variable IA_API_KEY en el servidor del panel.")
    texto = (texto or "").replace("\r\n", "\n").strip()
    if len(texto) < LARGO_MIN:
        raise ErrorIA("Escribí un poco más de texto para poder mejorarlo.")
    url, modelo, clave = _config()
    try:
        r = requests.post(
            f"{url}/chat/completions",
            headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"},
            json={
                "model": modelo,
                "temperature": 0.2,
                "messages": [{"role": "system", "content": PROMPT_SISTEMA},
                             {"role": "user", "content": texto}],
            },
            timeout=TIMEOUT,
        )
    except requests.Timeout:
        raise ErrorIA("La IA tardó demasiado en responder. Probá de nuevo en un momento.")
    except requests.RequestException:
        raise ErrorIA("No se pudo conectar con el servicio de IA. Probá de nuevo en un momento.")
    detalle = _detalle_error(r)
    if r.status_code == 429:
        raise ErrorIA("La IA llegó a su límite de uso por ahora. Probá de nuevo en un minuto." + detalle)
    if r.status_code in (401, 403):
        raise ErrorIA("El servicio de IA rechazó la clave (IA_API_KEY). Hay que revisarla en el servidor." + detalle)
    if r.status_code == 404:
        raise ErrorIA("El servicio de IA no encontró ese modelo (IA_MODEL) o esa dirección (IA_API_URL), "
                      "o la cuenta no tiene acceso a ese modelo." + detalle)
    if not r.ok:
        raise ErrorIA(f"El servicio de IA respondió con un error ({r.status_code})." + detalle)
    try:
        crudo = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ErrorIA("El servicio de IA devolvió una respuesta que no se entiende.")
    mejorado = _limpiar(crudo)
    if not mejorado:
        raise ErrorIA("La IA no devolvió ningún texto. Probá de nuevo.")
    return {"texto": mejorado, "advertencias": advertencias(texto, mejorado), "modelo": modelo}
