"""
Juez de la vigilancia marcaria con Claude (API de Anthropic).

El motor de similitud (similitud.py) es barato y no se le escapa nada, pero
con reglas fijas no puede juzgar la impresión de conjunto, cuál es el elemento
dominante de cada marca ni qué palabras son descriptivas del rubro. Por eso,
desde el 12/11/2026, cada par que el motor propone (marca del cliente contra
solicitud nueva) lo evalúa Claude como lo haría un examinador del INPI, y la
alerta solo se muestra si el veredicto alcanza la sensibilidad elegida para
esa marca (ficha de la marca → Vigilancia).

Dos pasos para que salga barato sin perder criterio:
  1. Haiku evalúa todos los pares.
  2. Los que Haiku marca como idéntica, confundible o cercana los vuelve a
     mirar Sonnet, y vale el veredicto de Sonnet.

Qué se le manda: solo datos públicos del boletín y del expediente (los dos
nombres, tipo, clases y su relación, cuán comunes son en la clase las
palabras que comparten y, desde el 13/11/2026, la protección/limitación de
productos y servicios de cada una). Nada del cliente (ni su nombre ni sus
datos de contacto).

Variables de entorno:
  ANTHROPIC_API_KEY     la clave (sin ella, la vigilancia funciona igual con
                        las reglas y el panel avisa que el juez está apagado)
  JUEZ_MODELO_RAPIDO    por defecto claude-haiku-5-5
  JUEZ_MODELO_FINO      por defecto claude-sonnet-5-5

Sin FastAPI: lo usan scripts/vigilancia.py, el panel y las pruebas.
"""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

URL = "https://api.anthropic.com/v1/messages"
VERSION_API = "2023-06-01"
MODELO_RAPIDO = "claude-haiku-5-5"
MODELO_FINO = "claude-sonnet-5-5"
HILOS = 8
TIMEOUT = 60

VEREDICTOS = ("identica", "confundible", "cercana", "no_confundible")
VEREDICTO_LEGIBLE = {"identica": "Idéntica", "confundible": "Confundible", "cercana": "Cercana",
                     "no_confundible": "No confundible"}

# Qué veredictos se muestran como alerta según la sensibilidad de cada marca.
SENSIBILIDADES = {
    "identicas": ("Solo idénticas", ("identica",)),
    "similares": ("Similares", ("identica", "confundible")),
    "amplia": ("Amplia", ("identica", "confundible", "cercana")),
}
SENSIBILIDAD_DEFAULT = "similares"

# Encabezado corto de cada clase de Niza (13.ª ed.), para que el juez sepa de qué rubro hablamos.
NIZA = {
    1: "productos químicos", 2: "pinturas y barnices", 3: "cosméticos, perfumes y productos de limpieza",
    4: "aceites industriales, combustibles y velas", 5: "productos farmacéuticos, veterinarios y suplementos",
    6: "metales y productos metálicos", 7: "máquinas y máquinas herramientas", 8: "herramientas de mano",
    9: "aparatos electrónicos y científicos, software", 10: "aparatos médicos",
    11: "iluminación, calefacción, refrigeración y aire acondicionado", 12: "vehículos y sus repuestos",
    13: "armas de fuego y pirotecnia", 14: "joyería y relojería", 15: "instrumentos musicales",
    16: "papel, imprenta y papelería", 17: "caucho, plásticos y aislantes", 18: "cuero, bolsos, valijas y paraguas",
    19: "materiales de construcción no metálicos", 20: "muebles", 21: "utensilios de cocina y del hogar",
    22: "cuerdas, redes y lonas", 23: "hilos", 24: "tejidos y ropa de cama y mesa", 25: "ropa, calzado y sombrerería",
    26: "mercería y adornos", 27: "alfombras y revestimientos de suelo", 28: "juegos, juguetes y artículos deportivos",
    29: "carne, lácteos, conservas y aceites comestibles", 30: "café, harinas, panadería, golosinas y condimentos",
    31: "productos agrícolas frescos y alimento para animales", 32: "cervezas y bebidas sin alcohol",
    33: "bebidas alcohólicas", 34: "tabaco y artículos para fumadores",
    35: "publicidad, gestión de negocios y venta minorista y mayorista", 36: "seguros, finanzas e inmobiliarias",
    37: "construcción, instalación y reparación", 38: "telecomunicaciones", 39: "transporte, logística y viajes",
    40: "tratamiento de materiales e impresión", 41: "educación, entretenimiento, deportes y cultura",
    42: "servicios tecnológicos y de software", 43: "restaurantes, bares y alojamiento",
    44: "servicios médicos, veterinarios, estética y agricultura", 45: "servicios jurídicos, de seguridad y personales",
}

