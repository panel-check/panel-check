"""
Notificaciones del INPI (webservice de Autogestión) → campanita 🔔 del panel.

Pame es apoderada de todas las marcas con las que trabajamos y vinculó al integrador del panel (ID 57874219)
desde «Mis conexiones» de la Autogestión WS (19/11/2026). Con el «Token de esta relación» (secret
INPI_HASH_DELEGACION) el método ConsultaNotificacionesAdjunto de ws.inpi.gob.ar/wsinpi.asmx devuelve las
notificaciones de su bandeja del portal: títulos digitales, vistas, vistas a la oposición, recibos de ingreso…

Cada notificación trae (prueba del 19/11/2026, 59 del último año):
    Id_Notificacion, Expediente (= acta), id_NotificacionesporDireccion (código del tipo), NombreNotificacion,
    Fecha_Carga, NombreDireccion, Notificaciones_Cuit/Notificaciones_Cuit/Cuit (uno o más CUIT) y Adjunto:
    un link de descarga directa del PDF (sin login; trae una clave, así que el PDF se baja y se guarda en la
    base apenas se ve la notificación, por si el link vence).

Códigos vistos: 1 = Título Digital Marcas · 4239 = Recibo de Ingreso · 14 = Vista · 15 = Vista a la oposición.

Sin FastAPI: lo usan el panel (notificaciones_inpi_api.py) y el proceso scripts/sincronizar_notificaciones_inpi.py.
"""

import datetime as _dt
import time
import xml.etree.ElementTree as ET
from html import escape as xml_escape

import requests

SOAP_URL = "https://ws.inpi.gob.ar/wsinpi.asmx"
NS_TEM = "http://tempuri.org/"

# código → (nombre corto para el panel, ícono, importante: avisa por Telegram)
TIPOS = {
    "1": ("Título digital", "🏅", True),
    "14": ("Vista", "👁", True),
    "15": ("Vista a la oposición", "⚠️", True),
    "4239": ("Recibo de ingreso", "🧾", False),
}


def tipo_de(codigo: str, nombre: str = ""):
    """(nombre, ícono, importante) de un código; los desconocidos se muestran con el nombre del INPI."""
    if codigo in TIPOS:
        return TIPOS[codigo]
    return (nombre or f"Notificación {codigo}", "📄", True)


def asegurar_tablas(cur) -> None:
    """Idempotente. No toca `marcas`."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notificaciones_inpi (
            id_notificacion  BIGINT PRIMARY KEY,
            acta             TEXT NOT NULL,
            tipo_codigo      TEXT,
            tipo_nombre      TEXT,
            direccion        TEXT,
            fecha_carga      TIMESTAMP,
            cuits            TEXT[],
            adjunto_url      TEXT,
            pdf              BYTEA,
            pdf_bajado_en    TIMESTAMPTZ,
            pdf_error        TEXT,
            carga_inicial    BOOLEAN NOT NULL DEFAULT false,
            creado_en        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notif_inpi_acta ON notificaciones_inpi(acta)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notif_inpi_fecha ON notificaciones_inpi(fecha_carga DESC)")
    # Hasta cuándo vio cada usuario la campanita → contador de no leídas.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notificaciones_inpi_visto (
            usuario      TEXT PRIMARY KEY,
            visto_hasta  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


# ── Webservice ──────────────────────────────────────────────────────────

def _envelope(hash_delegacion: str, desde: str = "", hasta: str = "", acta: str = "") -> str:
    filtros = {"fechaInicial": desde, "fechafinal": hasta, "expediente": acta, "direccion": "marcas"}
    campos = "".join(f"<{k}>{xml_escape(v)}</{k}>" for k, v in filtros.items() if v)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ConsultaNotificacionesAdjunto xmlns="{NS_TEM}">{campos}<hashDelegacion>{xml_escape(hash_delegacion)}</hashDelegacion></ConsultaNotificacionesAdjunto>
  </soap:Body>
</soap:Envelope>"""


def _tag(e) -> str:
    return e.tag.split("}")[-1]


def _fecha(texto: str):
    """«2026-11-10T09:31:22.153» → datetime (sin zona: es la hora que informa el INPI)."""
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        return _dt.datetime.fromisoformat(texto[:19])
    except ValueError:
        return None


