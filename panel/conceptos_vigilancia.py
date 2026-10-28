"""
Conceptos de cada marca de la cartera para la vigilancia marcaria.

La comparación diaria (vigilancia_core.py) ve si una solicitud nueva se ESCRIBE
o SUENA parecido a una marca de un cliente. Lo que no ve sola es el
SIGNIFICADO: «SUN» contra «SOL», «LION» contra «LEÓN», «RÁPIDO» contra
«VELOZ». Para eso cada marca vigilada tiene su lista de conceptos (columna
cartera_marcas.conceptos_vigilancia, separada por coma): traducciones,
sinónimos y palabras muy cercanas del mismo campo. Si una solicitud nueva
nombra uno de esos conceptos, la vigilancia la alerta con el motivo «mismo
concepto que …» (puntaje en similitud.puntaje_conceptos).

Quién arma la lista: la IA del panel (la misma de «Mejorar texto», ver
ia_texto.py), UNA vez por nombre de marca, no por solicitud. Lo hace sola un
hilo del panel (cada 20 minutos, de a tandas) para las marcas vigiladas que
todavía no la tienen o que cambiaron de nombre (`conceptos_para` guarda el
nombre para el que se armó). El equipo la puede corregir a mano en la ficha
de la marca (pestaña Vigilancia) o pedir que se vuelva a armar.

Qué se manda a la IA: solo el nombre de la marca (dato público del boletín).
Nada del cliente.

Sin FastAPI: lo usan el panel (cartera_api.py, app.py) y las pruebas.
"""

import threading
import time

import ia_texto
import similitud as sim

MAX_CONCEPTOS = 15
LARGO_MAX_CONCEPTO = 40
TANDA = 20                   # marcas por pasada del hilo
PASADA_SEGUNDOS = 20 * 60

PROMPT = (
    "Sos un agente de la propiedad industrial de Argentina que hace vigilancia de marcas. Te paso el nombre de una "
    "marca registrada. Devolvé los términos que un examinador del INPI consideraría que transmiten LA MISMA IDEA, "
    "para detectar solicitudes de terceros que se le parezcan por su significado aunque se escriban distinto:\n"
    "- traducciones de sus palabras con significado al español, inglés, portugués, italiano y francés;\n"
    "- sinónimos muy cercanos;\n"
    "- si la marca evoca una imagen concreta (un animal, un objeto, un lugar), hasta 3 términos muy cercanos de ese "
    "mismo campo (por ejemplo PANTERA → JAGUAR, PUMA).\n"
    "Reglas estrictas:\n"
    "1. Si la marca es una palabra inventada sin significado, devolvé la lista vacía; pero si evoca claramente una "
    "palabra real (KOMUNIKA → comunicar), usá esa palabra y sus traducciones.\n"
    "2. No incluyas el nombre de la marca tal cual, ni variantes de escritura (eso ya se controla aparte).\n"
    "3. No incluyas palabras genéricas o descriptivas del rubro (SHOP, STORE, GROUP, CAFÉ, ESTUDIO, SOLUCIONES…) "
    "ni artículos o preposiciones sueltas.\n"
    "4. Máximo 15 términos, cada uno de 1 a 3 palabras, en mayúsculas.\n"
    "Respondé SOLO con un objeto JSON, sin explicaciones: {\"conceptos\": [\"...\", \"...\"]}"
)


def limpiar(lista, denominacion: str = "") -> list:
    """Términos únicos, en mayúsculas, sin el propio nombre ni basura. Tope MAX_CONCEPTOS."""
    propio = sim.normalizar(denominacion or "")
    out, vistos = [], set()
    for c in lista or ():
        if not isinstance(c, str):
            continue
        c = " ".join(c.replace(",", " ").replace(";", " ").split()).upper()
        n = sim.normalizar(c)
        if len(n.replace(" ", "")) < 3 or len(c) > LARGO_MAX_CONCEPTO or len(n.split()) > 3:
            continue
        if n == propio or n.replace(" ", "") == propio.replace(" ", "") or n in vistos:
            continue
        vistos.add(n)
        out.append(c)
    return out[:MAX_CONCEPTOS]


