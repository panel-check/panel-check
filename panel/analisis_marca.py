"""
Análisis de marca: la pestaña «Análisis de marca» de la ficha del titular y su
PDF con el membrete de Smarties Consultora.

Qué guarda (tabla analisis_marca, una fila por acta):
  - texto: lo que escribe el equipo (el análisis en sí).
  - oposiciones: la última consulta a INPI de las oposiciones del expediente
    (JSON con la fecha de consulta y la lista). Se consulta la primera vez que
    se abre el análisis de una marca y cada vez que se aprieta «Verificar en
    INPI»; si INPI no responde se muestra lo que ya tenía el sistema.

Qué sale solo (no hay que escribirlo): nombre de la marca, número de acta,
titular con su CUIT entre paréntesis, y cuántas oposiciones tuvo y quién las
hizo. El PDF lleva el mismo membrete (y pie) que el presupuesto de registro de
marca: se dibuja con presupuestos.dibujar_membrete_y_pie.

También guarda el logo de la marca (lo trae el mismo pedido al expediente de INPI;
las marcas sin logo no tienen) y lo usa en la pantalla y en el PDF. El PDF puede
llevar una marca o varias (las del mismo titular que se estén analizando).

Este módulo no abre conexiones: las funciones de base reciben un cursor.
"""

import datetime as _dt
import io
import json
import logging
import re
from xml.sax.saxutils import escape

import archivos_seguros
import inpi_lead
import presupuestos as pres

log = logging.getLogger(__name__)

LARGO_MAX_TEXTO = 20000
RE_ACTA = re.compile(r"^\d{4,9}$")
MAX_OPUESTAS_TOTAL = 6   # marcas de oponentes que se consultan en INPI por análisis (una por acta citada)
MAX_MARCAS_PDF = 30      # tope de marcas en un mismo PDF
LOGO_MAX_LADO = 800      # px: el logo se guarda reducido (alcanza de sobra para el PDF)

# Columnas de `marcas` que usa el análisis.
COLUMNAS_MARCA = (
    "acta, tipo, clase, denominacion, denominacion_inpi, titular, cuit, tuvo_oposicion, "
    "oponente_nombre, oponente_cuit, estado_oposicion, oposicion_fecha_presentacion, "
    "oposicion_fecha_notificacion, oposicion_fecha_vencimiento, oposicion_fecha_levantamiento, "
    "oposicion_agente_oponente"
)


