"""
Poder para el registro de marca, autocompletado con los datos del cliente.

Después de que el cliente completa el formulario (Clientes → Formularios),
desde su ficha se genera el poder ya lleno —en PDF o en Word— para mandárselo
y que solo tenga que firmarlo. Es el mismo texto del modelo del estudio
(persona física o jurídica); lo único que cambia es el párrafo del otorgante,
y el lugar y fecha. La aclaración (y el cargo, en persona jurídica) quedan en
blanco: los completa a mano quien firma.

Los datos del apoderado (nombre, matrícula, domicilio) se editan en
Clientes → Ajustes y se guardan en vigilancia_config (clave poder_apoderado).
"""

import datetime as _dt
import io
import os
import re

LOGO = os.path.join(os.path.dirname(__file__), "recursos", "logo-poder.jpg")

APODERADO_DEFAULT = ("Pamela Daiana Guzzardi, agente de propiedad Industrial, Matrícula N° 2906, "
                     "domiciliada en la calle Oncativo 65, piso 1, departamento A, Ramos Mejía, República Argentina")

CUERPO = (
    "Poder amplio y suficiente para recabar de las autoridades nacionales que corresponda, sean éstas "
    "administrativas y / o judiciales, la obtención de Patentes de Invención y/o Marcas de Productos y "
    "Servicios y/o Modelos de Utilidad y/o Modelos Industriales, realizar todos los trámites conducentes "
    "para proteger los derechos de la mandante, a cuyo efecto, los faculta para efectuar ante dichas "
    "autoridades nacionales, administrativas y/o judiciales, todos los trámites necesarios a los objetos "
    "indicados; presentar solicitudes, alegatos, oposiciones, protestas, apelaciones y demandas; contestar "
    "vistas, exámenes, u observaciones a los trámites iniciados o a iniciarse; ampliar fundamentos de "
    "protestas y/o oposiciones y/o llamados de atención; desistir, arreglar y transigir; aceptar cesiones y "
    "transferencias; solicitar testimonios; recibir documentos y otros valores; justificar explotaciones; "
    "cobrar y percibir; firmar instrumentos públicos, privados y escrituras públicas; para renunciar o no a "
    "las gestiones judiciales; y hacer cuanto fuere necesario ante las autoridades nacionales, judiciales "
    "y/o administrativas, sea como demandante o como demandado, para proteger los intereses del "
    "mandante, con facultad de substituir este instrumento y revocar tal substitución."
)
CONSTANCIA = (
    "Se deja constancia que las facultades enumeradas en este mandato, son meramente ejemplificadas y no "
    "limitativas de otras facultades implícitas, por cuyo motivo, la mandataria queda autorizada para "
    "ejercer sin restricción alguna todos los derechos que acuerde la ley que se relacionen y/o emerjan y/o "
    "sean consecuencia del objeto del presente poder y ya sea ello en razón de la legislación vigente y/o de "
    "la legislación que en el futuro se promulgue."
)

LINEA_FIRMA = "_" * 38

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]
CABA = "Ciudad Autónoma de Buenos Aires"


def _dig(v):
    return re.sub(r"\D", "", str(v) if v is not None else "")


def fmt_dni(v):
    d = _dig(v)
    if not d:
        return ""
    return f"{int(d):,}".replace(",", ".")


def fmt_cuit(v):
    d = _dig(v)
    return f"{d[:2]}-{d[2:10]}-{d[10:]}" if len(d) == 11 else (v or "")


def fecha_larga(f) -> str:
    if isinstance(f, str):
        f = _dt.date.fromisoformat(f)
    return f"{f.day} de {MESES[f.month - 1]} de {f.year}"


def _es_caba(txt):
    t = (txt or "").lower()
    return "autónoma" in t or "autonoma" in t or t.strip() in ("caba", "capital federal")


