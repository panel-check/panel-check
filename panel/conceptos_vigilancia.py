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

Quién arma la lista: desde el 12/11/2026, Claude (ANTHROPIC_API_KEY, ver
juez_vigilancia.py), con un pedido más estricto; sin esa clave, la IA de
«Mejorar texto» (ia_texto.py). UNA vez por nombre de marca, no por solicitud.
La primera vez que el panel arranca con Claude configurado, rearma una vez
todas las listas que había hecho la IA anterior (la vieja queda guardada en
conceptos_previos); las corregidas a mano después de ese día no se tocan. Lo hace sola un
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
import juez_vigilancia as juez
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


PROMPT_CLAUDE = (
    "Sos un agente de la propiedad industrial de Argentina que hace vigilancia de marcas. Te paso el nombre de una "
    "marca de un cliente. Devolvé SOLO los términos que un examinador del INPI consideraría que transmiten exactamente "
    "LA MISMA IDEA para un consumidor argentino, para detectar solicitudes de terceros que se le parezcan por su "
    "significado aunque se escriban distinto:\n"
    "- traducciones directas al inglés, portugués, italiano y francés de las palabras con significado (y al español si la "
    "marca está en otro idioma);\n"
    "- sinónimos muy cercanos de uso corriente.\n"
    "Reglas estrictas:\n"
    "1. Interpretá la marca como la entiende un argentino, incluido el lunfardo y el uso rioplatense (TIMBEROS = "
    "jugadores, apostadores; no tiene nada que ver con la madera). Si dudás del sentido, no inventes: devolvé menos términos.\n"
    "2. Nada de asociaciones libres, campos semánticos amplios, palabras que solo se parecen en otro idioma ni traducciones "
    "rebuscadas o de verbos poco comunes (MANAR no es FLUX).\n"
    "3. Si la marca es una palabra inventada sin significado, devolvé la lista vacía; si evoca claramente una palabra real "
    "(KOMUNIKA → comunicar), usá esa palabra y sus traducciones.\n"
    "4. Si la marca une palabras (GIGARED = GIGA + RED, SOLMAR = SOL + MAR), incluí también la versión traducida unida "
    "(GIGANET, SUNSEA).\n"
    "5. No incluyas el nombre tal cual, ni variantes de escritura, ni palabras genéricas o descriptivas del rubro (VIAJES, "
    "TURISMO, TRIP, CAFE, SHOP, STORE, GROUP, ESTUDIO, SOLUCIONES…), ni artículos o preposiciones.\n"
    "6. Si la marca es (o empieza con) el nombre y/o apellido de una persona (SILVIO SIERRA, FRANCESCA INNOCENTI, "
    "JUAN PEREZ DISEÑOS) o un apellido usado como marca, NO traduzcas el nombre ni el apellido aunque coincida con una "
    "palabra (SIERRA no es MOUNTAIN, INNOCENTI no es SOFT): devolvé la lista vacía, o solo los términos de las otras "
    "palabras con significado propio. Lo mismo con abreviaturas o palabras cortadas (DELI no es DELICIOSO).\n"
    "7. Máximo 8 términos, cada uno de 1 a 3 palabras, en mayúsculas.\n"
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


def disponible() -> bool:
    return juez.configurado() or ia_texto.configurada()


def origen_actual() -> str:
    return "claude" if juez.configurado() else "llama"


def pedir_a_la_ia(denominacion: str) -> list:
    """La lista de conceptos de un nombre de marca. Levanta ia_texto.ErrorIA si no se pudo."""
    if juez.configurado():
        try:
            crudo = juez.pedir(PROMPT_CLAUDE, f"Marca: {denominacion.strip()}", juez.modelos()[1], max_tokens=300)
        except juez.ErrorJuez as e:
            raise ia_texto.ErrorIA(f"Claude no respondió: {e}") from e
        d = juez._json_de(crudo)
    else:
        crudo = ia_texto._pedir([{"role": "system", "content": PROMPT},
                                 {"role": "user", "content": f"Marca: {denominacion.strip()}"}], temperatura=0.1)
        d = ia_texto._json_de(crudo)
    if d is None or not isinstance(d.get("conceptos"), list):
        raise ia_texto.ErrorIA("La IA devolvió una respuesta que no se entiende.")
    return limpiar(d["conceptos"], denominacion)


def _clave(denominacion: str) -> str:
    return " ".join((denominacion or "").split()).upper()


def guardar(cur, acta: str, denominacion: str, conceptos: list, origen: str = None):
    cur.execute(
        "UPDATE cartera_marcas SET conceptos_vigilancia = %s, conceptos_para = %s, conceptos_en = now(), "
        "conceptos_origen = %s WHERE acta = %s",
        (", ".join(conceptos) or None, _clave(denominacion), origen or origen_actual(), acta),
    )


def _ya_armados(cur, denominacion: str):
    """Si otro expediente con el mismo nombre ya tiene su lista (de la IA o corregida), se reusa: no se pregunta dos veces."""
    # Con Claude configurado, no se reusa una lista de la IA anterior.
    origenes = ["claude", "manual"] if juez.configurado() else ["claude", "manual", "llama"]
    cur.execute("SELECT conceptos_vigilancia, conceptos_origen FROM cartera_marcas WHERE conceptos_para = %s "
                "AND conceptos_en IS NOT NULL AND COALESCE(conceptos_origen, 'llama') = ANY(%s) "
                "ORDER BY conceptos_en DESC LIMIT 1", (_clave(denominacion), origenes))
    f = cur.fetchone()
    if f is None:
        return None
    return sim.separar_conceptos(f["conceptos_vigilancia"] if isinstance(f, dict) else f[0]), \
        (f["conceptos_origen"] if isinstance(f, dict) else f[1])


def armar(cur, acta: str, denominacion: str, reusar: bool = True) -> list:
    """Arma y guarda la lista de una marca. Levanta ia_texto.ErrorIA si la IA falla."""
    previo = _ya_armados(cur, denominacion) if reusar else None
    if previo is not None:
        conceptos, origen = previo
    else:
        conceptos, origen = pedir_a_la_ia(denominacion), origen_actual()
    guardar(cur, acta, denominacion, conceptos, origen)
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
    if not disponible():
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


def rearmar_con_claude_una_vez(conexion, log=print) -> int:
    """La primera vez que hay Claude: las listas que armó la IA anterior (o sin origen) se
    marcan para rearmar; la lista vieja queda en conceptos_previos. Una sola vez."""
    if not juez.configurado():
        return 0
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM vigilancia_config WHERE clave = 'conceptos_rearmados_claude'")
            if cur.fetchone():
                return 0
            cur.execute("UPDATE cartera_marcas SET conceptos_previos = conceptos_vigilancia, conceptos_para = NULL "
                        "WHERE conceptos_en IS NOT NULL AND COALESCE(conceptos_origen, 'llama') = 'llama'")
            n = cur.rowcount
            cur.execute("INSERT INTO vigilancia_config (clave, valor) VALUES ('conceptos_rearmados_claude', now()::text) "
                        "ON CONFLICT (clave) DO NOTHING")
        conn.commit()
    log(f"[conceptos] {n} lista(s) de la IA anterior se van a rearmar con Claude")
    return n


def rearmar_nombres_propios_una_vez(conexion, log=print) -> int:
    """13/11/2026: se sumó la regla de nombres de persona al pedido. Las listas que armó Claude
    (no las cargadas a mano) se rearman una vez; la lista vieja queda en conceptos_previos."""
    if not juez.configurado():
        return 0
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM vigilancia_config WHERE clave = 'conceptos_rearmados_nombres'")
            if cur.fetchone():
                return 0
            cur.execute("UPDATE cartera_marcas SET conceptos_previos = conceptos_vigilancia, conceptos_para = NULL "
                        "WHERE conceptos_en IS NOT NULL AND conceptos_origen = 'claude'")
            n = cur.rowcount
            cur.execute("INSERT INTO vigilancia_config (clave, valor) VALUES ('conceptos_rearmados_nombres', now()::text) "
                        "ON CONFLICT (clave) DO NOTHING")
        conn.commit()
    log(f"[conceptos] {n} lista(s) de Claude se van a rearmar (regla de nombres de persona)")
    return n


def iniciar(conexion):
    """Prende el hilo que arma las listas que faltan. Sin IA no hace nada."""
    if not disponible():
        print("[conceptos] sin IA (ANTHROPIC_API_KEY ni IA_API_KEY): la vigilancia por concepto queda solo con las listas cargadas a mano")
        return None
    try:
        rearmar_con_claude_una_vez(conexion, log=lambda t: print(t, flush=True))
        rearmar_nombres_propios_una_vez(conexion, log=lambda t: print(t, flush=True))
    except Exception as e:  # noqa: BLE001
        print(f"[conceptos] no se pudo marcar el rearmado con Claude: {e}", flush=True)

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
