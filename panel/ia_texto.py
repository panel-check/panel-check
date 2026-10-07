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
de CUIT, titular ni datos de contacto). Para «Agendar con IA» (calendario) se manda
únicamente el texto que la persona pega en ese cuadro. Qué NO hace: no guarda nada ni cambia el
texto por su cuenta; devuelve una propuesta que la persona acepta o descarta.

Guardas: se avisa si en la versión mejorada falta algún número del original
(actas, años, fechas) o aparece uno nuevo, porque un modelo puede inventar o
comerse un dato sin darse cuenta.
"""

import json
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


def _pedir(mensajes: list, temperatura: float = 0.2) -> str:
    """Una consulta al proveedor (formato «chat completions»). Devuelve el texto crudo de la respuesta.
    Levanta ErrorIA con un mensaje claro si no se pudo."""
    if not configurada():
        raise ErrorIA("La IA no está configurada: falta la variable IA_API_KEY en el servidor del panel.")
    url, modelo, clave = _config()
    try:
        r = requests.post(
            f"{url}/chat/completions",
            headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"},
            json={"model": modelo, "temperature": temperatura, "messages": mensajes},
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
        return r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ErrorIA("El servicio de IA devolvió una respuesta que no se entiende.")


def mejorar_texto(texto: str) -> dict:
    """Devuelve {"texto": versión mejorada, "advertencias": [...], "modelo": nombre}.
    Levanta ErrorIA con un mensaje claro si no se pudo."""
    if not configurada():
        raise ErrorIA("La IA no está configurada: falta la variable IA_API_KEY en el servidor del panel.")
    texto = (texto or "").replace("\r\n", "\n").strip()
    if len(texto) < LARGO_MIN:
        raise ErrorIA("Escribí un poco más de texto para poder mejorarlo.")
    crudo = _pedir([{"role": "system", "content": PROMPT_SISTEMA}, {"role": "user", "content": texto}])
    mejorado = _limpiar(crudo)
    if not mejorado:
        raise ErrorIA("La IA no devolvió ningún texto. Probá de nuevo.")
    return {"texto": mejorado, "advertencias": advertencias(texto, mejorado), "modelo": _config()[1]}


# ── Agendar desde un texto pegado ────────────────────────────────────
# La IA solo EXTRAE datos del texto (no decide nada ni agenda): el panel valida cada dato
# (calendario_core.armar_propuesta) y la persona confirma en el formulario.
PROMPT_AGENDA = (
    "Sos un asistente de un estudio de propiedad industrial de Argentina. Te paso un texto pegado por el equipo "
    "con los datos de una reunión, llamada o videollamada para agendar. Extraé los datos y devolvé SOLO un objeto "
    "JSON (sin explicaciones, sin comillas de bloque de código) con exactamente estas claves:\n"
    '{"negocio": string|null, "acta": string|null, "fecha": "AAAA-MM-DD"|null, "hora": "HH:MM"|null, '
    '"duracion_min": number|null, "modalidad": "meet"|"llamada"|"presencial"|null, "email": string|null, '
    '"telefono": string|null, "lugar": string|null, "notas": string|null}\n'
    "Reglas estrictas:\n"
    "1. Si un dato NO está en el texto, poné null. No inventes ni supongas nada: es preferible null a adivinar.\n"
    "2. «negocio»: el nombre de la marca o negocio tal como está escrito (con sus mayúsculas y símbolos).\n"
    "3. «acta»: solo los dígitos del número de acta.\n"
    "4. «fecha»: la fecha ya resuelta en formato AAAA-MM-DD. Hoy es {hoy} ({dia}). Si el texto no dice el año, "
    "usá la próxima vez que llegue esa fecha (nunca una fecha pasada). Para «mañana», «el viernes», «el lunes que "
    "viene», etc., calculala desde hoy. Si el texto no dice ninguna fecha, null.\n"
    "5. «hora»: formato 24 horas HH:MM, hora de Argentina (9am = 09:00, 3 de la tarde = 15:00). Si no hay hora, null. "
    "Si dice «a la mañana» o «a la tarde» sin hora exacta, null.\n"
    "6. «modalidad»: «meet» si menciona Meet, Google Meet, videollamada o Zoom; «llamada» si es llamada o teléfono; "
    "«presencial» si es en persona o en un lugar. Si no lo dice, null.\n"
    "7. «duracion_min»: solo si el texto dice cuánto dura, en minutos; si no, null.\n"
    "8. «email»: el mail de la persona tal cual está escrito (si hay más de uno, separalos con coma). «telefono»: "
    "el teléfono tal cual. «lugar»: el lugar o la dirección si es presencial.\n"
    "9. «notas»: SOLO si hay algo más que el equipo deba tener presente (por ejemplo «traer DNI»), copiado casi "
    "textual y corto; si no, null. No repitas los datos de arriba.\n"
    "10. Las respuestas del equipo a preguntas anteriores aparecen al final del texto; usalas como parte del texto."
)


def _json_de(crudo: str):
    """El primer objeto JSON de la respuesta (algunos modelos agregan texto o bloques ```)."""
    t = _limpiar(crudo)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def extraer_agenda(texto: str, hoy: str, dia: str) -> dict:
    """Pide a la IA los datos de agenda del texto. Devuelve el diccionario crudo (sin validar).
    Levanta ErrorIA si no se pudo."""
    texto = (texto or "").replace("\r\n", "\n").strip()
    if len(texto) < 8:
        raise ErrorIA("Pegá o escribí un poco más de texto con los datos de la reunión.")
    crudo = _pedir(
        [{"role": "system", "content": PROMPT_AGENDA.replace("{hoy}", hoy).replace("{dia}", dia)},
         {"role": "user", "content": texto}],
        temperatura=0,
    )
    datos = _json_de(crudo)
    if datos is None:
        raise ErrorIA("La IA no devolvió datos que se entiendan. Probá de nuevo o completá el formulario a mano.")
    return datos
