"""
Escrito de desistimiento de una solicitud de registro de marca ante el INPI («DESISTE»), uno por marca (acta).

Se arma desde la solapa Poderes de la ficha del cliente, igual que la toma de intervención: el panel propone
los datos de cada marca de la cartera (acta, denominación, tipo, clase) y del titular (nombre, CUIT o DNI,
domicilio), se pueden corregir y se baja en PDF o Word; varias marcas se bajan en un ZIP.
Texto del modelo del estudio (pedido el 19/11/2026). La apoderada y la matrícula salen del apoderado de
Clientes → Ajustes (la matrícula, con la misma lectura que la toma de intervención).
"""

import io
import re
import zipfile

import poderes
import toma_intervencion as ti

APODERADA_NOMBRE = "Pamela Daiana Guzzardi"
DESTINATARIO = "Señor Director de la Dirección de Marcas del Instituto Nacional de la Propiedad Industrial:"
CIERRE = "SERÁ JUSTICIA.-"
GENEROS = ("domiciliada", "domiciliado", "domiciliado/a")


def _nombre_apoderada(apoderado: str) -> str:
    """Nombre de la apoderada: lo que va antes de la primera coma del texto de Ajustes."""
    n = (apoderado or "").split(",")[0].strip()
    return n or APODERADA_NOMBRE


def _domicilio(c: dict) -> str:
    domicilio = str(c.get("domicilio") or "").strip().rstrip(".")
    provincia = (c.get("provincia") or "").strip()
    localidad = (c.get("localidad") or "").strip()
    partes = [domicilio] if domicilio else []
    if poderes._es_caba(provincia):
        if "autónoma" not in domicilio.lower():
            partes.append(poderes.CABA)
    elif localidad and localidad.lower() not in domicilio.lower():
        partes.append(localidad)
    if partes and "república argentina" not in ", ".join(partes).lower():
        partes.append("República Argentina")
    return ", ".join(partes)


def _identificacion(c: dict, tipo: str) -> str:
    cuit, dni = poderes._dig(c.get("cuit")), poderes._dig(c.get("dni"))
    if len(cuit) == 11:
        return f"CUIT {poderes.fmt_cuit(cuit)}"
    if dni and tipo != "juridica":
        return f"DNI {poderes.fmt_dni(dni)}"
    return ""


def propuesta(marca: dict, cliente: dict, hoy) -> dict:
    """Valores sugeridos para una marca de la cartera; se pueden corregir en el panel."""
    c = cliente or {}
    tipo = c.get("tipo_persona") or ("juridica" if poderes._dig(c.get("cuit")).startswith("3") else "fisica")
    if tipo == "juridica":
        genero = "domiciliada"  # la sociedad
    else:
        genero = {"masculino": "domiciliado", "femenino": "domiciliada"}.get(
            poderes._sexo_amplio(c.get("sexo")), "domiciliado/a")
    return {"acta": str(marca.get("acta") or "").strip(), "marca": (marca.get("denominacion") or "").strip(),
            "clase": marca.get("clase") if marca.get("clase") is not None else "",
            "tipo": ti.tipo_texto(marca.get("tipo")),
            "titular": ((marca.get("titular") or c.get("nombre") or "")).strip(),
            "identificacion": _identificacion(c, tipo),
            "domicilio": _domicilio(c), "genero": genero, "fecha": hoy.isoformat()}


