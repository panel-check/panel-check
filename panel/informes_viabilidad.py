"""
Informe preliminar de disponibilidad marcaria (antes «de viabilidad»: el
informe gratuito que se manda antes de que el cliente contrate el registro).

El análisis lo hace la IA (skill «informe-viabilidad-marcaria») y entrega dos JSON:
  - el del CLIENTE: el texto del informe. Se pega en la ficha del cliente
    (pestaña Informes → «Generar informe de disponibilidad marcaria») y el
    panel arma el PDF.
  - el INTERNO: todo lo que encontró la IA (búsquedas, antecedentes, clases,
    revisión legal, observaciones para Pame). Se adjunta con «Adjuntar
    informe IA interno» y queda guardado para el análisis completo.

Formatos del JSON del cliente:
  - versión 2 (desde el 28/10/2026, criterios consolidados de Pame, modelo
    ATELIER ASTRANITY INFPR-1847): «Informe preliminar de disponibilidad
    marcaria», limitado a coincidencias exactas, con referencia INFPR, cuatro
    secciones con título, firma de cinco líneas, Calibri (Carlito, que tiene
    las mismas medidas), paleta del manual de Smarties y logo de trama arriba a
    la derecha. Si el JSON no trae la referencia, la asigna el panel.
  - versión 1 (formato anterior, modelo SABER LIBROS): se sigue aceptando y
    los informes ya guardados se siguen viendo igual.

Los dos se guardan en la tabla informes_viabilidad (uno por fila, tipo
'cliente' o 'interno'). Este módulo no abre conexiones: recibe cursores.
"""

import datetime as _dt
import io
import json
import os
import re
from xml.sax.saxutils import escape

RECURSOS = os.path.join(os.path.dirname(__file__), "recursos")
FUENTES = os.path.join(RECURSOS, "fuentes")
MARCA_AGUA = os.path.join(RECURSOS, "marca-agua-smarties.png")
LOGO_TRAMA = os.path.join(RECURSOS, "logo-trama-smarties.png")

TITULO_V2 = "Informe preliminar de disponibilidad marcaria"
FIRMA_V2 = {
    "nombre": "Pamela Guzzardi",
    "lineas": [
        "Agente de Propiedad Industrial – Mat. INPI 2906",
        "Smarties Consultora – Registro de Marcas Nacional e Internacional",
        "info@smartiesconsultora.com.ar",
        "+54 9 11 5589-0784",
    ],
}
# Paleta del manual de marca de Smarties (criterios del 28/10/2026).
AZUL, GRIS, CELESTE = "#161250", "#606060", "#0095FF"

# Referencias de consulta: INFPR-<número correlativo>. La primera de la serie
# fue INFPR-1847 (ATELIER ASTRANITY); el panel sigue desde la más alta usada.
PREFIJO_REF = "INFPR-"
PRIMERA_REF = 1847
_RE_REF = re.compile(r"^\s*INFPR\s*[-–—]?\s*(\d{1,7})\s*$", re.IGNORECASE)

# Expresiones que los criterios de Pame prohíben en el informe al cliente.
_PROHIBIDAS = [
    (re.compile(r"es una buena se[ñn]al", re.I), "«es una buena señal»"),
    (re.compile(r"la marca est[aá] libre", re.I), "«la marca está libre»"),
    (re.compile(r"disponible al 100", re.I), "«disponible al 100 %»"),
    (re.compile(r"ni la definici[oó]n final de la cobertura", re.I), "«ni la definición final de la cobertura»"),
    (re.compile(r"riesgo (muy )?(bajo|medio|alto)", re.I), "un nivel de riesgo («riesgo bajo», «riesgo alto»…)"),
    (re.compile(r"\[[A-ZÁÉÍÓÚÑ ]{3,}\]"), "un campo entre corchetes sin reemplazar (ej.: [MARCA])"),
]

_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
          "octubre", "noviembre", "diciembre")

TIPOS = ("cliente", "interno")
MAX_JSON = 2 * 1024 * 1024  # el interno trae la lista completa de antecedentes

FIRMA_DEFAULT = {
    "nombre": "Pamela Guzzardi",
    "cargo": "Agente de la Propiedad Industrial",
    "matricula": "Matrícula INPI N° 2906",
    "empresa": "Smarties Consultora",
}
INTRO_DEFAULT = "De acuerdo con la información disponible, se identifican las siguientes acciones recomendadas:"