# ── Tabla ─────────────────────────────────────────────────────────────────
def crear_tablas(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS analisis_marca (
            acta             TEXT PRIMARY KEY,
            texto            TEXT NOT NULL DEFAULT '',
            oposiciones      JSONB,
            actualizado_por  TEXT,
            actualizado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Logo de la marca, tal como figura en el expediente de INPI (reducido y sin metadatos).
    # logo_consultado_en vacío = todavía no se miró el expediente en busca del logo.
    cur.execute("ALTER TABLE analisis_marca ADD COLUMN IF NOT EXISTS logo BYTEA")
    cur.execute("ALTER TABLE analisis_marca ADD COLUMN IF NOT EXISTS logo_mime TEXT")
    cur.execute("ALTER TABLE analisis_marca ADD COLUMN IF NOT EXISTS logo_consultado_en TIMESTAMPTZ")
    # Datos de la marca tal como figuran en el expediente (denominación, tipo de marca,
    # limitación, publicación). NULL = todavía no se leyeron.
    cur.execute("ALTER TABLE analisis_marca ADD COLUMN IF NOT EXISTS expediente JSONB")
    # Marcas de los oponentes (la que cita el fundamento de cada oposición), una fila por acta:
    # sus datos y logo tal como figuran en su expediente de INPI. Se comparten entre análisis.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS analisis_marca_opuesta (
            acta           TEXT PRIMARY KEY,
            datos          JSONB NOT NULL,
            logo           BYTEA,
            logo_mime      TEXT,
            consultado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


# ── Formatos ──────────────────────────────────────────────────────────────
def fmt_cuit(cuit) -> str:
    d = re.sub(r"\D", "", str(cuit or ""))
    return f"{d[:2]}-{d[2:10]}-{d[10]}" if len(d) == 11 else d


def fmt_fecha(iso) -> str:
    """'2026-09-15' (o un date) -> '15/09/2026'; vacío -> ''."""
    if not iso:
        return ""
    if isinstance(iso, (_dt.date, _dt.datetime)):
        return iso.strftime("%d/%m/%Y")
    s = str(iso)[:10]
    return f"{s[8:10]}/{s[5:7]}/{s[:4]}" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else s


def nombre_marca(m: dict) -> str:
    nombre = (m.get("denominacion_inpi") or m.get("denominacion") or "").strip()
    if nombre:
        return nombre
    return {"F": "(figurativa, sin texto)", "M": "(mixta, nombre sin cargar)"}.get(m.get("tipo"), "(sin nombre cargado)")


def titular_con_cuit(m: dict) -> str:
    titular = " ".join(str(m.get("titular") or "").split()) or "(titular sin identificar)"
    cuit = fmt_cuit(m.get("cuit"))
    return f"{titular} (CUIT {cuit})" if cuit else f"{titular} (CUIT no disponible)"


# ── Oposiciones ───────────────────────────────────────────────────────────
def oposiciones_del_sistema(m: dict) -> list:
    """Lo que el sistema ya sabe de la oposición de esa marca (una sola, la que
    detectó la revisión de oposiciones). Sirve de respaldo cuando INPI no
    responde: no se puede asegurar que sea la cantidad total."""
    if not m.get("tuvo_oposicion"):
        return []
    oponente = (m.get("oponente_nombre") or "").strip() or None
    cuit_op = re.sub(r"\D", "", str(m.get("oponente_cuit") or ""))
    if oponente and len(cuit_op) == 11:
        oponente = f"{oponente} (CUIT {fmt_cuit(cuit_op)})"
    iso = lambda v: v.isoformat() if hasattr(v, "isoformat") else (v or None)  # noqa: E731
    return [{
        "oponente": oponente,
        "numero": None,
        "presentacion": iso(m.get("oposicion_fecha_presentacion")),
        "notificacion": iso(m.get("oposicion_fecha_notificacion")),
        "vencimiento": iso(m.get("oposicion_fecha_vencimiento")),
        "levantamiento": iso(m.get("oposicion_fecha_levantamiento")),
        "agente_oponente": m.get("oposicion_agente_oponente") or None,
    }]


def armar_oposiciones(m: dict, snapshot) -> dict:
    """Lo que se muestra (y va al PDF): {origen: 'inpi'|'sistema', cantidad,
    items, consultado_en}. Si hay una consulta a INPI guardada manda esa; si
    no, lo que el sistema ya tenía."""
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except ValueError:
            snapshot = None
    if isinstance(snapshot, dict) and isinstance(snapshot.get("items"), list):
        items = [dict(i) for i in snapshot["items"]]
        # Si INPI no informa el nombre del oponente y el sistema sí (leído del
        # formulario de la oposición), y hay una sola oposición, se completa.
        if len(items) == 1 and not items[0].get("oponente"):
            propio = oposiciones_del_sistema(m)
            if propio and propio[0].get("oponente"):
                items[0]["oponente"] = propio[0]["oponente"]
        return {"origen": "inpi", "cantidad": len(items), "items": items,
                "consultado_en": snapshot.get("consultado_en")}
    items = oposiciones_del_sistema(m)
    return {"origen": "sistema", "cantidad": len(items), "items": items, "consultado_en": None}


def linea_oposicion(i: dict) -> str:
    """'O'BRIEN SA — presentada el 15/09/2026, notificada el …, vence el …'."""
    partes = []
    if i.get("presentacion"):
        partes.append(f"presentada el {fmt_fecha(i['presentacion'])}")
    if i.get("notificacion"):
        partes.append(f"notificada el {fmt_fecha(i['notificacion'])}")
    if i.get("vencimiento"):
        partes.append(f"el plazo vence el {fmt_fecha(i['vencimiento'])}")
    if i.get("levantamiento"):
        partes.append(f"levantada el {fmt_fecha(i['levantamiento'])}")
    if i.get("agente_oponente"):
        partes.append(f"agente del oponente: {i['agente_oponente']}")
    quien = i.get("oponente") or "oponente no informado"
    return quien + (" — " + ", ".join(partes) if partes else "")


def resumen_oposiciones(op: dict) -> str:
    n = op["cantidad"]
    if n == 0:
        return "Ninguna registrada"
    return f"{n} oposición" if n == 1 else f"{n} oposiciones"


# ── Base ──────────────────────────────────────────────────────────────────
def _valor(fila, clave, i=0):
    return fila[clave] if isinstance(fila, dict) else fila[i]


def leer_marca(cur, acta: str):
    cur.execute(f"SELECT {COLUMNAS_MARCA} FROM marcas WHERE acta = %s", (acta,))
    return cur.fetchone()


def leer_analisis(cur, acta: str) -> dict:
    cur.execute(
        "SELECT texto, oposiciones, actualizado_por, actualizado_en, logo_consultado_en, "
        "(logo IS NOT NULL) AS tiene_logo, expediente FROM analisis_marca WHERE acta = %s", (acta,))
    f = cur.fetchone()
    if not f:
        return {"texto": "", "oposiciones": None, "actualizado_por": None, "actualizado_en": None,
                "logo_consultado": False, "tiene_logo": False, "expediente": None}
    return {"texto": _valor(f, "texto", 0) or "", "oposiciones": _valor(f, "oposiciones", 1),
            "actualizado_por": _valor(f, "actualizado_por", 2), "actualizado_en": _valor(f, "actualizado_en", 3),
            "logo_consultado": bool(_valor(f, "logo_consultado_en", 4)), "tiene_logo": bool(_valor(f, "tiene_logo", 5)),
            "expediente": _json_o_none(_valor(f, "expediente", 6))}


def _json_o_none(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, dict) else None


def leer_logo(cur, acta: str):
    """(bytes, mime) del logo guardado, o None."""
    cur.execute("SELECT logo, logo_mime FROM analisis_marca WHERE acta = %s", (acta,))
    f = cur.fetchone()
    if not f or _valor(f, "logo", 0) is None:
        return None
    return bytes(_valor(f, "logo", 0)), _valor(f, "logo_mime", 1) or "image/png"


def leer_resumen_lote(cur, actas: list) -> dict:
    """{acta: {tiene_texto, actualizado_en, consultada}}: cuáles ya tienen análisis escrito y
    cuáles ya se consultaron en INPI (oposiciones y logo). Las que no están en la tabla no figuran."""
    cur.execute(
        "SELECT acta, (btrim(texto) <> '') AS tiene_texto, actualizado_en, "
        "(oposiciones IS NOT NULL AND logo_consultado_en IS NOT NULL AND expediente IS NOT NULL) AS consultada, "
        "oposiciones FROM analisis_marca WHERE acta = ANY(%s)",
        (list(actas),))
    out, snaps = {}, {}
    for f in cur.fetchall():
        a = str(_valor(f, "acta", 0))
        e = _valor(f, "actualizado_en", 2)
        out[a] = {"tiene_texto": bool(_valor(f, "tiene_texto", 1)),
                  "actualizado_en": e.isoformat() if e else None,
                  "consultada": bool(_valor(f, "consultada", 3))}
        snaps[a] = _valor(f, "oposiciones", 4)
    # Una marca no está «consultada» del todo si falta leer la marca de algún oponente.
    for a, e in out.items():
        if e["consultada"] and pendientes_de_snapshot(cur, a, snaps[a]):
            e["consultada"] = False
    return out


def validar_texto(texto) -> str:
    t = str(texto or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(t) > LARGO_MAX_TEXTO:
        raise ValueError(f"El análisis es demasiado largo (máximo {LARGO_MAX_TEXTO} caracteres)")
    return t


def guardar_texto(cur, acta: str, texto: str, usuario: str):
    cur.execute(
        "INSERT INTO analisis_marca (acta, texto, actualizado_por, actualizado_en) VALUES (%s, %s, %s, now()) "
        "ON CONFLICT (acta) DO UPDATE SET texto = EXCLUDED.texto, actualizado_por = EXCLUDED.actualizado_por, "
        "actualizado_en = now()",
        (acta, texto, usuario),
    )


def guardar_oposiciones(cur, acta: str, items: list) -> dict:
    """Guarda la consulta a INPI (sin tocar el texto ni quién lo editó)."""
    snapshot = {"consultado_en": _dt.datetime.now(_dt.timezone.utc).isoformat(), "items": items}
    cur.execute(
        "INSERT INTO analisis_marca (acta, oposiciones) VALUES (%s, %s::jsonb) "
        "ON CONFLICT (acta) DO UPDATE SET oposiciones = EXCLUDED.oposiciones",
        (acta, json.dumps(snapshot, ensure_ascii=False)),
    )
    return snapshot


def preparar_logo(datos: bytes, mime: str):
    """Valida y limpia el logo que vino de INPI: imagen nueva solo con píxeles (sin
    metadatos), reducida a LOGO_MAX_LADO. Devuelve (bytes, mime) o None si no sirve."""
    from PIL import Image

    try:
        limpio, mime_limpio = archivos_seguros.limpiar_imagen(datos, mime)
        with Image.open(io.BytesIO(limpio)) as im:
            im.load()
            if max(im.size) <= LOGO_MAX_LADO:
                return limpio, mime_limpio
            im.thumbnail((LOGO_MAX_LADO, LOGO_MAX_LADO), Image.LANCZOS)
            salida = io.BytesIO()
            if im.mode in ("RGBA", "LA", "P"):
                im.convert("RGBA").save(salida, "PNG", optimize=True)
                return salida.getvalue(), "image/png"
            im.convert("RGB").save(salida, "JPEG", quality=92, optimize=True)
            return salida.getvalue(), "image/jpeg"
    except Exception as e:  # noqa: BLE001 — un logo roto no debe romper el análisis
        log.warning("Logo de INPI descartado: %s", e)
        return None


def guardar_expediente(cur, acta: str, expediente: dict):
    """Guarda los datos de la marca que figuran en el expediente (sin tocar lo demás)."""
    cur.execute(
        "INSERT INTO analisis_marca (acta, expediente) VALUES (%s, %s::jsonb) "
        "ON CONFLICT (acta) DO UPDATE SET expediente = EXCLUDED.expediente",
        (acta, json.dumps(expediente or {}, ensure_ascii=False)),
    )


# ── Marcas de los oponentes ───────────────────────────────────────────────
def actas_opuestas(items: list, acta_propia: str) -> list:
    """Actas de las marcas de los oponentes que citan los fundamentos de las oposiciones
    (sin repetir, en orden y con tope), listas para consultar en INPI."""
    out = []
    for i in items or []:
        for a in inpi_lead.marcas_opuestas_citadas(i.get("fundamento"), acta_propia)["actas"]:
            if a not in out:
                out.append(a)
    return out[:MAX_OPUESTAS_TOTAL]


def guardar_opuesta(cur, acta: str, estado: str, datos, logo):
    """Guarda lo leído del expediente de la marca de un oponente. estado: 'ok' | 'no_existe'."""
    prep = preparar_logo(*logo) if (logo and estado == "ok") else None
    cur.execute(
        "INSERT INTO analisis_marca_opuesta (acta, datos, logo, logo_mime, consultado_en) "
        "VALUES (%s, %s::jsonb, %s, %s, now()) "
        "ON CONFLICT (acta) DO UPDATE SET datos = EXCLUDED.datos, logo = EXCLUDED.logo, "
        "logo_mime = EXCLUDED.logo_mime, consultado_en = now()",
        (acta, json.dumps({**(datos or {}), "estado": estado}, ensure_ascii=False),
         prep[0] if prep else None, prep[1] if prep else None),
    )
    return bool(prep)


def leer_opuestas(cur, actas: list, con_logo: bool = False) -> dict:
    """{acta: {estado, denominacion, tipo_marca, clase, proteccion, limitacion, agente, caracter,
    particular, tiene_logo[, logo]}} de las que ya se consultaron."""
    if not actas:
        return {}
    cur.execute(
        "SELECT acta, datos, (logo IS NOT NULL) AS tiene_logo, logo, logo_mime FROM analisis_marca_opuesta "
        "WHERE acta = ANY(%s)", (list(actas),))
    out = {}
    for f in cur.fetchall():
        datos = _json_o_none(_valor(f, "datos", 1)) or {}
        e = dict(datos, tiene_logo=bool(_valor(f, "tiene_logo", 2)))
        if con_logo and e["tiene_logo"]:
            e["logo"] = (bytes(_valor(f, "logo", 3)), _valor(f, "logo_mime", 4) or "image/png")
        out[str(_valor(f, "acta", 0))] = e
    return out


def leer_logo_opuesta(cur, acta: str):
    cur.execute("SELECT logo, logo_mime FROM analisis_marca_opuesta WHERE acta = %s", (acta,))
    f = cur.fetchone()
    if not f or _valor(f, "logo", 0) is None:
        return None
    return bytes(_valor(f, "logo", 0)), _valor(f, "logo_mime", 1) or "image/png"


def _texto_agente(e: dict):
    """El agente de la marca del oponente tal como figura en su expediente."""
    if e.get("particular"):
        return "Particular (sin agente)"
    ag = (e.get("agente") or "").strip()
    if not ag:
        return None
    return f"{ag} ({e['caracter']})" if e.get("caracter") else ag


def adjuntar_opuestas(cur, acta_propia: str, op: dict, con_logo: bool = False):
    """Agrega a cada oposición los datos de la marca del oponente que cita su fundamento.
    Devuelve (op, pendientes): `pendientes` son las actas citadas que todavía no se
    consultaron en INPI. El agente de la marca del oponente (`agente_mostrar`) solo se
    completa cuando la oposición no trae el agente del oponente."""
    citadas = [inpi_lead.marcas_opuestas_citadas(i.get("fundamento"), acta_propia) for i in op["items"]]
    actas = []
    for c in citadas:
        for a in c["actas"]:
            if a not in actas:
                actas.append(a)
    actas = actas[:MAX_OPUESTAS_TOTAL]
    guardadas = leer_opuestas(cur, actas, con_logo)
    items = []
    for i, c in zip(op["items"], citadas):
        i = dict(i)
        marcas = []
        for a in c["actas"]:
            if a not in actas:
                continue
            e = guardadas.get(a)
            if not e:
                continue
            m = {"acta": a, "url": url_expediente(a), "estado": e.get("estado"), "tiene_logo": e.get("tiene_logo", False),
                 "denominacion": e.get("denominacion"), "tipo_marca": e.get("tipo_marca"), "clase": e.get("clase"),
                 "proteccion": e.get("proteccion"), "limitacion": e.get("limitacion"),
                 "agente_mostrar": None if i.get("agente_oponente") else _texto_agente(e)}
            if con_logo and e.get("logo"):
                m["logo"] = e["logo"]
            marcas.append(m)
        i["marcas_opuestas"] = marcas
        i["marca_citada"] = c["citada"]
        items.append(i)
    pendientes = [a for a in actas if a not in guardadas]
    return dict(op, items=items), pendientes


def pendientes_de_snapshot(cur, acta: str, snapshot) -> list:
    """Actas de marcas de oponentes citadas por una consulta ya guardada que aún no se leyeron."""
    snap = snapshot
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except ValueError:
            return []
    if not isinstance(snap, dict) or not isinstance(snap.get("items"), list):
        return []
    actas = actas_opuestas(snap["items"], acta)
    return [a for a in actas if a not in leer_opuestas(cur, actas)]


def guardar_logo(cur, acta: str, logo):
    """Guarda el logo del expediente (`logo` = (bytes, mime) de inpi_lead, o None si la
    marca no tiene). Siempre deja anotado que ya se miró el expediente."""
    prep = preparar_logo(*logo) if logo else None
    cur.execute(
        "INSERT INTO analisis_marca (acta, logo, logo_mime, logo_consultado_en) VALUES (%s, %s, %s, now()) "
        "ON CONFLICT (acta) DO UPDATE SET logo = EXCLUDED.logo, logo_mime = EXCLUDED.logo_mime, "
        "logo_consultado_en = now()",
        (acta, prep[0] if prep else None, prep[1] if prep else None),
    )
    return bool(prep)


def url_expediente(acta) -> str:
    """Link público al expediente de la marca en INPI (el mismo que usa el resto del panel)."""
    return f"https://portaltramites.inpi.gob.ar/MarcasConsultas/Resultado?acta={re.sub(r'[^0-9]', '', str(acta))}"


def clase_texto(m: dict) -> str:
    c = m.get("clase")
    return str(c) if c not in (None, "") else "no informada"


TIPOS_MARCA = {"D": "Denominativa", "M": "Mixta", "F": "Figurativa", "T": "Tridimensional"}


def datos_expediente(m: dict, expediente) -> dict:
    """Lo que se muestra de la marca: denominación y tipo salen del expediente de INPI si
    ya se leyó (y si no, de lo que el sistema sabe); limitación y publicación solo
    existen si el expediente se leyó."""
    e = expediente if isinstance(expediente, dict) else {}
    pubs = []
    for p in e.get("publicaciones") or []:
        if isinstance(p, dict) and (p.get("fecha") or p.get("numero")):
            pubs.append({"fecha": fmt_fecha(p.get("fecha")), "numero": p.get("numero") or "",
                         "url": p.get("url") or None, "tipo": p.get("tipo") or ""})
    return {
        "denominacion": (e.get("denominacion") or "").strip() or nombre_marca(m),
        "tipo_marca": (e.get("tipo_marca") or "").strip() or TIPOS_MARCA.get(m.get("tipo"), ""),
        "limitacion": (e.get("limitacion") or "").strip(),
        "publicaciones": pubs,
    }


def linea_publicacion(p: dict) -> str:
    """'Fecha 02/09/2026 · Boletín N.º 11110 · Tipo: Nueva' (sin el link)."""
    partes = []
    if p.get("fecha"):
        partes.append(f"Fecha {p['fecha']}")
    if p.get("numero"):
        partes.append(f"Boletín N.º {p['numero']}")
    if p.get("tipo"):
        partes.append(f"Tipo: {p['tipo']}")
    return " · ".join(partes)


def datos_para_pdf(m: dict, texto: str, op: dict, logo=None, expediente=None) -> dict:
    e = datos_expediente(m, expediente)
    return {
        "acta": str(m["acta"]),
        "url_acta": url_expediente(m["acta"]),
        "marca": e["denominacion"],
        "tipo_marca": e["tipo_marca"],
        "limitacion": e["limitacion"],
        "publicaciones": e["publicaciones"],
        "clase": clase_texto(m),
        "logo": logo,   # (bytes, mime) o None
        "titular": titular_con_cuit(m),
        "oposiciones": op,
        "texto": texto or "",
        "fecha": pres.hoy_ar().isoformat(),
    }


def nombre_archivo(d) -> str:
    """Nombre del PDF. `d` es una marca (dict) o una lista de marcas."""
    if isinstance(d, list):
        if len(d) == 1:
            return nombre_archivo(d[0])
        quien = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ ]+", " ", d[0].get("titular", "").split(" (CUIT")[0])
        quien = " ".join(quien.split())[:40]
        return f"Analisis de marcas - {quien} - {len(d)} marcas.pdf" if quien else f"Analisis de marcas - {len(d)} marcas.pdf"
    m = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ ]+", " ", d.get("marca") or "")
    m = " ".join(m.split())[:40]
    return f"Analisis de marca - {m} - acta {d['acta']}.pdf" if m else f"Analisis de marca - acta {d['acta']}.pdf"


# ── PDF ───────────────────────────────────────────────────────────────────
_RE_VINETA = re.compile(r"^\s*(?:[-*•]\s+)(.*)$")


def _bloques_de_texto(texto: str):
    """Parte el texto escrito en bloques: ('p', texto) o ('v', texto de viñeta).
    Una línea en blanco separa párrafos; un salto simple queda como salto de
    línea; las líneas que empiezan con «- », «* » o «• » son viñetas."""
    bloques = []
    for parrafo in re.split(r"\n\s*\n", texto.strip("\n")):
        lineas = parrafo.split("\n")
        actual = []
        for ln in lineas:
            v = _RE_VINETA.match(ln)
            if v:
                if actual:
                    bloques.append(("p", "\n".join(actual)))
                    actual = []
                bloques.append(("v", v.group(1)))
            else:
                actual.append(ln)
        if actual and "".join(actual).strip():
            bloques.append(("p", "\n".join(actual)))
    return bloques


def _xml(t: str) -> str:
    return escape(t).replace("\n", "<br/>")


def generar_pdf(datos, texto_comun=None) -> bytes:
    """El análisis en PDF (A4, con el membrete de Smarties). `datos` es una marca
    (ver datos_para_pdf) o una lista de marcas para un PDF con varias, una a continuación
    de la otra. Si el texto es largo sigue en más hojas, todas con membrete.
    `texto_comun` (solo con varias marcas): el análisis es el mismo para todas, así que sale
    UNA sola vez al final, en lugar del texto propio de cada marca."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, HRFlowable, Image, PageTemplate,
                                    Paragraph, Spacer, Table, TableStyle)

    marcas = list(datos) if isinstance(datos, (list, tuple)) else [datos]
    if not marcas:
        raise ValueError("No hay marcas para el PDF")
    varias = len(marcas) > 1
    comun = texto_comun is not None and varias

    F = pres._fuentes()
    esc = F["escala"]
    _, alto = A4

    def estilo(nombre, fuente="r", size=11, leading=15.75, alin=TA_LEFT, color=pres.COLOR_TEXTO, **kw):
        tam = size * esc * (F["escala_b"] if fuente == "b" else 1.0)
        return ParagraphStyle(nombre, fontName=F[fuente], fontSize=tam, leading=leading,
                              alignment=alin, textColor=HexColor(color), **kw)

    titulo = estilo("titulo", "b", 20, 24, TA_CENTER, pres.COLOR_TITULO)
    dato = estilo("dato", leftIndent=0)
    dato_op = estilo("dato_op", leftIndent=18.7, bulletIndent=6, bulletFontName=F["r"], bulletFontSize=11 * esc)
    chico = estilo("chico", "i", 9, 12.8)
    seccion = estilo("seccion", "b", 12, 17, TA_LEFT, pres.COLOR_SUBTITULO)
    marca_n = estilo("marca_n", "b", 13, 18, TA_LEFT, pres.COLOR_TITULO)
    cuerpo = estilo("cuerpo", alin=TA_JUSTIFY)
    fundamento = estilo("fundamento", size=10, leading=14, alin=TA_JUSTIFY, leftIndent=18.7)
    opuesta = estilo("opuesta", size=10, leading=14, leftIndent=18.7)
    vineta = estilo("vineta", alin=TA_JUSTIFY, leftIndent=18.7, bulletIndent=6, bulletFontName=F["r"],
                    bulletFontSize=11 * esc)

    ANCHO = 520.1 - 75.4
    LOGO_W, LOGO_H = 150.0, 90.0   # caja máxima del logo (se achica sin deformarlo)

    def imagen_logo(logo, caja_w=None, caja_h=None):
        """El logo ajustado a la caja, o None si no hay o no se puede leer."""
        if not logo:
            return None
        try:
            ancho, alto_px = ImageReader(io.BytesIO(logo[0])).getSize()
            k = min((caja_w or LOGO_W) / ancho, (caja_h or LOGO_H) / alto_px)
            img = Image(io.BytesIO(logo[0]), width=ancho * k, height=alto_px * k)
            img.hAlign = "RIGHT"
            return img
        except Exception:  # noqa: BLE001 — un logo ilegible no tiene que impedir el PDF
            log.warning("No se pudo dibujar el logo de la marca en el PDF", exc_info=True)
            return None

    OPUESTA_LOGO_W, OPUESTA_LOGO_H = 90.0, 56.0   # el logo de la marca del oponente va más chico

    def link_acta(acta, url):
        x = _xml(acta)
        return f'<a href="{escape(url, {chr(34): "&quot;"})}" color="{pres.COLOR_SUBTITULO}">{x}</a>' if url else x

    def bloque_opuesta(mo):
        """Datos de la marca de un oponente (la que cita el fundamento de su oposición)."""
        acta_x = link_acta(mo["acta"], mo.get("url"))
        if mo.get("estado") == "no_existe":
            return [Paragraph(f"<b>Marca del oponente:</b> Acta {acta_x} (no se encontró el expediente en INPI)", opuesta)]
        ps = [Paragraph(f"<b>Marca del oponente:</b> {_xml(mo.get('denominacion') or 'sin denominación')} · Acta {acta_x}", opuesta)]
        for etiqueta, clave in (("Tipo de marca", "tipo_marca"), ("Clase", "clase"), ("Protección", "proteccion"),
                                ("Limitaciones", "limitacion"), ("Agente", "agente_mostrar")):
            if mo.get(clave):
                ps.append(Paragraph(f"<b>{etiqueta}:</b> {_xml(mo[clave])}", opuesta))
        img = imagen_logo(mo.get("logo"), OPUESTA_LOGO_W, OPUESTA_LOGO_H)
        if img is None:
            return ps
        t = Table([[ps, img]], colWidths=[ANCHO - OPUESTA_LOGO_W - 12, OPUESTA_LOGO_W + 12])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        return [t]

    def flujo_analisis(texto, titulo_seccion):
        """Título «Análisis» y el texto escrito (párrafos y viñetas)."""
        out = [Paragraph(titulo_seccion, seccion), Spacer(1, 4)]
        bloques = _bloques_de_texto(texto)
        if not bloques:
            out.append(Paragraph("(Sin análisis escrito.)", chico))
        previo = None
        for tipo, t in bloques:
            if tipo == "v":
                out.append(Paragraph(_xml(t), vineta, bulletText="•"))
                out.append(Spacer(1, 3))
            else:
                if previo == "v":
                    out.append(Spacer(1, 5))  # un poco de aire al volver al texto después de una lista
                out.append(Paragraph(_xml(t), cuerpo))
                out.append(Spacer(1, 8))
            previo = tipo
        return out

    h = [Paragraph("Análisis de marcas" if varias else "Análisis de marca", titulo), Spacer(1, 22)]

    for n, d in enumerate(marcas, 1):
        op = d["oposiciones"]
        if varias:
            if n > 1:
                h.append(Spacer(1, 14))
                h.append(HRFlowable(width="100%", thickness=0.6, color=HexColor(pres.COLOR_SUBTITULO)))
                h.append(Spacer(1, 12))
            h.append(CondPageBreak(230))
            h.append(Paragraph(f"Marca {n} de {len(marcas)}", marca_n))
            h.append(Spacer(1, 6))

        datos_p = [Paragraph(f"<b>Denominación:</b> {_xml(d['marca'])}", dato)]
        if d.get("tipo_marca"):
            datos_p.append(Paragraph(f"<b>Tipo de marca:</b> {_xml(d['tipo_marca'])}", dato))
        acta_xml = _xml(d["acta"])
        if d.get("url_acta"):
            acta_xml = f'<a href="{escape(d["url_acta"], {chr(34): "&quot;"})}" color="{pres.COLOR_SUBTITULO}">{acta_xml}</a>'
        datos_p.append(Paragraph(f"<b>Acta:</b> {acta_xml}", dato))
        datos_p.append(Paragraph(f"<b>Clase:</b> {_xml(d.get('clase') or 'no informada')}", dato))
        if d.get("limitacion"):
            datos_p.append(Paragraph(f"<b>Limitaciones:</b> {_xml(d['limitacion'])}", dato))
        datos_p.append(Paragraph(f"<b>Titular:</b> {_xml(d['titular'])}", dato))
        img = imagen_logo(d.get("logo"))
        if img is not None:
            t = Table([[datos_p, img]], colWidths=[ANCHO - LOGO_W - 12, LOGO_W + 12])
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
            h.append(t)
        else:
            h.extend(datos_p)
        pubs = d.get("publicaciones") or []

        def pub_xml(p):
            partes = []
            if p.get("fecha"):
                partes.append(f"Fecha {_xml(p['fecha'])}")
            if p.get("numero"):
                num = _xml(p["numero"])
                if p.get("url"):
                    num = f'<a href="{escape(p["url"], {chr(34): "&quot;"})}" color="{pres.COLOR_SUBTITULO}">{num}</a>'
                partes.append(f"Boletín N.º {num}")
            if p.get("tipo"):
                partes.append(f"Tipo: {_xml(p['tipo'])}")
            return " · ".join(partes)

        if len(pubs) == 1:
            h.append(Paragraph(f"<b>Publicación:</b> {pub_xml(pubs[0])}", dato))
        elif pubs:
            h.append(Paragraph("<b>Publicaciones:</b>", dato))
            for k, p in enumerate(pubs, 1):
                h.append(Paragraph(pub_xml(p), dato_op, bulletText=f"{k}."))
        h.append(Paragraph(f"<b>Oposiciones:</b> {_xml(resumen_oposiciones(op))}", dato))
        for k, i in enumerate(op["items"], 1):
            h.append(Paragraph(_xml(linea_oposicion(i)), dato_op, bulletText=f"{k}."))
            if i.get("fundamento"):
                h.append(Paragraph(f"<b>Fundamento:</b> {_xml(i['fundamento'])}", fundamento))
                h.append(Spacer(1, 4))
            for mo in i.get("marcas_opuestas") or []:
                h.extend(bloque_opuesta(mo))
                h.append(Spacer(1, 4))
            if i.get("marca_citada") and not i.get("marcas_opuestas"):
                h.append(Paragraph(f"<b>Marca del oponente (citada en el fundamento):</b> {_xml(i['marca_citada'])}", opuesta))
                h.append(Spacer(1, 4))
        if op["origen"] == "sistema":
            h.append(Spacer(1, 3))
            h.append(Paragraph("Dato del sistema: no se verificó contra el expediente de INPI.", chico))

        if not comun:
            h.append(Spacer(1, 16))
            h.extend(flujo_analisis(d.get("texto") or "", "Análisis"))

    if comun:
        h.append(Spacer(1, 14))
        h.append(HRFlowable(width="100%", thickness=0.6, color=HexColor(pres.COLOR_SUBTITULO)))
        h.append(Spacer(1, 12))
        h.append(CondPageBreak(150))
        h.extend(flujo_analisis(texto_comun or "", f"Análisis (el mismo para las {len(marcas)} marcas)"))

    cuando = pres.mes_anio(_dt.date.fromisoformat(marcas[0]["fecha"]))

    def decorar(c: _canvas.Canvas, doc):
        pres.dibujar_membrete_y_pie(c, cuando)

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, title="Análisis de marcas" if varias else "Análisis de marca",
                          author=pres.MARCA_NOMBRE)
    # Mismo arranque que el presupuesto: el título apoya su base 167.7 pt bajo el borde de arriba;
    # el marco termina antes del pie.
    arriba = 167.7 - pres.AJUSTE - titulo.fontSize
    marco = Frame(75.4, 70.0, 520.1 - 75.4, alto - arriba - 70.0, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0, id="cuerpo")
    doc.addPageTemplates([PageTemplate(id="pagina", frames=[marco], onPage=decorar)])
    doc.build(h)
    return buf.getvalue()