def pedir_a_la_ia(denominacion: str) -> list:
    """La lista de conceptos de un nombre de marca. Levanta ia_texto.ErrorIA si no se pudo."""
    crudo = ia_texto._pedir([{"role": "system", "content": PROMPT},
                             {"role": "user", "content": f"Marca: {denominacion.strip()}"}], temperatura=0.1)
    d = ia_texto._json_de(crudo)
    if d is None or not isinstance(d.get("conceptos"), list):
        raise ia_texto.ErrorIA("La IA devolvió una respuesta que no se entiende.")
    return limpiar(d["conceptos"], denominacion)


def _clave(denominacion: str) -> str:
    return " ".join((denominacion or "").split()).upper()


def guardar(cur, acta: str, denominacion: str, conceptos: list):
    cur.execute(
        "UPDATE cartera_marcas SET conceptos_vigilancia = %s, conceptos_para = %s, conceptos_en = now() WHERE acta = %s",
        (", ".join(conceptos) or None, _clave(denominacion), acta),
    )


def _ya_armados(cur, denominacion: str):
    """Si otro expediente con el mismo nombre ya tiene su lista (de la IA o corregida), se reusa: no se pregunta dos veces."""
    cur.execute("SELECT conceptos_vigilancia FROM cartera_marcas WHERE conceptos_para = %s AND conceptos_en IS NOT NULL "
                "ORDER BY conceptos_en DESC LIMIT 1", (_clave(denominacion),))
    f = cur.fetchone()
    if f is None:
        return None
    return sim.separar_conceptos(f["conceptos_vigilancia"] if isinstance(f, dict) else f[0])


def armar(cur, acta: str, denominacion: str, reusar: bool = True) -> list:
    """Arma y guarda la lista de una marca. Levanta ia_texto.ErrorIA si la IA falla."""
    conceptos = _ya_armados(cur, denominacion) if reusar else None
    if conceptos is None:
        conceptos = pedir_a_la_ia(denominacion)
    guardar(cur, acta, denominacion, conceptos)
    return conceptos


SQL_PENDIENTES = """
    SELECT cm.acta, cm.denominacion
    FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id
    WHERE cm.vigilar AND c.activo AND NULLIF(trim(cm.denominacion), '') IS NOT NULL
      AND cm.conceptos_para IS DISTINCT FROM upper(regexp_replace(trim(cm.denominacion), '\\s+', ' ', 'g'))
"""


def contar_pendientes(cur) -> int:
    cur.execute(f"SELECT COUNT(*) AS n FROM ({SQL_PENDIENTES}) x")
    f = cur.fetchone()
    return int(f["n"] if isinstance(f, dict) else f[0])


def armar_pendientes(conexion, limite: int = TANDA, log=print) -> dict:
    """Una tanda: arma la lista de las marcas vigiladas que no la tienen (o cambiaron de nombre).
    Si la IA falla (límite de uso, clave), corta la tanda y sigue en la próxima."""
    stats = {"armadas": 0, "error": None}
    if not ia_texto.configurada():
        return stats
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_PENDIENTES + " ORDER BY cm.alta_en DESC LIMIT %s", (limite,))
            filas = [(f["acta"], f["denominacion"]) if isinstance(f, dict) else tuple(f) for f in cur.fetchall()]
            for acta, denominacion in filas:
                try:
                    armar(cur, acta, denominacion)
                except ia_texto.ErrorIA as e:
                    stats["error"] = str(e)
                    break
                conn.commit()
                stats["armadas"] += 1
    if stats["armadas"] or stats["error"]:
        log(f"[conceptos] {stats['armadas']} marca(s) con conceptos nuevos" + (f"; cortó: {stats['error']}" if stats["error"] else ""))
    return stats


def iniciar(conexion):
    """Prende el hilo que arma las listas que faltan. Sin IA_API_KEY no hace nada."""
    if not ia_texto.configurada():
        print("[conceptos] sin IA_API_KEY: la vigilancia por concepto queda solo con las listas cargadas a mano")
        return None

    def bucle():
        time.sleep(120)
        while True:
            try:
                armar_pendientes(conexion, log=lambda t: print(t, flush=True))
            except Exception as e:  # noqa: BLE001
                print(f"[conceptos] falló la pasada: {e}", flush=True)
            time.sleep(PASADA_SEGUNDOS)

    hilo = threading.Thread(target=bucle, name="conceptos-vigilancia", daemon=True)
    hilo.start()
    return hilo