# El informe al cliente nunca muestra el número de clase (decisión del 25/10/2026).
_RE_CLASE = re.compile(r"\bclases?\s*(?:n[°ºo.]*\s*)?\d+", re.IGNORECASE)


class InformeInvalido(ValueError):
    """El JSON pegado no sirve (no es JSON, le falta algo o menciona una clase)."""


def crear_tablas(cur):
    """Idempotente. Llamar después de cartera.crear_tablas (usa `clientes`)."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS informes_viabilidad (
            id            SERIAL PRIMARY KEY,
            cliente_id    INTEGER REFERENCES clientes(id) ON DELETE CASCADE,
            tipo          TEXT NOT NULL CHECK (tipo IN ('cliente', 'interno')),
            marca         TEXT,
            datos         JSONB NOT NULL,
            generado_por  TEXT,
            generado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_informes_cliente ON informes_viabilidad(cliente_id, generado_en DESC)")


# ── Lectura y validación del JSON pegado ──────────────────────────────────

def leer_json(texto) -> dict:
    """Acepta el texto pegado (con o sin ```json ... ```) o un dict ya parseado."""
    if isinstance(texto, dict):
        return texto
    t = (texto or "").strip()
    if not t:
        raise InformeInvalido("Pegá el JSON que generó la IA.")
    if len(t.encode("utf-8")) > MAX_JSON:
        raise InformeInvalido("El JSON es demasiado grande (máximo 2 MB).")
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t)
    try:
        d = json.loads(t)
    except json.JSONDecodeError as e:
        raise InformeInvalido(f"No es un JSON válido (línea {e.lineno}, columna {e.colno}: {e.msg}).")
    if not isinstance(d, dict):
        raise InformeInvalido("El JSON tiene que ser un objeto { … }.")
    return d


def _txt(v, largo=4000) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        v = str(v)
    return " ".join(v.replace("\r", "").split())[:largo]


def _comillas(s: str) -> str:
    """El modelo usa comillas tipográficas: «X» o "X" → “X”."""
    s = s.replace("«", "“").replace("»", "”")
    return re.sub(r'"([^"]*)"', r"“\1”", s)


def es_v2(d: dict) -> bool:
    """El JSON (pegado o ya guardado) es del formato de disponibilidad (versión 2)."""
    enc = d.get("encabezado") if isinstance(d.get("encabezado"), dict) else {}
    firma = d.get("firma") if isinstance(d.get("firma"), dict) else {}
    try:
        version = int(d.get("version") or d.get("formato") or 0)
    except (TypeError, ValueError):
        version = 0
    return (version >= 2 or "titulo" in d or "referencia" in enc or "lineas" in firma
            or isinstance(enc.get("web"), dict) or isinstance(enc.get("instagram"), dict))


def normalizar_cliente(d: dict, nombre_cliente: str = "", hoy: _dt.date = None) -> dict:
    """Deja el JSON del cliente con la forma que usa el PDF. Lanza InformeInvalido si no sirve."""
    if d.get("tipo") == "informe_viabilidad_interno":
        raise InformeInvalido("Ese es el JSON interno: va en «Adjuntar informe IA interno».")
    if es_v2(d):
        return _normalizar_v2(d, nombre_cliente, hoy)
    return _normalizar_v1(d, nombre_cliente)


def _normalizar_v1(d: dict, nombre_cliente: str = "") -> dict:
    """Formato anterior (modelo SABER LIBROS)."""
    enc = d.get("encabezado") or {}
    if not isinstance(enc, dict):
        raise InformeInvalido("«encabezado» tiene que ser un objeto.")
    secciones = d.get("secciones")
    if not isinstance(secciones, list) or not secciones:
        raise InformeInvalido("Falta la lista «secciones» con los textos del informe.")
    marca = _txt(enc.get("marca_pretendida"), 200)
    if not marca:
        raise InformeInvalido("Falta «encabezado.marca_pretendida».")

    out_enc = {
        "marca_pretendida": marca,
        "fecha_informe": _txt(enc.get("fecha_informe"), 40),
        "cliente": _txt(enc.get("cliente"), 200) or _txt(nombre_cliente, 200),
        "web": _txt(enc.get("web") or enc.get("sitio_web"), 300),
        "instagram": _txt(enc.get("instagram"), 300),
        "actividad": _comillas(_txt(enc.get("actividad"), 1500)),
        "antecedentes": _comillas(_txt(enc.get("antecedentes"), 1500)) or ".-",
        "logo": _comillas(_txt(enc.get("logo"), 500)) or "Pendiente de análisis.",
    }
    out_sec = []
    for i, s in enumerate(secciones[:10]):
        if isinstance(s, str):
            s = {"texto": s}
        if not isinstance(s, dict):
            raise InformeInvalido(f"La sección {i + 1} no tiene el formato esperado.")
        texto = s.get("texto")
        parrafos = texto if isinstance(texto, list) else re.split(r"\n\s*\n", texto or "")
        parrafos = [_comillas(_txt(p)) for p in parrafos if _txt(p)]
        if not parrafos:
            continue
        out_sec.append({"titulo": _txt(s.get("titulo"), 120), "parrafos": parrafos})
    if not out_sec:
        raise InformeInvalido("Las secciones no tienen texto.")
    firma = d.get("firma") if isinstance(d.get("firma"), dict) else {}
    out = {
        "tipo": "informe_viabilidad_cliente",
        "encabezado": out_enc,
        "introduccion": _comillas(_txt(d.get("introduccion"), 600)) or INTRO_DEFAULT,
        "secciones": out_sec,
        "firma": {k: _txt(firma.get(k), 120) or v for k, v in FIRMA_DEFAULT.items()},
    }
    menciones = sorted({m.group(0) for m in _RE_CLASE.finditer(_texto_visible(out))})
    if menciones:
        raise InformeInvalido("El informe al cliente no puede mostrar números de clase y menciona: "
                              + ", ".join(f"«{m}»" for m in menciones) + ". Corregilo en el JSON y volvé a pegarlo.")
    return out


def _texto_visible(d: dict) -> str:
    e = d["encabezado"]
    if d.get("formato") == 2:
        partes = [d["titulo"], e.get("cliente", "")]
    else:
        partes = [e["actividad"], e["antecedentes"], e["logo"], d["introduccion"]]
    for s in d["secciones"]:
        partes += [s["titulo"]] + s["parrafos"]
    return "\n".join(partes)


# ── Formato 2: informe preliminar de disponibilidad marcaria ──────────────

def fecha_en_letras(v=None, hoy: _dt.date = None) -> str:
    """«28 de octubre de 2026». Acepta ya en letras, DD/MM/AAAA, AAAA-MM-DD o vacío (= hoy)."""
    t = _txt(v, 60)
    if t and re.search(r"[a-zA-Z]", t):
        return t
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$", t) or None
    f = None
    if m:
        f = (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    else:
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", t)
        if m:
            f = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if t and not f:
        raise InformeInvalido(f"No entiendo la fecha «{t}» (usá «28 de octubre de 2026» o 28/10/2026).")
    try:
        fecha = _dt.date(*f) if f else (hoy or _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-3))).date())
    except ValueError:
        raise InformeInvalido(f"La fecha «{t}» no existe.")
    return f"{fecha.day} de {_MESES[fecha.month - 1]} de {fecha.year}"


def normalizar_referencia(v) -> str:
    """'' si no viene (la asigna el panel); «INFPR-1848» si viene bien; error si viene mal."""
    t = _txt(v, 40)
    if not t or t.upper().rstrip("-– ") == "INFPR":
        return ""
    m = _RE_REF.match(t)
    if not m:
        raise InformeInvalido(f"La referencia «{t}» no tiene el formato INFPR-número (ej.: INFPR-1848).")
    return f"{PREFIJO_REF}{int(m.group(1))}"


def numero_referencia(ref: str):
    m = _RE_REF.match(ref or "")
    return int(m.group(1)) if m else None


def _enlace(v, tipo: str):
    """web / instagram → {"url", "texto"}, o None si no hay. Acepta string u objeto."""
    if isinstance(v, dict):
        url, texto = _txt(v.get("url"), 300), _txt(v.get("texto"), 200)
    else:
        url, texto = _txt(v, 300), ""
    if not url and not texto:
        return None
    if tipo == "instagram":
        if url and "instagram.com" not in url.lower() and not re.match(r"https?://", url, re.I):
            url, texto = "", texto or url  # vino el usuario («@cuenta»), no una dirección
        usuario = ""
        fuente = url or texto
        m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", fuente)
        if m:
            usuario = m.group(1)
        elif re.fullmatch(r"@?[A-Za-z0-9_.]+", fuente):
            usuario = fuente.lstrip("@")
        if not url and usuario:
            url = f"https://www.instagram.com/{usuario}"
        if not texto:
            texto = f"@{usuario}" if usuario else url
    else:
        if not url:
            url = texto
        if not texto:
            texto = re.sub(r"^https?://", "", url).rstrip("/")
    if url and not re.match(r"https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    if not re.match(r"https?://[^\s/]+\.[^\s]+", url, re.I):
        raise InformeInvalido(f"El enlace de {'Instagram' if tipo == 'instagram' else 'la web'} «{url}» no parece una dirección válida.")
    return {"url": url, "texto": texto}


def _normalizar_v2(d: dict, nombre_cliente: str = "", hoy: _dt.date = None) -> dict:
    enc = d.get("encabezado") or {}
    if not isinstance(enc, dict):
        raise InformeInvalido("«encabezado» tiene que ser un objeto.")
    marca = _txt(enc.get("marca_pretendida"), 200)
    if not marca:
        raise InformeInvalido("Falta «encabezado.marca_pretendida».")
    secciones = d.get("secciones")
    if not isinstance(secciones, list) or not secciones:
        raise InformeInvalido("Falta la lista «secciones» con los textos del informe.")

    out_sec = []
    for i, s in enumerate(secciones[:10]):
        if isinstance(s, str):
            s = {"texto": s}
        if not isinstance(s, dict):
            raise InformeInvalido(f"La sección {i + 1} no tiene el formato esperado.")
        texto = s.get("texto")
        parrafos = texto if isinstance(texto, list) else re.split(r"\n\s*\n", texto or "")
        parrafos = [_comillas(_txt(p)) for p in parrafos if _txt(p)]
        if not parrafos:
            continue
        titulo = _txt(s.get("titulo"), 120)
        if not titulo:
            raise InformeInvalido(f"La sección {i + 1} no tiene título (en este formato todas llevan título).")
        out_sec.append({"titulo": titulo, "parrafos": parrafos})
    if not out_sec:
        raise InformeInvalido("Las secciones no tienen texto.")

    firma = d.get("firma") if isinstance(d.get("firma"), dict) else {}
    lineas = [_txt(x, 160) for x in (firma.get("lineas") or []) if _txt(x)] if isinstance(firma.get("lineas"), list) else []
    encab = {
        "marca_pretendida": marca,
        "cliente": _txt(enc.get("cliente"), 200) or _txt(nombre_cliente, 200),
        "fecha": fecha_en_letras(enc.get("fecha") or enc.get("fecha_informe"), hoy),
        "referencia": normalizar_referencia(enc.get("referencia")),
        "web": _enlace(enc.get("web") or enc.get("sitio_web"), "web"),
        "instagram": _enlace(enc.get("instagram"), "instagram"),
    }
    out = {
        "tipo": "informe_viabilidad_cliente",
        "formato": 2,
        "titulo": _txt(d.get("titulo"), 120) or TITULO_V2,
        "encabezado": encab,
        "secciones": out_sec,
        "firma": {"nombre": _txt(firma.get("nombre"), 120) or FIRMA_V2["nombre"],
                  "lineas": lineas[:8] or list(FIRMA_V2["lineas"])},
    }
    visible = _texto_visible(out)
    menciones = sorted({m.group(0) for m in _RE_CLASE.finditer(visible)})
    if menciones:
        raise InformeInvalido("El informe al cliente no puede mostrar números de clase y menciona: "
                              + ", ".join(f"«{m}»" for m in menciones) + ". Corregilo en el JSON y volvé a pegarlo.")
    malas = [nombre for rx, nombre in _PROHIBIDAS if rx.search(visible)]
    if malas:
        raise InformeInvalido("El informe al cliente usa expresiones que los criterios no permiten: "
                              + "; ".join(malas) + ". Corregilo en el JSON y volvé a pegarlo.")
    return out


def referencias_usadas(cur) -> list:
    """(referencia, cliente_id, marca, tipo) de todos los informes guardados que tienen una."""
    cur.execute(
        """
        SELECT cliente_id, marca, tipo,
               COALESCE(NULLIF(datos->'encabezado'->>'referencia', ''), NULLIF(datos->'caso'->>'referencia', '')) AS ref
          FROM informes_viabilidad
        """
    )
    out = []
    for f in cur.fetchall():
        f = dict(f) if not isinstance(f, dict) else f
        ref = normalizar_referencia_silenciosa(f.get("ref"))
        if ref:
            out.append((ref, f.get("cliente_id"), f.get("marca") or "", f.get("tipo")))
    return out


def normalizar_referencia_silenciosa(v) -> str:
    try:
        return normalizar_referencia(v)
    except InformeInvalido:
        return ""


def _misma_marca(a: str, b: str) -> bool:
    n = lambda s: re.sub(r"[^a-z0-9ñ]", "", (s or "").lower()
                         .translate(str.maketrans("áéíóúü", "aeiouu")))
    return n(a) == n(b)


def asignar_referencia(cur, cliente_id: int, d: dict) -> str:
    """Completa d["encabezado"]["referencia"] y la devuelve.

    - Si el JSON trae una, se controla que no la tenga otro informe (de otro
      cliente o de otra marca). La misma marca del mismo cliente la puede
      repetir: es una corrección del mismo informe.
    - Si no trae, se reusa la del último informe de esa marca del cliente
      (corrección) o se asigna la siguiente a la más alta usada.
    Llamar dentro de la misma transacción que guarda el informe.
    """
    cur.execute("SELECT pg_advisory_xact_lock(184700)")  # dos que generan a la vez no se pisan el número
    marca = d["encabezado"]["marca_pretendida"]
    usadas = referencias_usadas(cur)
    ref = d["encabezado"].get("referencia") or ""
    if ref:
        for r, cid, m, _tipo in usadas:
            if r == ref and not (cid == cliente_id and _misma_marca(m, marca)):
                raise InformeInvalido(f"La referencia {ref} ya la usa el informe de «{m}». "
                                      "Dejala vacía y el panel asigna la siguiente.")
    else:
        propias = [r for r, cid, m, tipo in usadas if cid == cliente_id and _misma_marca(m, marca) and tipo == "cliente"]
        if propias:
            ref = propias[0]  # vienen en orden de carga; cualquiera sirve: es la misma marca
        else:
            nums = [numero_referencia(r) for r, *_ in usadas] + [PRIMERA_REF - 1]
            ref = f"{PREFIJO_REF}{max(n for n in nums if n is not None) + 1}"
    d["encabezado"]["referencia"] = ref
    return ref


def referencia_de_marca(cur, cliente_id: int, marca: str) -> str:
    """La referencia del informe al cliente de esa marca (para completar el interno)."""
    for r, cid, m, tipo in referencias_usadas(cur):
        if cid == cliente_id and tipo == "cliente" and _misma_marca(m, marca):
            return r
    return ""


def normalizar_interno(d: dict) -> dict:
    if d.get("tipo") == "informe_viabilidad_cliente" or ("secciones" in d and "encabezado" in d):
        raise InformeInvalido("Ese es el JSON para el cliente: va en «Generar informe de disponibilidad marcaria».")
    if not marca_interno(d):
        raise InformeInvalido("No encuentro la marca en el JSON interno (se espera «caso.marca_pretendida»).")
    return d


def marca_interno(d: dict) -> str:
    caso = d.get("caso") if isinstance(d.get("caso"), dict) else {}
    return _txt(caso.get("marca_pretendida") or d.get("marca_pretendida")
                or (d.get("informe") or {}).get("datos_generales", {}).get("marca_pretendida"), 200)


def resumen_interno(d: dict) -> dict:
    """Lo que se muestra en la lista sin abrir el JSON completo."""
    b = d.get("busqueda_inpi") if isinstance(d.get("busqueda_inpi"), dict) else {}
    # Versión 2: las coincidencias exactas tienen su lista y la búsqueda amplia va dentro de «busqueda_amplia».
    ident = b.get("coincidencias_exactas") or b.get("identicas") or []
    rel = b.get("antecedentes_relevantes") or []
    amplia = b.get("busqueda_amplia") if isinstance(b.get("busqueda_amplia"), dict) else {}
    total_ant = sum(len(v) for fuente in (b, amplia) for k, v in fuente.items()
                    if k.startswith("antecedentes_") and k != "antecedentes_relevantes" and isinstance(v, list))
    caso = d.get("caso") if isinstance(d.get("caso"), dict) else {}
    consultas = b.get("consultas_procedimiento") if isinstance(b.get("consultas_procedimiento"), list) else []
    return {
        "referencia": normalizar_referencia_silenciosa(caso.get("referencia")),
        "consultas": len(consultas),
        "identicas": len(ident) if isinstance(ident, list) else 0,
        "relevantes": [{k: _txt(a.get(k), 400) for k in ("acta", "denominacion", "titular", "clase", "vigencia", "por_que_es_relevante")}
                       for a in rel[:15] if isinstance(a, dict)],
        "antecedentes_listados": total_ant,
        "observaciones": _txt(d.get("observaciones_para_pame"), 3000),
        "limitaciones": [_txt(x, 300) for x in (d.get("limitaciones") or [])[:10]],
        "fecha_consulta": _txt((d.get("caso") or {}).get("fecha_consulta_inpi") if isinstance(d.get("caso"), dict) else "", 40),
    }


# ── PDF (mismo formato que el modelo del estudio) ─────────────────────────

def _fuentes() -> tuple:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    try:
        pdfmetrics.getFont("Carlito")
    except KeyError:
        for e in ("Regular", "Bold", "Italic"):
            nombre = "Carlito" if e == "Regular" else f"Carlito-{e}"
            pdfmetrics.registerFont(TTFont(nombre, os.path.join(FUENTES, f"Carlito-{e}.ttf")))
        pdfmetrics.registerFontFamily("Carlito", normal="Carlito", bold="Carlito-Bold", italic="Carlito-Italic",
                                      boldItalic="Carlito-Bold")
    return "Carlito", "Carlito-Bold"


def generar_pdf(d: dict) -> bytes:
    if d.get("formato") == 2:
        return _generar_pdf_v2(d)
    return _generar_pdf_v1(d)


def _generar_pdf_v2(d: dict) -> bytes:
    """Igual al modelo aprobado ATELIER ASTRANITY (INFPR-1847): carta, texto a 90 pt de cada borde
    (márgenes de 84 + los 6 de relleno del marco de reportlab),
    Calibri (Carlito) 10,5 / título 20 / subtítulos 13 en negrita, paleta del manual,
    logo de trama arriba a la derecha y la firma unida, a unos 22 pt del último párrafo."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

    normal, negrita = _fuentes()
    buf = io.BytesIO()
    e = d["encabezado"]
    ancho, alto = letter
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=84, rightMargin=84, topMargin=91, bottomMargin=66,
                            title=f"{d['titulo']} – {e['marca_pretendida']}", author="Smarties Consultora",
                            subject=e.get("referencia") or "")
    gris, azul = HexColor(GRIS), HexColor(AZUL)
    base = ParagraphStyle("b2", fontName=normal, fontSize=10.5, leading=12.8, textColor=gris, alignment=TA_LEFT)
    cuerpo = ParagraphStyle("j2", parent=base, alignment=TA_JUSTIFY, spaceAfter=6)
    titulo = ParagraphStyle("t2", parent=base, fontName=normal, fontSize=20, leading=24, textColor=azul, spaceAfter=13)
    sub = ParagraphStyle("s2", parent=base, fontName=negrita, fontSize=13, leading=15.6, textColor=azul,
                         spaceBefore=10.5, spaceAfter=5, keepWithNext=1)
    firma_nombre = ParagraphStyle("fn2", parent=base, fontName=negrita, textColor=azul)

    def link(enl):
        return (f'<a href="{escape(enl["url"])}" color="{CELESTE}"><u>{escape(enl["texto"])}</u></a>')

    h = [Paragraph(escape(d["titulo"]), titulo)]
    for etiqueta, clave in (("Marca pretendida", "marca_pretendida"), ("Cliente", "cliente"), ("Fecha", "fecha"),
                            ("Referencia de consulta", "referencia")):
        if e.get(clave):  # los datos que no hay se omiten (no se inventan)
            h.append(Paragraph(f"{etiqueta}: {escape(e[clave])}", base))
    for etiqueta, clave in (("Web", "web"), ("Instagram", "instagram")):
        if e.get(clave):
            h.append(Paragraph(f"{etiqueta}: {link(e[clave])}", base))
    for s in d["secciones"]:
        h.append(Paragraph(escape(s["titulo"]), sub))
        h += [Paragraph(escape(p), cuerpo) for p in s["parrafos"]]
    f = d["firma"]
    h.append(KeepTogether([Spacer(1, 20), Paragraph(escape(f["nombre"]), firma_nombre)]
                          + [Paragraph(escape(x), base) for x in f["lineas"]]))

    logo = LOGO_TRAMA if os.path.exists(LOGO_TRAMA) else None

    def pagina(canvas, _doc):
        if logo:  # mismo lugar y tamaño que en el modelo (proporción del archivo: 2 × 1)
            w = 129.7
            canvas.drawImage(logo, ancho - 90 - w - 1.5, alto - 87.6, width=w, height=w / 2, mask="auto")

    doc.build(h, onFirstPage=pagina, onLaterPages=lambda c, dd: None)
    return buf.getvalue()


