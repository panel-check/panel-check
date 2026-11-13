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
# Lista real sacada del «Diagnóstico de ubicaciones» del 13/11/2026 (cartera + 120 marcas al azar).
# Después de la concesión la ubicación queda en «División Resoluciones»: la concesión se detecta por el
# estado del trámite («Concedida»), no por el sector.
ORDEN = ["estudio_forma", "estudio_fondo", "analisis_fondo", "publicacion", "notificaciones", "resoluciones"]

# Cada regla: (clave del catálogo, palabras que tienen que estar TODAS en el texto limpio).
REGLAS = [
    ("estudio_forma", ("estudio", "forma")),      # «Sector Estudio Forma», «Sector Estudio Forma IA»
    ("estudio_fondo", ("estudio", "fondo")),      # «Sector Estudio Fondo 583/25», «Sector Estudio Fondo IA»
    ("analisis_fondo", ("analisis", "fondo")),    # «División Análisis de Fondo»
    ("publicacion", ("publicacion",)),
    ("notificaciones", ("notificacion",)),
    ("resoluciones", ("resolucion",)),
]

CATALOGO = {
    "estudio_forma": {
        "nombre": "Estudio de forma",
        "que_es": "El INPI está revisando que la solicitud esté completa y bien presentada: los datos, "
                  "la clase y, si tiene, el logo.",
        "que_sigue": "Si está todo en orden, pasa al estudio de fondo. Si falta algo, el INPI lo pide con "
                     "una vista formal, que hay que contestar en un plazo corto.",
        "mail": True,
    },
    "analisis_fondo": {
        # Visto en marcas de la cartera con mucho tiempo ahí (mediana 156 días): falta confirmar con Pame
        # qué significa. Hasta tener el texto validado no sale mail.
        "nombre": "Análisis de fondo",
        "que_es": "El INPI está analizando en detalle tu marca.",
        "que_sigue": "Te avisamos apenas haya una novedad.",
        "mail": False,
    },
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


# ── Tablas ────────────────────────────────────────────────────────────────
# seguimiento_titulares: uno por titular (un solo mail y un solo link para todas sus actas).
#   estado: prueba | pendiente | invitado | activo | baja. Los de prueba nunca reciben mails: sirven para
#   validar el barrido con actas reales antes de lanzar.
# seguimiento_actas: cada acta seguida, con la última ubicación leída y si sigue activa (motivo_fin al cortar).
# seguimiento_eventos: historial (ubicaciones para la línea de tiempo, concesión, apoderado...). aviso:
#   pendiente (hay que mandar mail) | alerta (lo tiene que ver Pame en el panel) | no_corresponde | enviado.
DDL = [
    """CREATE TABLE IF NOT EXISTS seguimiento_titulares (
        id           SERIAL PRIMARY KEY,
        clave        TEXT UNIQUE NOT NULL,
        email        TEXT,
        token        TEXT UNIQUE NOT NULL,
        estado       TEXT NOT NULL DEFAULT 'pendiente',
        prueba       BOOLEAN NOT NULL DEFAULT false,
        invitado_en  TIMESTAMPTZ,
        aceptado_en  TIMESTAMPTZ,
        baja_en      TIMESTAMPTZ,
        creado_en    TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS seguimiento_actas (
        acta             TEXT PRIMARY KEY,
        titular_id       INTEGER NOT NULL REFERENCES seguimiento_titulares(id) ON DELETE CASCADE,
        activo           BOOLEAN NOT NULL DEFAULT true,
        motivo_fin       TEXT,
        finalizado_en    TIMESTAMPTZ,
        ubicacion_actual TEXT,
        ubicacion_fecha  DATE,
        sector           TEXT,
        estado_tramite   TEXT,
        revisado_en      TIMESTAMPTZ,
        alta_en          TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_seguimiento_actas_titular ON seguimiento_actas(titular_id)",
    """CREATE TABLE IF NOT EXISTS seguimiento_eventos (
        id               SERIAL PRIMARY KEY,
        acta             TEXT NOT NULL,
        tipo             TEXT NOT NULL,
        sector           TEXT,
        ubicacion        TEXT,
        ubicacion_fecha  DATE,
        detalle          TEXT,
        aviso            TEXT NOT NULL DEFAULT 'no_corresponde',
        detectado_en     TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_seguimiento_eventos_acta ON seguimiento_eventos(acta, detectado_en)",
    "CREATE INDEX IF NOT EXISTS idx_seguimiento_eventos_aviso ON seguimiento_eventos(aviso) WHERE aviso IN ('pendiente', 'alerta')",
]


def asegurar_tablas(cur) -> None:
    for sql in DDL:
        cur.execute(sql)


# ── Lógica de cada lectura (pura: se prueba sin base ni INPI) ─────────────
def _fin_por_estado(estado_tramite: str | None) -> str | None:
    t = limpiar(estado_tramite)
    if "conced" in t:
        return "concedida"
    if "deneg" in t:
        return "denegada"
    if "desist" in t or "abandon" in t:
        return "desistida"
    return None


def con_representante(exp: dict) -> bool:
    """Mismo criterio que define si es lead: CARACTER (Apoderado, Gestor Ratificado) o matrícula de agente."""
    return bool((exp.get("caracter") or "").strip() or (exp.get("matricula_agente") or "").strip())


def evaluar(guardado: dict, exp: dict, prueba: bool = False) -> tuple[dict, list[dict]]:
    """Compara lo guardado de un acta con una lectura nueva del expediente (inpi_lead.consultar_expediente).
    Devuelve (cambios para seguimiento_actas, eventos para seguimiento_eventos). En las de prueba ningún
    evento queda para mail ni alerta."""
    ub, fecha = exp.get("ubicacion_actual"), exp.get("ubicacion_fecha")
    sector = sector_de(ub)
    cambios = {"ubicacion_actual": ub, "ubicacion_fecha": fecha, "sector": sector,
               "estado_tramite": exp.get("estado_tramite")}
    eventos = []
    fin = "apoderado" if con_representante(exp) else _fin_por_estado(exp.get("estado_tramite"))

    def ev(tipo, aviso, detalle=None):
        eventos.append({"tipo": tipo, "sector": sector, "ubicacion": ub, "ubicacion_fecha": fecha,
                        "detalle": detalle, "aviso": "no_corresponde" if prueba else aviso})

    anterior = (guardado.get("ubicacion_actual"), guardado.get("ubicacion_fecha"))
    if ub and not anterior[0]:
        ev("ubicacion_inicial", "no_corresponde")
    elif ub and hubo_cambio((anterior[0], str(anterior[1]) if anterior[1] else None), (ub, fecha)):
        if fin == "apoderado":
            aviso = "no_corresponde"
        elif sector is None:
            aviso = "alerta"  # sector que el catálogo no conoce: se carga antes de avisar a nadie
        elif not CATALOGO[sector]["mail"]:
            aviso = "no_corresponde"
        else:
            aviso = "pendiente"
        ev("ubicacion", aviso, None if sector else "Sector desconocido: cargarlo en el catálogo")

    if fin:
        cambios.update({"activo": False, "motivo_fin": fin})
        if fin == "concedida":
            ev("concedida", "pendiente")
        elif fin == "apoderado":
            ev("apoderado", "no_corresponde", "Se sumó un apoderado o gestor: no se mandan más mails")
        else:
            ev(fin, "alerta", "Contacto personalizado")
    return cambios, eventos
