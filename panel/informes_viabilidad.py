"""
Informe preliminar de viabilidad marcaria (el informe gratuito que se manda
antes de que el cliente contrate el registro).

El análisis lo hace la IA (skill «informe-viabilidad-marcaria») y entrega dos JSON:
  - el del CLIENTE: el texto resumido, con el estilo de Pame. Se pega en la
    ficha del cliente (pestaña Informes → «Generar informe de viabilidad
    marcaria») y el panel arma el PDF con el mismo formato que el modelo del
    estudio (Informe_Preliminar_SABER_LIBROS.pdf).
  - el INTERNO: todo lo que encontró la IA (búsquedas, antecedentes, clases,
    observaciones para Pame). Se adjunta con «Adjuntar informe IA interno» y
    queda guardado para el análisis completo y usos futuros.

Los dos se guardan en la tabla informes_viabilidad (uno por fila, tipo
'cliente' o 'interno'). Este módulo no abre conexiones: recibe cursores.
"""

import io
import json
import os
import re
from xml.sax.saxutils import escape

RECURSOS = os.path.join(os.path.dirname(__file__), "recursos")
FUENTES = os.path.join(RECURSOS, "fuentes")
MARCA_AGUA = os.path.join(RECURSOS, "marca-agua-smarties.png")

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


def normalizar_cliente(d: dict, nombre_cliente: str = "") -> dict:
    """Deja el JSON del cliente con la forma que usa el PDF. Lanza InformeInvalido si no sirve."""
    if d.get("tipo") == "informe_viabilidad_interno":
        raise InformeInvalido("Ese es el JSON interno: va en «Adjuntar informe IA interno».")
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
    partes = [e["actividad"], e["antecedentes"], e["logo"], d["introduccion"]]
    for s in d["secciones"]:
        partes += [s["titulo"]] + s["parrafos"]
    return "\n".join(partes)


def normalizar_interno(d: dict) -> dict:
    if d.get("tipo") == "informe_viabilidad_cliente" or ("secciones" in d and "encabezado" in d):
        raise InformeInvalido("Ese es el JSON para el cliente: va en «Generar informe de viabilidad marcaria».")
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
    ident = b.get("identicas") or []
    rel = b.get("antecedentes_relevantes") or []
    total_ant = sum(len(v) for k, v in b.items() if k.startswith("antecedentes_") and k != "antecedentes_relevantes"
                    and isinstance(v, list))
    return {
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
    base = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ]+", "_", (d.get("encabezado") or {}).get("marca_pretendida", "")).strip("_")[:60]
    return f"Informe_Preliminar_{base or 'marca'}.{ext}"
