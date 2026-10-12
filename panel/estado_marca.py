"""
Estado público de una marca (lo que ve la persona en el verificador de la web).

INPI devuelve en cada fila de la búsqueda un campo «Estado» de texto libre. Además, el panel sabe
cosas de las marcas que ya cargó (`marcas`): si salió en un boletín, cuándo se publicó, si tuvo
oposición y si ya tiene resolución. Este módulo junta las dos fuentes y devuelve UN estado claro:

  en_tramite      presentada, todavía sin publicar en el boletín (el «preboletín»)
  publicada       salió en el boletín (con «en plazo de oposición» si todavía corre el plazo)
  con_oposicion   alguien se opuso a la solicitud
  concedida       INPI la concedió (la marca está registrada)
  denegada        INPI la denegó
  inactiva        abandonada, caducada o vencida
  otro            un estado que INPI escribió con otras palabras: se muestra tal cual

Sin red ni base: lo usa verificador_core.evaluar y se prueba aparte (scripts/test_verificador.py).

Qué tan confiable es cada fuente:
  - La resolución (concedida / denegada) y las oposiciones salen de lo que el panel leyó del expediente.
  - Lo que dice INPI en «Estado» se interpreta por palabras clave: si INPI escribe algo que no
    reconocemos, el estado sale como «otro» con el texto de INPI en vez de adivinar.
  - Una marca que el panel no cargó (anterior a 2026 o no escaneada) solo tiene lo que dice INPI.
"""

import re
import unicodedata
from datetime import date, datetime

# Los 30 días son el plazo de oposición que usa todo el sistema (revisar_oposiciones.py).
# Solo sirve para decir «en plazo de oposición»; no se muestra cuántos días quedan.
PLAZO_OPOSICION_DIAS = 30

TEXTOS = {
    "en_tramite": "En trámite",
    "publicada": "Publicada",
    "con_oposicion": "Con oposición",
    "concedida": "Concedida",
    "denegada": "Denegada",
    "inactiva": "Abandonada o caducada",
}
TEXTO_SIN_PUBLICAR = "En trámite (todavía sin publicar)"
TEXTO_EN_PLAZO = "Publicada · en plazo de oposición"
MAX_TEXTO_OTRO = 40


def _plano(texto) -> str:
    """Minúsculas y sin acentos: «Concedída» y «concedida» son lo mismo."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", t).strip().lower()


def estado_de_inpi(texto):
    """Lo que dice el campo «Estado» de INPI → código, o None si no lo reconocemos."""
    t = _plano(texto)
    if not t:
        return None
    if "denegad" in t or "rechazad" in t:
        return "denegada"
    if "no vigente" in t or any(p in t for p in ("abandon", "caduc", "desisti", "archiv", "vencid", "nula", "cancelad")):
        return "inactiva"
    if "concedid" in t or "registrad" in t or "vigente" in t:
        return "concedida"
    if "publicad" in t:
        return "publicada"
    if any(p in t for p in ("tramite", "solicitud", "presentad", "examen", "pendiente")):
        return "en_tramite"
    return None


def _fecha(valor):
    if isinstance(valor, datetime):
        return valor.date()
    return valor if isinstance(valor, date) else None


def _estado(codigo: str, texto: str = "") -> dict:
    return {"codigo": codigo, "texto": texto or TEXTOS.get(codigo, codigo)}


def _texto_de_inpi(texto) -> str:
    limpio = re.sub(r"[\x00-\x1f\x7f]+", " ", str(texto or ""))
    limpio = re.sub(r"\s+", " ", limpio).strip()[:MAX_TEXTO_OTRO]
    return limpio[:1].upper() + limpio[1:].lower() if limpio else ""


def estado_publico(estado_inpi, db=None, hoy=None):
    """{codigo, texto} de la marca, o None si no hay nada que decir.

    estado_inpi  el texto del campo «Estado» de INPI para esa fila.
    db           lo que el panel sabe de esa acta (fila de `marcas`) o None si no la tiene:
                 boletin, fuente, fecha_publicacion, tuvo_oposicion, estado_oposicion, estado_tramite.
    """
    db = db or {}
    hoy = hoy or date.today()
    inpi = estado_de_inpi(estado_inpi)
    resolucion = _plano(db.get("estado_tramite"))

    # 1) La resolución manda sobre todo lo demás.
    if resolucion.startswith("concedid"):
        return _estado("concedida")
    if resolucion.startswith("denegad"):
        return _estado("denegada")
    if inpi in ("concedida", "denegada", "inactiva"):
        return _estado(inpi)

    # 2) Una oposición que sigue en pie (si se levantó, la marca sigue su camino).
    if db.get("tuvo_oposicion") and _plano(db.get("estado_oposicion")) != "levantada":
        return _estado("con_oposicion")

    # 3) Publicada: salió en un boletín, o INPI dice que está publicada.
    publicada_el = _fecha(db.get("fecha_publicacion"))
    if publicada_el or db.get("boletin") or inpi == "publicada":
        if publicada_el and 0 <= (hoy - publicada_el).days <= PLAZO_OPOSICION_DIAS:
            return _estado("publicada", TEXTO_EN_PLAZO)
        return _estado("publicada")

    # 4) Preboletín: el panel la tiene (la encontró el escaneo de actas) pero todavía no salió en un boletín.
    if db:
        return _estado("en_tramite", TEXTO_SIN_PUBLICAR)

    # 5) Solo lo que dice INPI.
    if inpi == "en_tramite":
        return _estado("en_tramite")
    crudo = _texto_de_inpi(estado_inpi)
    return {"codigo": "otro", "texto": crudo} if crudo else None