def parsear(xml_texto: str) -> list[dict]:
    """Lista de notificaciones de la respuesta SOAP. Levanta ValueError si viene un error (Fault)."""
    root = ET.fromstring(xml_texto)
    for n in root.iter():
        if _tag(n) == "Fault":
            raise ValueError("el INPI devolvió un error: " + " ".join((x.text or "").strip() for x in n.iter())[:300])
    filas = []
    for n in root.iter():
        if _tag(n) != "Notificaciones_Archivos":
            continue
        d, cuits = {}, []
        for h in n:
            t = _tag(h)
            if t == "Notificaciones_Cuit":
                cuits += [(c.text or "").strip() for c in h.iter() if _tag(c) == "Cuit" and (c.text or "").strip()]
            else:
                d[t] = (h.text or "").strip()
        try:
            id_n = int(d.get("Id_Notificacion") or 0)
        except ValueError:
            continue
        if not id_n:
            continue
        filas.append({
            "id_notificacion": id_n,
            "acta": d.get("Expediente", ""),
            "tipo_codigo": d.get("id_NotificacionesporDireccion", ""),
            "tipo_nombre": d.get("NombreNotificacion", ""),
            "direccion": d.get("NombreDireccion", ""),
            "fecha_carga": _fecha(d.get("Fecha_Carga", "")),
            "cuits": sorted(set(cuits)),
            "adjunto_url": d.get("Adjunto", "") if d.get("Adjunto", "").lower().startswith("http") else "",
        })
    return filas


def consultar(hash_delegacion: str, desde: str = "", hasta: str = "", reintentos: int = 3) -> list[dict]:
    """Llama a ConsultaNotificacionesAdjunto (marcas). Sin fechas el INPI toma el último año.
    Ante error espera 5, 10, 20 s, como pide el INPI; si no hay caso, levanta la última excepción."""
    cuerpo = _envelope(hash_delegacion, desde, hasta).encode("utf-8")
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"{NS_TEM}ConsultaNotificacionesAdjunto"',
        "User-Agent": "panel-check/1.0 (notificaciones propias)",
        "Accept": "text/xml",
    }
    error = None
    for intento in range(reintentos + 1):
        try:
            r = requests.post(SOAP_URL, data=cuerpo, headers=headers, timeout=(20, 120))
            r.raise_for_status()
            return parsear(r.text)
        except (requests.RequestException, ET.ParseError, ValueError) as e:
            error = e
        if intento < reintentos:
            time.sleep(5 * (2 ** intento))
    raise error


def bajar_pdf(url: str):
    """(bytes, None) o (None, motivo)."""
    try:
        r = requests.get(url, timeout=(20, 120))
    except requests.RequestException as e:
        return None, type(e).__name__
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    if r.content[:4] != b"%PDF":
        return None, f"no es un PDF ({r.headers.get('Content-Type', '')})"
    return r.content, None


# ── Base ────────────────────────────────────────────────────────────────

def guardar(cur, filas: list[dict], carga_inicial: bool = False) -> list[int]:
    """Inserta las notificaciones que no estaban (no pisa las existentes; solo renueva el link del PDF si
    todavía no se bajó). Devuelve los id nuevos."""
    nuevos = []
    for f in filas:
        cur.execute(
            """
            INSERT INTO notificaciones_inpi (id_notificacion, acta, tipo_codigo, tipo_nombre, direccion,
                                             fecha_carga, cuits, adjunto_url, carga_inicial)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_notificacion) DO UPDATE
               SET adjunto_url = CASE WHEN notificaciones_inpi.pdf IS NULL AND EXCLUDED.adjunto_url <> ''
                                      THEN EXCLUDED.adjunto_url ELSE notificaciones_inpi.adjunto_url END
            RETURNING (xmax = 0)
            """,
            (f["id_notificacion"], f["acta"], f["tipo_codigo"], f["tipo_nombre"], f["direccion"],
             f["fecha_carga"], f["cuits"], f["adjunto_url"], carga_inicial),
        )
        if cur.fetchone()[0]:
            nuevos.append(f["id_notificacion"])
    return nuevos


SELECT_DETALLE = """
    SELECT n.id_notificacion, n.acta, n.tipo_codigo, n.tipo_nombre, n.fecha_carga, n.creado_en,
           n.carga_inicial, (n.pdf IS NOT NULL) AS tiene_pdf, (n.adjunto_url <> '') AS tiene_link,
           COALESCE(NULLIF(cm.denominacion, ''), NULLIF(m.denominacion_inpi, ''), NULLIF(m.denominacion, '')) AS marca,
           COALESCE(cm.clase, m.clase) AS clase,
           COALESCE(NULLIF(cm.titular, ''), NULLIF(m.titular, '')) AS titular,
           cm.cliente_id, c.nombre AS cliente
    FROM notificaciones_inpi n
    LEFT JOIN cartera_marcas cm ON cm.acta = n.acta
    LEFT JOIN clientes c ON c.id = cm.cliente_id
    LEFT JOIN marcas m ON m.acta = n.acta
"""


def describir(fila: dict) -> dict:
    """Agrega nombre corto e ícono del tipo a una fila de SELECT_DETALLE."""
    nombre, icono, importante = tipo_de(fila.get("tipo_codigo") or "", fila.get("tipo_nombre") or "")
    return {**fila, "tipo": nombre, "icono": icono, "importante": importante}