PROMPT = """Sos un agente de la propiedad industrial de Argentina que hace vigilancia de marcas para un estudio. El sistema del estudio compara cada solicitud nueva publicada (o recién presentada) en el INPI con las marcas de sus clientes y te pasa un par: la MARCA DEL CLIENTE y la SOLICITUD NUEVA de un tercero. Tenés que decidir si vale la pena avisarle al cliente para que evalúe oponerse (art. 13 de la Ley 22.362: 30 días corridos desde la publicación).

Juzgá como un examinador del INPI:
1. Impresión de conjunto: compará las marcas completas, como las vería y oiría un consumidor argentino, no letra por letra.
2. Elemento dominante: identificá la parte distintiva de cada marca. Las palabras descriptivas del rubro (TURISMO o VIAJES en la clase 39, CAFE en la 43, PADEL en deportes, LAB en farmacia), las genéricas o de uso común (TODO, CASA, CLUB, GRUPO, MAS) y las formas societarias no dan exclusividad: si lo único que comparten es una de esas, no son confundibles. El sistema te dice cuántas solicitudes de la clase usaron en los últimos 18 meses cada palabra compartida: muchas apariciones = palabra común.
3. Parecido fonético en castellano rioplatense (C/S/Z, B/V, LL/Y suenan igual; H muda), gráfico y conceptual. Pronunciá cada marca entera: si en su conjunto suenan casi igual (SUSPENSYS / SUSPENCITY, NEXALABS / NOXALAB), son confundibles aunque compartan una raíz descriptiva.
4. Conceptual: una traducción o sinónimo cuenta solo si es evidente para el público argentino y afecta al elemento dominante (NET = RED, SUN = SOL sí). Asociaciones rebuscadas, juegos de palabras forzados o palabras que solo coinciden en otro idioma no cuentan (MANAR contra FLUX, TIMBEROS contra BOIS: no son confundibles).
5. Clases: misma clase pesa más; clase relacionada, algo menos; clase sin relación, solo si las marcas son idénticas o la del cliente es muy conocida.
6. Una marca corta e inventada (de fantasía) se confunde con menos diferencia que una formada por palabras comunes.
7. En marcas mixtas o figurativas solo ves el texto, no el logo: no supongas nada del dibujo.
8. Productos y servicios: si te paso la protección o limitación de cada marca (lo que figura en el expediente), comparalos como el INPI: idénticos, uno incluido en el otro («suplementos nutricionales» dentro de «suplementos alimenticios para personas»), relacionados (mismo canal o mismo público) o distintos. Productos idénticos o incluidos suben el riesgo aunque los nombres no sean idénticos; productos claramente distintos dentro de la misma clase lo bajan, salvo que las marcas sean idénticas o la del cliente sea muy conocida. «Toda la clase» cubre todo. Si falta el dato de alguna, juzgá con la clase. Mencioná los productos en la razón cuando pesen.

Veredictos posibles:
- "identica": mismo elemento distintivo, con diferencias mínimas (espacios, signos, plural, una palabra descriptiva agregada).
- "confundible": riesgo real de confusión del elemento dominante en la misma clase o una relacionada. Vale la pena avisar al cliente.
- "cercana": comparten algo relevante, pero las diferencias son claras; un agente lo miraría, aunque probablemente no se oponga.
- "no_confundible": lo que comparten es descriptivo, genérico o de uso común, el parecido es forzado o no hay parecido real.

Nivel de riesgo para el cliente: "alta", "media" o "baja".

Respondé SOLO con un objeto JSON, sin texto antes ni después:
{"veredicto": "identica|confundible|cercana|no_confundible", "nivel": "alta|media|baja", "dominante_cliente": "...", "dominante_nueva": "...", "razon": "una o dos oraciones cortas en castellano (máximo 50 palabras), concretas, para el equipo del estudio"}"""


class ErrorJuez(Exception):
    pass


def clave() -> str:
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def configurado() -> bool:
    return bool(clave())


def modelos():
    return ((os.environ.get("JUEZ_MODELO_RAPIDO") or MODELO_RAPIDO).strip(),
            (os.environ.get("JUEZ_MODELO_FINO") or MODELO_FINO).strip())


