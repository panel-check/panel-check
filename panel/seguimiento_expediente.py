"""Vigilancia de expediente gratuita (en diseño, 13/11/2026): catálogo de sectores del INPI.

La «UBICACIÓN ACTUAL» del expediente (inpi_lead.ubicacion_expediente) dice en qué sector del
INPI está el trámite. El INPI cambia los nombres de los sectores (el 12/11/2026 «ESTUDIO DE
FONDO IA» pasó a llamarse «Sector Estudio Fondo 583/25»), así que no se compara el texto
exacto: se limpia (sin tildes, números ni mayúsculas) y se buscan palabras clave.

Un sector que no reconoce ninguna regla queda como desconocido (None): nunca sale un mail con
una explicación vacía; el panel lo avisa para cargarlo en el catálogo.

Los textos son una propuesta para pulir con Pame (todavía no se mandan a nadie).
"""

import re
import unicodedata

# Orden en que suelen pasar las marcas sin problemas (para la línea de tiempo).
ORDEN = ["estudio_fondo", "publicacion", "notificaciones", "resoluciones"]

# Cada regla: (clave del catálogo, palabras que tienen que estar TODAS en el texto limpio).
REGLAS = [
    ("estudio_fondo", ("estudio", "fondo")),
    ("publicacion", ("publicacion",)),
    ("notificaciones", ("notificacion",)),
    ("resoluciones", ("resolucion",)),
]

CATALOGO = {
    "estudio_fondo": {
        "nombre": "Estudio de fondo",
        "que_es": "El INPI está analizando tu marca: revisa que se pueda registrar y que no choque con "
                  "otras ya registradas o en trámite.",
        "que_sigue": "Si no encuentra problemas, se publica en el Boletín de Marcas. Si encuentra algo, "
                     "emite una vista, una observación que hay que contestar en un plazo.",
        "mail": True,
    },
    "publicacion": {
        "nombre": "Publicación",
        "que_es": "Tu marca pasó el estudio y fue publicada en el Boletín de Marcas, donde cualquiera puede verla.",
        "que_sigue": "Se abre un plazo de 30 días en el que otras personas o empresas pueden presentar oposiciones.",
        "mail": True,
    },
    "notificaciones": {
        # Llega acá cuando hubo oposición: ya se avisó con el mail de oposición, así que no sale mail de ubicación.
        "nombre": "Notificaciones",
        "que_es": "El INPI está notificando novedades de tu expediente.",
        "que_sigue": "Revisá la bandeja de entrada del portal del INPI.",
        "mail": False,
    },
    "resoluciones": {
        "nombre": "Resoluciones",
        "que_es": "Terminó el plazo de oposiciones y tu marca está en la etapa final: el INPI tiene que "
                  "resolver si la concede.",
        "que_sigue": "La resolución, que suele demorar alrededor de un mes.",
        "mail": True,
    },
}


def limpiar(texto: str | None) -> str:
    """Texto del sector sin tildes, números, signos ni mayúsculas: «Sector Estudio Fondo 583/25»
    → «sector estudio fondo»."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def sector_de(ubicacion: str | None) -> str | None:
    """Clave del catálogo para el texto de UBICACIÓN ACTUAL, o None si no se reconoce."""
    t = limpiar(ubicacion)
    if not t:
        return None
    palabras = t.split()
    for clave, requeridas in REGLAS:
        if all(any(p.startswith(r) for p in palabras) for r in requeridas):
            return clave
    return None


def firma_ubicacion(ubicacion: str | None, fecha: str | None) -> str | None:
    """Lo que identifica una ubicación para detectar cambios: el sector (la clave del catálogo, o
    el texto limpio si es desconocido) más la fecha. Un renombre del INPI con la misma fecha no
    cuenta como movimiento; volver a un sector en el que ya estuvo (fecha nueva) sí."""
    if not ubicacion and not fecha:
        return None
    return f"{sector_de(ubicacion) or limpiar(ubicacion)}|{fecha or ''}"


def hubo_cambio(anterior: tuple, nueva: tuple) -> bool:
    """anterior y nueva son (ubicacion, fecha). La primera lectura (anterior vacía) no es un cambio."""
    fa, fn = firma_ubicacion(*anterior), firma_ubicacion(*nueva)
    return fa is not None and fn is not None and fa != fn