def propuesta(cliente: dict, hoy=None) -> dict:
    """Valores sugeridos para el poder, a partir de la ficha del cliente.
    Todos se pueden corregir en el panel antes de generar."""
    hoy = hoy or _dt.date.today()
    c = cliente or {}
    tipo = c.get("tipo_persona") or ("juridica" if _dig(c.get("cuit")).startswith("3") else "fisica")
    nombre = (c.get("nombre") or "").strip()
    domicilio = str(c.get("domicilio") or "").strip().rstrip(".")
    provincia = (c.get("provincia") or "").strip()
    localidad = (c.get("localidad") or "").strip()
    if tipo == "juridica":
        partes = [f"{nombre}, CUIT {fmt_cuit(c.get('cuit'))}"]
        if domicilio:
            dom = domicilio
            if provincia and provincia.lower() not in domicilio.lower():
                dom += ", " + (CABA if _es_caba(provincia) else f"Provincia de {provincia}")
            partes.append(f"domiciliada en {dom}")
        otorgante = ", ".join(partes) + ", República Argentina."
    else:
        lugar_dom = [domicilio] if domicilio else []
        if _es_caba(provincia):
            lugar_dom.append(CABA)
        else:
            if localidad and localidad.lower() not in domicilio.lower():
                lugar_dom.append(localidad)
            if provincia:
                lugar_dom.append(f"Provincia de {provincia}")
        partes = [nombre]
        if c.get("dni"):
            partes.append(f"DNI {fmt_dni(c.get('dni'))}")
        if lugar_dom:
            partes.append("domiciliado en " + ", ".join(lugar_dom))
        otorgante = ", ".join(partes) + ", República Argentina."
    if _es_caba(provincia) or (tipo == "juridica" and _es_caba(domicilio)):
        lugar = CABA
    elif provincia:
        lugar = f"Provincia de {provincia}"
    else:
        lugar = CABA
    return {"tipo_persona": tipo, "otorgante": otorgante, "lugar": lugar, "fecha": hoy.isoformat()}


def _parrafo_inicial(d, apoderado):
    otorgante = d["otorgante"].strip().rstrip(".")
    return f"El abajo firmante {otorgante}. Por el presente otorgo a {apoderado.strip().rstrip('.')}. {CUERPO}"


def _lugar_fecha(d):
    return f"{d['lugar'].strip().rstrip(',')}, República Argentina, el {fecha_larga(d['fecha'])}." \
        if "república argentina" not in d["lugar"].lower() else f"{d['lugar'].strip()}, el {fecha_larga(d['fecha'])}."


# Pasos para que el poder entre siempre en UNA hoja A4 (pedido de Pame, 02/11/2026): primero se arma con el
# diseño normal y, si no entra, se van achicando los espacios en blanco y después la letra, hasta que entre.
# (tamaño de letra, interlineado, escala de los espacios en blanco, ancho del logo en cm)
_AJUSTES_UNA_HOJA = [
    (12, 14.6, 1.0, 3.6),
    (12, 14.6, 0.6, 3.6),
    (12, 14.0, 0.4, 3.0),
    (11.5, 13.4, 0.35, 3.0),
    (11, 12.8, 0.3, 2.6),
    (10.5, 12.2, 0.25, 2.4),
    (10, 11.6, 0.2, 2.2),
    (9.5, 11.0, 0.2, 2.0),
    (9, 10.4, 0.15, 1.8),
]


def _armar_pdf(d: dict, apoderado: str, letra, interlineado, esc, logo_cm):
    """Arma el PDF con un ajuste dado; devuelve (bytes, cantidad de hojas)."""
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2.6 * cm, rightMargin=2.2 * cm, topMargin=1.4 * cm,
                            bottomMargin=1.6 * cm, title="Poder", author="")
    cuerpo = ParagraphStyle("c", fontName="Times-Roman", fontSize=letra, leading=interlineado, alignment=TA_JUSTIFY)
    titulo = ParagraphStyle("t", parent=cuerpo, alignment=TA_CENTER)
    normal = ParagraphStyle("n", parent=cuerpo, alignment=0)

    def esp(alto_cm):
        return Spacer(1, alto_cm * esc * cm)

    h = []
    if os.path.exists(LOGO):
        img = Image(LOGO, width=logo_cm * cm, height=logo_cm * cm * 176 / 280)
        img.hAlign = "LEFT"
        h += [img, esp(0.5)]
    else:
        h.append(esp(2.5))
    h += [Paragraph("<u>Poder</u>", titulo), esp(0.5),
          Paragraph(escape(_parrafo_inicial(d, apoderado)), cuerpo),
          Paragraph(escape(CONSTANCIA), cuerpo),
          esp(1.9),
          Paragraph(escape(_lugar_fecha(d)), normal),
          # el lugar para la firma nunca baja de ~1,2 cm, aunque se achique todo lo demás
          Spacer(1, max(1.8 * esc, 1.2) * cm),
          Paragraph("FIRMA", normal), Spacer(1, max(1.0 * esc, 0.6) * cm),
          Paragraph("ACLARACIÓN: " + LINEA_FIRMA, normal)]
    if d.get("tipo_persona") == "juridica":
        h += [Spacer(1, max(0.9 * esc, 0.6) * cm), Paragraph("CARGO: " + LINEA_FIRMA, normal)]
    doc.build(h)
    return buf.getvalue(), doc.page