# ── Movimientos de las actas de clientes (cartera_novedades) ─────────────
# La revisión diaria de la cartera (revisar_cartera.yml, 23:35) guarda cada cambio que encuentra en el
# expediente de una marca de cliente (pedido de Pame, 19/11/2026, de verlos en la campanita).
# Criterio del 20/11/2026: en la campanita solo va lo importante —cambio de UBICACIÓN ACTUAL del expediente
# (con qué significa el sector), vista, oposición y cambio de estado (concedida, denegada…)—. Los movimientos
# de trámite (poder, toma de intervención, formularios, recibos, escritos) no se muestran: siguen en la ficha.

import re as _re

# Qué significa cada sector, para el equipo (claves de seguimiento_expediente.sector_de).
SECTOR_INTERNO = {
    "estudio_forma": ("Estudio de forma", "El INPI revisa que la solicitud esté completa. Puede salir una vista formal."),
    "estudio_fondo": ("Estudio de fondo", "El INPI analiza si la marca se puede registrar. Puede salir una vista."),
    "analisis_fondo": ("Análisis de fondo", "El INPI analiza la marca en detalle."),
    "publicacion": ("Publicación", "Se publicó en el boletín: corren los 30 días para que terceros se opongan."),
    "notificaciones": ("Notificaciones", "Cerró el plazo de oposiciones con oposición: el INPI notificó "
                                          "oficialmente la oposición. Desde la notificación corren los 3 meses "
                                          "para negociar."),
    "resoluciones": ("Resoluciones", "Cerró el plazo de oposiciones: el expediente espera la resolución "
                                     "(la concesión suele tardar alrededor de un mes)."),
}

SELECT_MOVIMIENTOS = """
    SELECT nv.id, nv.acta, nv.tipo, nv.texto, nv.detectado_en,
           NULLIF(cm.denominacion, '') AS marca, cm.clase, NULLIF(cm.titular, '') AS titular,
           COALESCE(cm.tuvo_oposicion, false) AS tuvo_oposicion,
           COALESCE(nv.cliente_id, cm.cliente_id) AS cliente_id, c.nombre AS cliente
    FROM cartera_novedades nv
    LEFT JOIN cartera_marcas cm ON cm.acta = nv.acta
    LEFT JOIN clientes c ON c.id = COALESCE(nv.cliente_id, cm.cliente_id)
    WHERE nv.tipo IN ('ubicacion', 'estado', 'oposicion', 'movimiento')
"""


