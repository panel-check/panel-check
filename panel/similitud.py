"""
Motor de similitud entre marcas para la vigilancia marcaria.

Sin dependencias externas (solo biblioteca estándar): lo usan el panel (para
probar un nombre a mano) y scripts/vigilancia.py (para comparar cada
solicitud nueva contra la cartera).

Cómo puntúa dos marcas (0-100):
  - Se normalizan: mayúsculas, sin acentos, sin signos, sin palabras
    genéricas ("GROUP", "STORE", "SA"...) salvo que la marca quedaría vacía.
  - Se calculan varias señales y se toma la MEJOR (no el promedio: alcanza
    con que una sola sea fuerte, igual que un examinador de INPI):
      * escrita     Jaro-Winkler y distancia de edición sobre el nombre
                    (con y sin espacios: "BALI STONE" = "BALISTONE").
      * fonética    el nombre pasado a una clave fonética del español
                    rioplatense (C/S/Z, B/V, LL/Y, H muda, K/QU/C, PH/F...).
      * inclusión   una marca contenida en la otra ("KOMUNIKA" en "KOMUNIKA
                    PLUS") o compartiendo la palabra distintiva.
      * traducción  (no implementado: el cruce idiomático queda para revisión
                    humana).
  - Después se ajusta por clase: misma clase = sin cambios; clase vinculada
    (ver tabla editable) = -8; clase sin relación = no se compara (la
    alerta directamente no se crea), salvo que el cliente tenga activado
    "vigilar todas las clases" en esa marca o el nombre sea idéntico.

El puntaje es una ayuda para priorizar, no un dictamen: la decisión de
oponer es siempre de una persona.
"""

import re
import unicodedata
from difflib import SequenceMatcher

_ACENTOS = str.maketrans("ÁÉÍÓÚÜÀÈÌÒÙÂÊÎÔÛÑÇ", "AEIOUUAEIOUAEIOUNC")


def _sin_acentos(s: str) -> str:
    s = (s or "").upper().translate(_ACENTOS)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def tokens(nombre: str, genericas=frozenset()) -> list:
    """Palabras significativas del nombre (sin acentos ni signos ni genéricas).
    Si al sacar las genéricas no queda nada, devuelve todas las palabras."""
    base = re.sub(r"[^A-Z0-9 ]", " ", _sin_acentos(nombre))
    # "&" y "+" separan palabras; los puntos de siglas (S.A.) ya se van arriba.
    todas = [t for t in base.split() if t]
    utiles = [t for t in todas if t not in genericas]
    return utiles or todas


def normalizar(nombre: str, genericas=frozenset()) -> str:
    return " ".join(tokens(nombre, genericas))


# ── Fonética del español rioplatense ─────────────────────────────────────

def fonetica_palabra(w: str) -> str:
    """Clave fonética simplificada. No es un algoritmo lingüístico completo:
    une las grafías que en el oído rioplatense suenan igual (seseo, yeísmo
    con "sh", b/v, h muda, k/c/qu, x, ph, w, dobles consonantes)."""
    w = _sin_acentos(w)
    w = re.sub(r"[^A-Z0-9]", "", w)
    if not w:
        return ""
    # Dígrafos y grupos antes de pasar letra por letra.
    reemplazos = [
        ("PH", "F"), ("CK", "K"), ("QU", "K"), ("GUE", "GE"), ("GUI", "GI"),
        ("GÜ", "G"), ("SCH", "S"), ("SH", "X"), ("CH", "X"), ("LL", "Y"),
        ("TH", "T"), ("WH", "W"), ("KS", "X"), ("CC", "K"),
    ]
    for a, b in reemplazos:
        w = w.replace(a, b)
    # Una H inicial o entre vocales es muda; después de C ya se resolvió (CH).
    w = re.sub(r"H", "", w)
    # C suave (e, i) suena S en el Río de la Plata; C dura suena K. Z suena S.
    w = re.sub(r"C(?=[EI])", "S", w)
    w = w.replace("C", "K").replace("Z", "S")
    # G suave (e, i) y J suenan igual.
    w = re.sub(r"G(?=[EI])", "J", w)
    # B/V y W (como "u") igualan.
    w = w.replace("V", "B").replace("W", "B")
    # Y vocal/semiconsonante: se deja; la "I" final o sola queda como Y.
    w = re.sub(r"I(?=[AEOU])", "Y", w)
    w = re.sub(r"(?<=[AEIOU])I$", "Y", w)
    # Dobles consonantes colapsan.
    w = re.sub(r"(.)\1+", r"\1", w)
    # Ñ ya se normalizó a N arriba (se pierde la distinción, aceptable).
    return w