def generar_pdf(d: dict, apoderado: str) -> bytes:
    """PDF del poder, siempre en una sola hoja: si con el diseño normal no entra (domicilio o apoderado muy
    largos), achica primero los espacios y después la letra. Si ni con el ajuste más chico entra, sale igual."""
    pdf = b""
    for ajuste in _AJUSTES_UNA_HOJA:
        pdf, hojas = _armar_pdf(d, apoderado, *ajuste)
        if hojas <= 1:
            break
    return pdf


def generar_docx(d: dict, apoderado: str) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin, sec.right_margin, sec.top_margin, sec.bottom_margin = Cm(2.6), Cm(2.2), Cm(1.4), Cm(1.6)
    st = doc.styles["Normal"]
    st.font.name = "Times New Roman"
    st.font.size = Pt(12)
    st.paragraph_format.space_after = Pt(0)
    st.paragraph_format.line_spacing = 1.15

    def par(texto="", alin=None, antes=0, subrayado=False):
        p = doc.add_paragraph()
        if alin is not None:
            p.alignment = alin
        p.paragraph_format.space_before = Pt(antes)
        if texto:
            r = p.add_run(texto)
            r.underline = subrayado
        return p

    if os.path.exists(LOGO):
        doc.add_paragraph().add_run().add_picture(LOGO, width=Cm(3.6))
    par("Poder", WD_ALIGN_PARAGRAPH.CENTER, antes=12, subrayado=True)
    par(_parrafo_inicial(d, apoderado), WD_ALIGN_PARAGRAPH.JUSTIFY, antes=14)
    par(CONSTANCIA, WD_ALIGN_PARAGRAPH.JUSTIFY)
    par(_lugar_fecha(d), antes=54)
    par("FIRMA", antes=50)
    par("ACLARACIÓN: " + LINEA_FIRMA, antes=28)
    if d.get("tipo_persona") == "juridica":
        par("CARGO: " + LINEA_FIRMA, antes=24)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def nombre_archivo(d: dict, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ]+", "_", d.get("otorgante", "").split(",")[0]).strip("_")[:60] or "cliente"
    return f"Poder_{base}.{ext}"


# ── Poder simple marcas (modelo pedido el 09/11/2026) ─────────────────────────────────────────────
# Poder acotado a marcas (INPI y negociaciones/acuerdos vinculados), solo para persona humana (con DNI).
# Texto fijo del estudio: el apoderado va tal cual el modelo, no sale de Ajustes.

MODELO_SIMPLE = "simple_marcas"
NOMBRE_MODELO = {"amplio": "Poder para firmar", MODELO_SIMPLE: "Poder simple marcas"}

APODERADO_SIMPLE = ("Pamela Daiana Guzzardi, Agente de la Propiedad Industrial, Matrícula INPI N.º 2906, "
                    "domiciliada en la calle Oncativo 65, piso 1, departamento A, Ramos Mejía, República Argentina")

# Variantes de género del modelo: «El/La», «representarlo/a», «del/de la mandante». Por defecto, como el modelo.
GENEROS = {"neutro": ("El/La", "representarlo/a", "del/de la"),
           "masculino": ("El", "representarlo", "del"),
           "femenino": ("La", "representarla", "de la")}