def _sin_tildes(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _fechas_inpi(texto: str) -> str:
    """«/Date(1795108680593)/» (fecha cruda del INPI, en UTC) → «14/11/2026» en hora Argentina."""
    def f(m):
        d = _dt.datetime.utcfromtimestamp(int(m.group(1)) / 1000) - _dt.timedelta(hours=3)
        return d.strftime("%d/%m/%Y")
    return _re.sub(r"/?Date\((\d+)\)/?", f, texto or "")


def clasificar_movimiento(f: dict):
    """Para la campanita: None si no es importante; si no, la fila con tipo, ícono, explicación y detalle."""
    tipo, texto = f.get("tipo"), _fechas_inpi(f.get("texto") or "")
    limpio = _sin_tildes(texto)
    base = {**f, "tipo_mov": tipo, "explicacion": "",
            "detalle": texto.replace("Nuevo movimiento en el expediente: ", "En el expediente: ")}

    if tipo == "ubicacion":
        m = _re.search(r"pas[oó] de (.+?) a (.+?)(?: \(desde el ([\d/]+)\))?$", texto)
        destino = m.group(2) if m else texto
        try:
            import seguimiento_expediente as se
            clave = se.sector_de(destino)
        except Exception:
            clave = None
        nombre, que = SECTOR_INTERNO.get(clave, (destino, ""))
        return {**base, "tipo": f"Pasó a {nombre}", "icono": "📍", "explicacion": que,
                "detalle": f"Ubicación actual: {destino}" + (f" (desde el {m.group(3)})" if m and m.group(3) else "")}

    if tipo == "estado":
        if "concedida" in limpio.split("→")[-1]:
            return {**base, "tipo": "Concedida", "icono": "🏅", "explicacion": "El INPI concedió la marca."}
        if "denegada" in limpio.split("→")[-1]:
            return {**base, "tipo": "Denegada", "icono": "⛔", "explicacion": "El INPI denegó la marca."}
        return {**base, "tipo": "Cambio de estado", "icono": "🏁"}

    if tipo == "oposicion":
        es_vista = "vista" in limpio and "opo" not in limpio
        return {**base, "tipo": "Vista" if es_vista else "Oposición recibida", "icono": "👁" if es_vista else "⚠️",
                "explicacion": "", "_grupo": "vista" if es_vista else "oposicion"}

    if tipo == "movimiento":
        if "ratifica" in limpio:
            return None
        if "opo. de marcas" in limpio or "oposicion de marcas" in limpio:
            return {**base, "tipo": "Oposición recibida", "icono": "⚠️",
                    "explicacion": "Un tercero presentó una oposición. Figura en el expediente antes de que el "
                                   "INPI la notifique.", "_grupo": "oposicion"}
        if "vista de marcas" in limpio:
            if f.get("tuvo_oposicion"):
                return {**base, "tipo": "Notificación de la oposición", "icono": "📨",
                        "explicacion": "El INPI notificó oficialmente la oposición (vista + cédula). Desde acá "
                                       "corren los 3 meses para negociar.", "_grupo": "vista"}
            return {**base, "tipo": "Vista del INPI", "icono": "👁",
                    "explicacion": "El INPI emitió una vista: hay que contestarla en plazo.", "_grupo": "vista"}
        return None
    return None


def filtrar_movimientos(filas: list[dict]) -> list[dict]:
    """Clasifica y saca repetidos: una oposición o vista detectada dos veces el mismo día en la misma acta
    (por el aviso general y por el movimiento del expediente) queda una sola vez."""
    vistos, salida = set(), []
    clasificadas = [c for c in (clasificar_movimiento(dict(f)) for f in filas) if c]
    # Primero los movimientos del expediente (traen más detalle) para que ganen la deduplicación.
    for c in sorted(clasificadas, key=lambda c: c["tipo_mov"] != "movimiento"):
        grupo = c.pop("_grupo", None)
        if grupo:
            clave = (c["acta"], grupo, c["detectado_en"].date() if c.get("detectado_en") else None)
            if clave in vistos:
                continue
            vistos.add(clave)
        salida.append(c)
    return sorted(salida, key=lambda c: (c["detectado_en"].timestamp() if c.get("detectado_en") else 0,
                                         c.get("id") or 0), reverse=True)


# ── Mail diario «Cartera y vigilancia» (scripts/notificar_cartera.py) ───────
# Criterio del 22/11/2026: el mail lleva lo mismo que la campanita (ubicación, vista, oposición, concedida…)
# y además los avances del trámite que hace el estudio: toma de intervención, poder y contestación de vista.
# Formularios, recibos sueltos y escritos genéricos no van. El INPI a veces carga dos veces lo mismo (y cada
# presentación trae su «Recibo de Ingreso — …» aparte): se deja una sola línea por acta, avance y fecha.
AVANCES = [
    # (clave, palabras a buscar en el texto sin tildes, título, ícono)
    ("contestacion_vista", ("contestacion de vista",), "Se contestó la vista", "✍️"),
    ("toma_intervencion", ("toma de intervencion", "toma intervencion"), "Se tomó intervención", "🙋"),
    ("poder", ("poder",), "Se presentó el poder", "📝"),
]


def clasificar_para_mail(f: dict):
    """Como clasificar_movimiento, más los avances del trámite. None si no va en el mail."""
    c = clasificar_movimiento(f)
    if c:
        return c
    if f.get("tipo") != "movimiento":
        return None
    texto = _fechas_inpi(f.get("texto") or "")
    limpio = _sin_tildes(texto)
    if "ratifica" in limpio:
        return None
    m = _re.search(r"(\d{2}/\d{2}/\d{4})", texto)
    for clave, palabras, titulo, icono in AVANCES:
        if any(_re.search(r"\b" + _re.escape(p) + r"\b", limpio) for p in palabras):
            return {**f, "tipo_mov": "avance", "tipo": titulo, "icono": icono, "explicacion": "",
                    "detalle": f"En el expediente el {m.group(1)}" if m else "",
                    "_grupo": f"avance:{clave}:{m.group(1) if m else ''}"}
    return None


def _clave_mail(c: dict, grupo):
    avance = (grupo or "").startswith("avance:")
    return (c["acta"], grupo or (c["tipo"], c.get("detalle")),
            None if avance or not c.get("detectado_en") else c["detectado_en"].date())


def filtrar_para_mail(filas: list[dict], ya_avisadas: list[dict] = ()) -> list[dict]:
    """Lista para el mail, sin repetidos (misma acta + mismo avance/oposición/vista + mismo día), en el
    orden en que vienen las filas (cliente, acta, fecha). ya_avisadas: novedades de los últimos días que ya
    salieron en un mail anterior, para no repetir una carga doble del INPI que llegó en otra corrida."""
    vistos, salida = set(), []
    for f in ya_avisadas:
        c = clasificar_para_mail(dict(f))
        if c:
            vistos.add(_clave_mail(c, c.pop("_grupo", None)))
    for f in filas:
        c = clasificar_para_mail(dict(f))
        if not c:
            continue
        clave = _clave_mail(c, c.pop("_grupo", None))
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append(c)
    return salida