class Uso:
    """Tokens usados en una corrida (para el reporte y la estimación de costo)."""

    def __init__(self):
        self._l = threading.Lock()
        self.llamadas = 0
        self.entrada = 0
        self.salida = 0
        self.cache = 0
        self.errores = 0

    def sumar(self, u: dict):
        with self._l:
            self.llamadas += 1
            self.entrada += int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
            self.cache += int(u.get("cache_read_input_tokens") or 0)
            self.salida += int(u.get("output_tokens") or 0)

    def error(self):
        with self._l:
            self.errores += 1

    def resumen(self) -> dict:
        return {"llamadas_ia": self.llamadas, "tokens_entrada": self.entrada, "tokens_cache": self.cache,
                "tokens_salida": self.salida, "errores_ia": self.errores}


def pedir(sistema: str, texto: str, modelo: str, max_tokens: int = 1200, uso: Uso = None) -> str:
    """Una llamada a la API de mensajes. Reintenta ante límites o caídas. Levanta ErrorJuez."""
    k = clave()
    if not k:
        raise ErrorJuez("Falta ANTHROPIC_API_KEY")
    # Sin «temperature»: los modelos 5.x no la aceptan (HTTP 400 «temperature is deprecated»).
    cuerpo = {"model": modelo, "max_tokens": max_tokens,
              "system": [{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
              "messages": [{"role": "user", "content": texto}]}
    cab = {"x-api-key": k, "anthropic-version": VERSION_API, "content-type": "application/json"}
    ultimo = ""
    for intento in range(4):
        try:
            r = requests.post(URL, headers=cab, json=cuerpo, timeout=TIMEOUT)
        except requests.RequestException as e:
            ultimo = f"sin conexión ({type(e).__name__})"
            time.sleep(2 * (intento + 1))
            continue
        if r.status_code == 200:
            d = r.json()
            if uso:
                uso.sumar(d.get("usage") or {})
            return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        try:
            detalle = r.json().get("error", {}).get("message", "")
        except ValueError:
            detalle = r.text[:200]
        ultimo = f"HTTP {r.status_code}: {detalle}"
        if r.status_code in (429, 500, 502, 503, 504, 529):
            time.sleep(min(30, 3 * (intento + 1) ** 2))
            continue
        break
    if uso:
        uso.error()
    raise ErrorJuez(ultimo or "La API no respondió")


def _json_de(texto: str):
    """El primer objeto JSON de la respuesta (tolera texto alrededor y saltos de línea dentro de las comillas)."""
    texto = texto or ""
    i = texto.find("{")
    while i != -1:
        try:
            obj, _ = json.JSONDecoder(strict=False).raw_decode(texto[i:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = texto.find("{", i + 1)
    return None


def _clase(c) -> str:
    if c in (None, ""):
        return "sin dato"
    try:
        return f"{int(c)} ({NIZA.get(int(c), '?')})"
    except (TypeError, ValueError):
        return str(c)


TIPOS = {"D": "denominativa", "M": "mixta (con logo)", "F": "figurativa", "T": "tridimensional"}


def texto_par(par: dict) -> str:
    """El mensaje para el juez. `par` trae: marca_cliente, clases_cliente, tipo_cliente, marca_nueva,
    clase_nueva, tipo_nuevo, relacion ('misma'|'relacionada'|'distinta'|'desconocida'),
    motivos (los del motor) y compartidas [(palabra, apariciones en la clase)]."""
    clases_cli = ", ".join(_clase(c) for c in (par.get("clases_cliente") or [])) or "sin dato"
    rel = {"misma": "misma clase", "relacionada": "clases distintas pero relacionadas",
           "distinta": "clases sin relación", "desconocida": "relación de clases sin confirmar"}.get(par.get("relacion"), "")
    lineas = [
        f"MARCA DEL CLIENTE: «{par.get('marca_cliente') or ''}» · {TIPOS.get((par.get('tipo_cliente') or '')[:1].upper(), 'tipo sin dato')} · clase(s) {clases_cli}",
        f"SOLICITUD NUEVA: «{par.get('marca_nueva') or ''}» · {TIPOS.get((par.get('tipo_nuevo') or '')[:1].upper(), 'tipo sin dato')} · clase {_clase(par.get('clase_nueva'))}",
        f"Relación de clases: {rel}.",
    ]
    if par.get("productos_cliente"):
        lineas.append(f"Productos/servicios de la marca del cliente: {par['productos_cliente']}")
    if par.get("productos_nueva"):
        lineas.append(f"Productos/servicios de la solicitud nueva: {par['productos_nueva']}")
    if par.get("otros_nombres"):
        lineas.append(f"El cliente también vigila estos nombres: {par['otros_nombres']}.")
    comp = par.get("compartidas") or []
    if comp:
        lineas.append("Palabras que comparten y cuántas solicitudes de la clase de la solicitud nueva las usaron en los últimos 18 meses: "
                      + ", ".join(f"{p} ({n})" for p, n in comp) + ".")
    if par.get("motivos"):
        lineas.append(f"Por qué la marcó el sistema automático (puede estar equivocado): {par['motivos']}.")
    return "\n".join(lineas)


def _normalizar_respuesta(d: dict, modelo: str) -> dict:
    v = str(d.get("veredicto") or "").strip().lower().replace(" ", "_").replace("í", "i")
    if v not in VEREDICTOS:
        raise ErrorJuez(f"veredicto desconocido: {v!r}")
    n = str(d.get("nivel") or "").strip().lower()
    if n not in ("alta", "media", "baja"):
        n = {"identica": "alta", "confundible": "media"}.get(v, "baja")
    razon = " ".join(str(d.get("razon") or "").split())[:600]
    return {"veredicto": v, "nivel": n, "razon": razon, "modelo": modelo,
            "dominante_cliente": str(d.get("dominante_cliente") or "")[:80],
            "dominante_nueva": str(d.get("dominante_nueva") or "")[:80]}


def _evaluar_con(par: dict, modelo: str, uso: Uso = None) -> dict:
    crudo = pedir(PROMPT, texto_par(par), modelo, uso=uso)
    d = _json_de(crudo)
    if d is None or "veredicto" not in d:
        # Un segundo intento antes de darlo por perdido (a veces la respuesta llega cortada).
        crudo = pedir(PROMPT, texto_par(par), modelo, uso=uso)
        d = _json_de(crudo)
    if d is None or "veredicto" not in d:
        raise ErrorJuez(f"La IA devolvió una respuesta que no se entiende ({modelo}): {' '.join((crudo or '').split())[:160]!r}")
    return _normalizar_respuesta(d, modelo)


def evaluar(par: dict, uso: Uso = None) -> dict:
    """Veredicto final de un par: Haiku primero; si ve algo, confirma Sonnet."""
    rapido, fino = modelos()
    r = _evaluar_con(par, rapido, uso)
    if r["veredicto"] != "no_confundible" and fino and fino != rapido:
        try:
            r2 = _evaluar_con(par, fino, uso)
            r2["modelo"] = f"{rapido} + {fino}"
            return r2
        except ErrorJuez:
            r["modelo"] = f"{rapido} (sin confirmar)"
    return r


def evaluar_lote(pares: list, uso: Uso = None, hilos: int = HILOS, log=print) -> list:
    """Evalúa varios pares en paralelo. Devuelve una lista del mismo largo con
    el veredicto o None si ese par falló (queda para la próxima corrida)."""
    if not pares:
        return []
    if not configurado():
        return [None] * len(pares)
    resultados = [None] * len(pares)
    cortar = threading.Event()

    def uno(i):
        if cortar.is_set():
            return
        try:
            resultados[i] = evaluar(pares[i], uso)
        except ErrorJuez as e:
            # Clave inválida o sin saldo: no tiene sentido seguir intentando.
            if "HTTP 401" in str(e) or "HTTP 403" in str(e) or "credit" in str(e).lower():
                cortar.set()
            log(f"[juez] un par quedó sin evaluar: {e}")

    with ThreadPoolExecutor(max_workers=max(1, hilos)) as ex:
        list(ex.map(uno, range(len(pares))))
    return resultados


def alcanza(veredicto: str, sensibilidad: str) -> bool:
    """¿Este veredicto se muestra como alerta con esta sensibilidad?"""
    return veredicto in SENSIBILIDADES.get(sensibilidad or SENSIBILIDAD_DEFAULT, SENSIBILIDADES[SENSIBILIDAD_DEFAULT])[1]


def probar_conexion() -> dict:
    """Llamada mínima para el chequeo del panel."""
    if not configurado():
        return {"ok": False, "detalle": "Falta la variable ANTHROPIC_API_KEY en el servidor del panel."}
    rapido, fino = modelos()
    try:
        t = pedir("Respondé solo con la palabra OK.", "Prueba de conexión", rapido, max_tokens=5)
        return {"ok": True, "detalle": f"La API respondió ({rapido}): {t.strip()[:20]}", "modelos": [rapido, fino]}
    except ErrorJuez as e:
        return {"ok": False, "detalle": str(e), "modelos": [rapido, fino]}


def costo_estimado(uso: Uso) -> float:
    """USD aproximados de la corrida (a precio de Sonnet, el más caro de los dos: cota superior)."""
    return round(uso.entrada / 1e6 * 2 + uso.cache / 1e6 * 0.2 + uso.salida / 1e6 * 10, 4)