def _generar_pdf_v1(d: dict) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

    normal, negrita = _fuentes()
    buf = io.BytesIO()
    e = d["encabezado"]
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=3 * cm, rightMargin=3 * cm, topMargin=2.5 * cm,
                            bottomMargin=2.5 * cm, title=f"Informe preliminar {e['marca_pretendida']}",
                            author=d["firma"]["empresa"])
    base = ParagraphStyle("b", fontName=normal, fontSize=11, leading=13.4, alignment=TA_LEFT, spaceAfter=8)
    just = ParagraphStyle("j", parent=base, alignment=TA_JUSTIFY)
    titulo = ParagraphStyle("t", parent=base, fontName=negrita, alignment=TA_CENTER, spaceAfter=10)
    sub = ParagraphStyle("s", parent=base, fontName=negrita)
    numerada = ParagraphStyle("n", parent=base, leftIndent=18, spaceBefore=0)
    firma = ParagraphStyle("f", parent=base, fontName=negrita, spaceAfter=0)

    def campo(etiqueta, valor, link=False):
        v = escape(valor or "")
        if link and valor:
            url = valor if re.match(r"https?://", valor) else "https://" + valor
            v = f'<a href="{escape(url)}" color="#0563C1"><u>{v}</u></a>'
        return Paragraph(f"<b>{escape(etiqueta)}:</b> {v}", base)

    h = [Paragraph("INFORME PRELIMINAR", titulo),
         Paragraph("SOLICITUD DE REGISTRO DE MARCA EN ARGENTINA", titulo), Spacer(1, 18),
         campo("Marca pretendida", e["marca_pretendida"]),
         campo("Fecha", e["fecha_informe"]),
         campo("Cliente", e["cliente"]),
         campo("Sitio web", e["web"], link=True),
         campo("Instagram", e["instagram"], link=e["instagram"].startswith("http")),
         campo("Actividad", e["actividad"]),
         campo("Antecedentes", e["antecedentes"]),
         campo("Logo", e["logo"]),
         Spacer(1, 8),
         Paragraph(escape(d["introduccion"]), base)]
    for i, s in enumerate(d["secciones"]):
        if s["titulo"]:
            # Como el modelo: la primera va numerada («1. PROTECCIÓN DE LA MARCA»), las demás en negrita.
            h.append(Paragraph("1.&nbsp;&nbsp;" + escape(s["titulo"].upper()), numerada) if i == 0
                     else Paragraph(escape(s["titulo"]), sub))
        h += [Paragraph(escape(p), just) for p in s["parrafos"]]
    f = d["firma"]
    h.append(KeepTogether([Spacer(1, 4)] + [Paragraph(escape(f[k]), firma)
                                            for k in ("nombre", "cargo", "matricula", "empresa") if f.get(k)]))

    agua = MARCA_AGUA if os.path.exists(MARCA_AGUA) else None

    def pagina(canvas, _doc):
        canvas.saveState()
        if agua:
            lado = 441.9  # misma posición y tamaño que en el modelo
            canvas.drawImage(agua, 85.05, 175.05, width=lado, height=lado, mask="auto")
        canvas.setFont(normal, 11)
        canvas.setFillColor(HexColor("#000000"))
        canvas.drawString(3 * cm, 1.4 * cm, str(canvas.getPageNumber()))
        canvas.restoreState()

    doc.build(h, onFirstPage=pagina, onLaterPages=pagina)
    return buf.getvalue()


def nombre_archivo(d: dict, ext: str = "pdf") -> str:
    enc = d.get("encabezado") or {}
    base = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ]+", "_", enc.get("marca_pretendida", "")).strip("_")[:60]
    ref = enc.get("referencia") if d.get("formato") == 2 else ""
    return f"Informe_Preliminar_{base or 'marca'}{'_' + ref if ref else ''}.{ext}"