SIMPLE_FACULTADES = (
    "A tal efecto, la faculta para solicitar registros y renovaciones de marcas; presentar escritos, "
    "documentación, declaraciones juradas de uso, alegatos, oposiciones, llamados de atención y recursos "
    "administrativos; contestar vistas, exámenes, observaciones y traslados; ampliar fundamentos; ofrecer y "
    "acompañar prueba; tomar intervención y vista de expedientes; solicitar rectificaciones de datos, cambios "
    "de domicilio, certificados, testimonios y títulos; abonar tasas y aranceles; recibir documentación y "
    "notificaciones; constituir domicilio electrónico y realizar los demás actos de trámite necesarios para la "
    "obtención, conservación y defensa administrativa de los derechos marcarios {del} mandante."
)
SIMPLE_NEGOCIACION = (
    "Asimismo, la faculta para intervenir en negociaciones relativas a oposiciones y acuerdos de coexistencia "
    "marcaria; celebrar y suscribir acuerdos; transigir; desistir de solicitudes u oposiciones; renunciar a "
    "registros; ceder o transferir marcas; y aceptar cesiones o transferencias en nombre {del} mandante."
)
SIMPLE_LIMITE = ("Las facultades enumeradas se ejercerán únicamente dentro del objeto y de los límites "
                 "establecidos en el presente instrumento.")


def es_simple(d: dict) -> bool:
    return (d or {}).get("modelo") == MODELO_SIMPLE


def propuesta_simple(cliente: dict, hoy=None) -> dict:
    """Valores sugeridos para el poder simple marcas: nombre, DNI y domicilio del cliente, y como lugar
    la localidad y provincia. Todo se puede corregir antes de generar."""
    hoy = hoy or _dt.date.today()
    c = cliente or {}
    nombre = (c.get("nombre") or "").strip()
    domicilio = str(c.get("domicilio") or "").strip().rstrip(".")
    provincia = (c.get("provincia") or "").strip()
    localidad = (c.get("localidad") or "").strip()
    dom = [domicilio] if domicilio else []
    if _es_caba(provincia):
        dom.append(CABA)
        lugar = CABA
    else:
        if localidad and localidad.lower() not in domicilio.lower():
            dom.append(localidad)
        if provincia:
            dom.append(f"Provincia de {provincia}")
        lugar = ", ".join(x for x in (localidad, f"Provincia de {provincia}" if provincia else "") if x) or CABA
    partes = [nombre]
    if c.get("dni"):
        partes.append(f"DNI {fmt_dni(c.get('dni'))}")
    if dom:
        partes.append("con domicilio en " + ", ".join(dom))
    tipo = c.get("tipo_persona") or ("juridica" if _dig(c.get("cuit")).startswith("3") else "fisica")
    return {"modelo": MODELO_SIMPLE, "tipo_persona": "fisica", "genero": "neutro",
            "otorgante": ", ".join(partes), "lugar": lugar, "fecha": hoy.isoformat(),
            "cliente_juridica": tipo == "juridica"}


def _genero(d):
    return GENEROS.get(d.get("genero") or "neutro", GENEROS["neutro"])


def _simple_parrafo_inicial(d):
    art, repr_, _ = _genero(d)
    otorgante = re.sub(r",?\s*República Argentina\.?$", "", d["otorgante"].strip().rstrip(".,"), flags=re.I)
    return (f"{art} abajo firmante {otorgante}, República Argentina, por el presente otorga a {APODERADO_SIMPLE}, "
            f"poder para {repr_} exclusivamente en materia de marcas de productos y servicios de las que sea "
            "titular o cuyo registro solicite, en trámites ante el Instituto Nacional de la Propiedad Industrial "
            "(INPI) de la República Argentina y en las negociaciones y acuerdos vinculados con dicha materia.")


def _simple_parrafos(d):
    _, _, del_ = _genero(d)
    return [_simple_parrafo_inicial(d), SIMPLE_FACULTADES.format(**{"del": del_}),
            SIMPLE_NEGOCIACION.format(**{"del": del_}), SIMPLE_LIMITE]