def fonetica(nombre: str, genericas=frozenset()) -> str:
    return " ".join(fonetica_palabra(t) for t in tokens(nombre, genericas))


# ── Métricas de cadenas ──────────────────────────────────────────────────

def distancia_edicion(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    previa = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i]
        for j, cb in enumerate(b, 1):
            actual.append(min(previa[j] + 1, actual[j - 1] + 1, previa[j - 1] + (ca != cb)))
        previa = actual
    return previa[-1]


def _sim_edicion(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return 1.0 - distancia_edicion(a, b) / max(len(a), len(b))


def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    ventana = max(max(len(a), len(b)) // 2 - 1, 0)
    ma = [False] * len(a)
    mb = [False] * len(b)
    coincidencias = 0
    for i, ca in enumerate(a):
        for j in range(max(0, i - ventana), min(len(b), i + ventana + 1)):
            if not mb[j] and b[j] == ca:
                ma[i] = mb[j] = True
                coincidencias += 1
                break
    if not coincidencias:
        return 0.0
    k = trans = 0
    for i in range(len(a)):
        if ma[i]:
            while not mb[k]:
                k += 1
            if a[i] != b[k]:
                trans += 1
            k += 1
    m = coincidencias
    jaro = (m / len(a) + m / len(b) + (m - trans / 2) / m) / 3
    prefijo = 0
    for ca, cb in zip(a[:4], b[:4]):
        if ca != cb:
            break
        prefijo += 1
    return jaro + prefijo * 0.1 * (1 - jaro)


def _sim(a: str, b: str) -> float:
    """Mezcla de Jaro-Winkler y edición: JW premia el prefijo común (lo que
    más pesa en una marca), la edición castiga largos muy distintos."""
    if not a or not b:
        return 0.0
    return 0.6 * jaro_winkler(a, b) + 0.4 * _sim_edicion(a, b)


# ── Puntaje ──────────────────────────────────────────────────────────────

def _contiene(a: str, b: str) -> bool:
    """¿Una contiene a la otra como palabra o como prefijo/sufijo de palabra
    (de al menos 4 letras)? Evita que "SOL" matchee dentro de "CONSOLA"."""
    corta, larga = (a, b) if len(a) <= len(b) else (b, a)
    if len(corta.replace(" ", "")) < 4:
        return False
    if re.search(rf"(?:^| ){re.escape(corta)}(?: |$)", larga):
        return True
    return larga.replace(" ", "").startswith(corta.replace(" ", "")) or \
        larga.replace(" ", "").endswith(corta.replace(" ", ""))


def puntaje_nombres(a: str, b: str, genericas=frozenset()) -> tuple:
    """(puntaje 0-100, [motivos]) entre dos nombres de marca, sin considerar
    clases. Devuelve (0, []) si alguno está vacío."""
    na, nb = normalizar(a, genericas), normalizar(b, genericas)
    if not na or not nb:
        return 0, []
    motivos = []
    if na == nb:
        return 100, ["nombre idéntico"]
    if na.replace(" ", "") == nb.replace(" ", ""):
        return 98, ["igual sin espacios"]

    ca, cb = na.replace(" ", ""), nb.replace(" ", "")
    candidatos = []  # (puntaje, motivo)

    escrita = max(_sim(na, nb), _sim(ca, cb))
    candidatos.append((escrita * 100, "parecido al escribirse"))

    fa, fb = fonetica(a, genericas), fonetica(b, genericas)
    if fa and fb:
        if fa == fb or fa.replace(" ", "") == fb.replace(" ", ""):
            candidatos.append((96, "suenan igual"))
        else:
            fon = max(_sim(fa, fb), _sim(fa.replace(" ", ""), fb.replace(" ", "")))
            candidatos.append((fon * 100 * 0.98, "suenan parecido"))

    # Inclusión: la palabra distintiva de una está en la otra.
    if _contiene(na, nb):
        corta, larga = (na, nb) if len(na) <= len(nb) else (nb, na)
        # Cuanto más de la larga cubre la corta, más fuerte.
        cobertura = len(corta.replace(" ", "")) / max(len(larga.replace(" ", "")), 1)
        candidatos.append((min(93, 80 + cobertura * 14), "una contiene a la otra"))
    else:
        ta, tb = set(na.split()), set(nb.split())
        comunes = [t for t in ta & tb if len(t) >= 4]
        if comunes and (len(ta) > 1 or len(tb) > 1):
            candidatos.append((78, f"comparten «{max(comunes, key=len)}»"))
        # Misma palabra con pequeñas diferencias (plural, género, errata).
        for x in ta:
            for y in tb:
                if len(x) >= 5 and len(y) >= 5 and _sim_edicion(x, y) >= 0.8 and (len(ta) > 1 or len(tb) > 1):
                    candidatos.append((76, f"palabras parecidas («{x}» / «{y}»)"))

    mejor = max(candidatos, key=lambda c: c[0])
    motivos = [m for p, m in sorted(candidatos, key=lambda c: -c[0]) if p >= mejor[0] - 6][:2]
    return int(round(min(100, mejor[0]))), motivos


def relacion_clases(clase_a, clase_b, pares_relacionados: set) -> str:
    """'misma' | 'relacionada' | 'distinta' | 'desconocida'."""
    if clase_a is None or clase_b is None:
        return "desconocida"
    if int(clase_a) == int(clase_b):
        return "misma"
    par = (min(int(clase_a), int(clase_b)), max(int(clase_a), int(clase_b)))
    return "relacionada" if par in pares_relacionados else "distinta"


def puntaje_marcas(nombre_a, clase_a, nombre_b, clase_b, genericas=frozenset(),
                   pares_relacionados=frozenset(), todas_las_clases=False) -> dict:
    """Puntaje final entre la marca de un cliente (a) y una solicitud nueva
    (b). Devuelve {puntaje, motivos, relacion_clases} o None si no hay nada
    que alertar por clases."""
    base, motivos = puntaje_nombres(nombre_a, nombre_b, genericas)
    if base == 0:
        return None
    rel = relacion_clases(clase_a, clase_b, pares_relacionados)
    puntaje = base
    if rel == "misma":
        motivos = motivos + [f"misma clase ({clase_b})"]
    elif rel == "relacionada":
        puntaje -= 8
        motivos = motivos + [f"clase vinculada ({clase_a} ↔ {clase_b})"]
    elif rel == "desconocida":
        puntaje -= 5
        motivos = motivos + ["clase sin confirmar"]
    else:  # distinta
        if base >= 98 or todas_las_clases:
            # Idéntica (o vigilancia en todas las clases): se avisa, más baja.
            puntaje -= 15
            motivos = motivos + [f"otra clase ({clase_b})"]
        else:
            return None
    return {"puntaje": max(0, min(100, puntaje)), "motivos": motivos, "relacion_clases": rel}


def nivel(puntaje: int, nivel_alta: int = 90, nivel_media: int = 82) -> str:
    if puntaje >= nivel_alta:
        return "alta"
    if puntaje >= nivel_media:
        return "media"
    return "baja"


# Índice para no comparar cada solicitud contra TODA la cartera (cientos de
# marcas x miles de solicitudes): se generan claves baratas por marca y solo
# se compara cuando comparten alguna.
def claves_indice(nombre: str, genericas=frozenset()) -> set:
    claves = set()
    toks = tokens(nombre, genericas)
    if not toks:
        return claves
    pegado = "".join(toks)
    fon = [fonetica_palabra(t) for t in toks]
    fon_pegado = "".join(fon)
    for base, pref in ((pegado, "e"), (fon_pegado, "f")):
        if len(base) >= 3:
            claves.add(f"{pref}3:{base[:3]}")
            claves.add(f"{pref}t3:{base[-3:]}")
    for t in toks:
        if len(t) >= 4:
            claves.add(f"w:{t}")
    for t in fon:
        if len(t) >= 3:
            claves.add(f"fw:{t}")
    return claves


def ratio_difflib(a: str, b: str) -> float:
    """Solo para pruebas de sanidad contra una implementación estándar."""
    return SequenceMatcher(None, a, b).ratio()
