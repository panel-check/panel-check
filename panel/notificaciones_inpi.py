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
# La revisión diaria de la cartera (revisar_cartera.yml, 23:35) ya guarda cada cambio que encuentra en el
# expediente de una marca de cliente. La campanita los muestra junto con las notificaciones del INPI (pedido
# de Pame, 19/11/2026). tipo → (nombre, ícono)
TIPOS_MOVIMIENTO = {
    "estado": ("Cambio de estado", "🏁"),
    "oposicion": ("Oposición o vista", "⚠️"),
    "publicacion": ("Publicada en el boletín", "📰"),
    "titular": ("Cambio de titular", "👤"),
    "ubicacion": ("Cambió de sector", "📍"),
    "movimiento": ("Movimiento en el expediente", "🗂"),
    "alta_auto": ("Marca sumada a Clientes", "➕"),
}

SELECT_MOVIMIENTOS = """
    SELECT nv.id, nv.acta, nv.tipo, nv.texto, nv.detectado_en,
           NULLIF(cm.denominacion, '') AS marca, cm.clase, NULLIF(cm.titular, '') AS titular,
           COALESCE(nv.cliente_id, cm.cliente_id) AS cliente_id, c.nombre AS cliente
    FROM cartera_novedades nv
    LEFT JOIN cartera_marcas cm ON cm.acta = nv.acta
    LEFT JOIN clientes c ON c.id = COALESCE(nv.cliente_id, cm.cliente_id)
"""


def describir_movimiento(fila: dict) -> dict:
    nombre, icono = TIPOS_MOVIMIENTO.get(fila.get("tipo") or "", ("Novedad del expediente", "🗂"))
    return {**fila, "tipo_mov": fila.get("tipo"), "tipo": nombre, "icono": icono}