def _textos(d: dict, apoderado: str) -> dict:
    marca = (d.get("marca") or "").strip().upper()
    tipo = (d.get("tipo") or "").strip().upper()
    clase = str(d.get("clase") or "").strip()
    acta = str(d.get("acta") or "").strip()
    marca_tipo = f"“{marca}”" + (f" ({tipo})" if tipo else "")
    titulo = f"DESISTE DE LA SOLICITUD DE REGISTRO DE LA MARCA {marca_tipo}, ACTA N.º {acta}"
    if clase:
        titulo += f", CLASE {clase} INTERNACIONAL"
    titulo += "."

    titular = (d.get("titular") or "").strip().rstrip(",")
    persona = [titular]
    if (d.get("identificacion") or "").strip():
        persona.append(d["identificacion"].strip().rstrip(".,"))
    dom = (d.get("domicilio") or "").strip().rstrip(".,")
    genero = d.get("genero") if d.get("genero") in GENEROS else "domiciliada"
    if dom:
        persona.append(f"{genero} en {dom}")
    presentacion = (f"{_nombre_apoderada(apoderado)}, Agente de la Propiedad Industrial, Matrícula INPI N.º "
                    f"{ti.matricula(apoderado)}, en mi carácter de apoderada de {', '.join(persona)}, conforme al "
                    "poder que obra en el expediente, al Sr. Director respetuosamente digo:")
    en_clase = f", en clase {clase} Internacional" if clase else ""
    marca_cuerpo = f"“{(d.get('marca') or '').strip()}”" + (f" ({tipo})" if tipo else "")
    return {
        "titulo": titulo,
        "destinatario": DESTINATARIO,
        "presentacion": presentacion,
        "desiste": ("Que, cumpliendo con expresas instrucciones de mi mandante, vengo por el presente a desistir de "
                    f"la solicitud de registro de la marca {marca_cuerpo}, Acta N.º {acta}{en_clase}."),
        "petitorio": ("Solicito al Sr. Director que tenga por desistida la solicitud de registro de marca indicada "
                      "y provea de conformidad."),
        "cierre": CIERRE,
    }


def generar_pdf(d: dict, apoderado: str) -> bytes:
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    t = _textos(d, apoderado)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=3 * cm, rightMargin=2.5 * cm, topMargin=2.5 * cm,
                            bottomMargin=2.5 * cm, title="Desiste", author="")
    base = ParagraphStyle("b", fontName="Times-Roman", fontSize=12, leading=18, alignment=TA_JUSTIFY)
    titulo = ParagraphStyle("t", parent=base, fontName="Times-Bold")
    sangria = ParagraphStyle("s", parent=base, firstLineIndent=1.5 * cm, spaceAfter=0.3 * cm)
    centro = ParagraphStyle("c", parent=base, fontName="Times-Bold", alignment=TA_CENTER)
    h = [Paragraph("<u>" + escape(t["titulo"]) + "</u>", titulo), Spacer(1, 1.0 * cm),
         Paragraph(escape(t["destinatario"]), base), Spacer(1, 0.5 * cm),
         Paragraph(escape(t["presentacion"]), sangria),
         Paragraph(escape(t["desiste"]), sangria),
         Paragraph(escape(t["petitorio"]), sangria), Spacer(1, 1.0 * cm),
         Paragraph(escape(t["cierre"]), centro)]
    doc.build(h)
    return buf.getvalue()


def generar_docx(d: dict, apoderado: str) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    t = _textos(d, apoderado)
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin, sec.right_margin, sec.top_margin, sec.bottom_margin = Cm(3), Cm(2.5), Cm(2.5), Cm(2.5)
    st = doc.styles["Normal"]
    st.font.name = "Times New Roman"
    st.font.size = Pt(12)
    st.paragraph_format.space_after = Pt(0)
    st.paragraph_format.line_spacing = 1.5

    def par(texto, alin=WD_ALIGN_PARAGRAPH.JUSTIFY, antes=0, despues=0, sangria=False, negrita=False, subrayado=False):
        p = doc.add_paragraph()
        p.alignment = alin
        p.paragraph_format.space_before = Pt(antes)
        p.paragraph_format.space_after = Pt(despues)
        if sangria:
            p.paragraph_format.first_line_indent = Cm(1.5)
        r = p.add_run(texto)
        r.bold, r.underline = negrita, subrayado
        return p

    par(t["titulo"], negrita=True, subrayado=True)
    par(t["destinatario"], antes=28, despues=14)
    for k in ("presentacion", "desiste", "petitorio"):
        par(t[k], sangria=True, despues=8)
    par(t["cierre"], WD_ALIGN_PARAGRAPH.CENTER, antes=28, negrita=True)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def nombre_archivo(d: dict, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ]+", "_", f"{d.get('marca') or 'marca'}_acta_{d.get('acta') or ''}").strip("_")[:70]
    return f"Desiste_{base}.{ext}"


def generar_zip(items: list, apoderado: str, formato: str) -> bytes:
    gen = generar_docx if formato == "docx" else generar_pdf
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        usados = set()
        for d in items:
            n = nombre_archivo(d, formato)
            while n in usados:
                n = n.replace(f".{formato}", f"_2.{formato}")
            usados.add(n)
            z.writestr(n, gen(d, apoderado))
    return buf.getvalue()