def _simple_lugar_fecha(d):
    f = d["fecha"]
    if isinstance(f, str):
        f = _dt.date.fromisoformat(f)
    lugar = re.sub(r",?\s*República Argentina\.?$", "", d["lugar"].strip().rstrip(".,"), flags=re.I)
    dias = "al 1 día" if f.day == 1 else f"a los {f.day} días"
    return f"{lugar}, República Argentina, {dias} del mes de {MESES[f.month - 1]} de {f.year}."


_LINEAS_FIRMA_SIMPLE = [("FIRMA: ", 36), ("ACLARACIÓN: ", 31), ("DNI: ", 38)]


def _armar_pdf_simple(d: dict, letra, interlineado, esc, logo_cm):
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2.6 * cm, rightMargin=2.2 * cm, topMargin=1.4 * cm,
                            bottomMargin=1.6 * cm, title="Poder", author="")
    cuerpo = ParagraphStyle("c", fontName="Times-Roman", fontSize=letra, leading=interlineado, alignment=TA_JUSTIFY,
                            spaceAfter=0.35 * esc * cm)
    titulo = ParagraphStyle("t", parent=cuerpo, alignment=TA_CENTER)
    normal = ParagraphStyle("n", parent=cuerpo, alignment=0, spaceAfter=0)

    def esp(alto_cm):
        return Spacer(1, alto_cm * esc * cm)

    h = []
    if os.path.exists(LOGO):
        img = Image(LOGO, width=logo_cm * cm, height=logo_cm * cm * 176 / 280)
        img.hAlign = "LEFT"
        h += [img, esp(0.5)]
    else:
        h.append(esp(2.5))
    h += [Paragraph("<u>Poder</u>", titulo), esp(0.3)]
    h += [Paragraph(escape(p), cuerpo) for p in _simple_parrafos(d)]
    h += [esp(1.2), Paragraph(escape(_simple_lugar_fecha(d)), normal)]
    for i, (rot, largo) in enumerate(_LINEAS_FIRMA_SIMPLE):
        # el lugar para la firma nunca baja de ~1,2 cm, aunque se achique todo lo demás
        h += [Spacer(1, max((2.4 if i == 0 else 1.0) * esc, 1.2 if i == 0 else 0.6) * cm),
              Paragraph(rot + "_" * largo, normal)]
    doc.build(h)
    return buf.getvalue(), doc.page


def generar_pdf_simple(d: dict) -> bytes:
    """PDF del poder simple marcas, siempre en una sola hoja (mismo ajuste escalonado que el poder amplio)."""
    pdf = b""
    for ajuste in _AJUSTES_UNA_HOJA:
        pdf, hojas = _armar_pdf_simple(d, *ajuste)
        if hojas <= 1:
            break
    return pdf


def generar_docx_simple(d: dict) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin, sec.right_margin, sec.top_margin, sec.bottom_margin = Cm(2.6), Cm(2.2), Cm(1.4), Cm(1.6)
    st = doc.styles["Normal"]
    st.font.name = "Times New Roman"
    st.font.size = Pt(11.5)
    st.paragraph_format.space_after = Pt(0)
    st.paragraph_format.line_spacing = 1.1

    def par(texto="", alin=None, antes=0, despues=0, subrayado=False):
        p = doc.add_paragraph()
        if alin is not None:
            p.alignment = alin
        p.paragraph_format.space_before = Pt(antes)
        p.paragraph_format.space_after = Pt(despues)
        if texto:
            r = p.add_run(texto)
            r.underline = subrayado
        return p

    if os.path.exists(LOGO):
        doc.add_paragraph().add_run().add_picture(LOGO, width=Cm(3.0))
    par("Poder", WD_ALIGN_PARAGRAPH.CENTER, antes=8, despues=8, subrayado=True)
    for p in _simple_parrafos(d):
        par(p, WD_ALIGN_PARAGRAPH.JUSTIFY, despues=8)
    par(_simple_lugar_fecha(d), antes=22)
    for i, (rot, largo) in enumerate(_LINEAS_FIRMA_SIMPLE):
        par(rot + "_" * largo, antes=56 if i == 0 else 24)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def nombre_archivo_simple(d: dict, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ]+", "_", d.get("otorgante", "").split(",")[0]).strip("_")[:60] or "cliente"
    return f"Poder_simple_marcas_{base}.{ext}"
