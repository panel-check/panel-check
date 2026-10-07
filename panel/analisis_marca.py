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

Este módulo no abre conexiones: las funciones de base reciben un cursor.
"""

import datetime as _dt
import io
import json
import re
from xml.sax.saxutils import escape

import presupuestos as pres

LARGO_MAX_TEXTO = 20000
RE_ACTA = re.compile(r"^\d{4,9}$")

# Columnas de `marcas` que usa el análisis.
COLUMNAS_MARCA = (
    "acta, tipo, denominacion, denominacion_inpi, titular, cuit, tuvo_oposicion, "
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
    cur.execute("SELECT texto, oposiciones, actualizado_por, actualizado_en FROM analisis_marca WHERE acta = %s", (acta,))
    f = cur.fetchone()
    if not f:
        return {"texto": "", "oposiciones": None, "actualizado_por": None, "actualizado_en": None}
    return {"texto": _valor(f, "texto", 0) or "", "oposiciones": _valor(f, "oposiciones", 1),
            "actualizado_por": _valor(f, "actualizado_por", 2), "actualizado_en": _valor(f, "actualizado_en", 3)}


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


def datos_para_pdf(m: dict, texto: str, op: dict) -> dict:
    return {
        "acta": str(m["acta"]),
        "marca": nombre_marca(m),
        "titular": titular_con_cuit(m),
        "oposiciones": op,
        "texto": texto or "",
        "fecha": pres.hoy_ar().isoformat(),
    }


def nombre_archivo(d: dict) -> str:
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


def generar_pdf(d: dict) -> bytes:
    """El análisis de marca en PDF (A4, con el membrete de Smarties) a partir de `d`
    (ver datos_para_pdf). Si el texto es largo sigue en más hojas, todas con membrete."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer

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
    cuerpo = estilo("cuerpo", alin=TA_JUSTIFY)
    vineta = estilo("vineta", alin=TA_JUSTIFY, leftIndent=18.7, bulletIndent=6, bulletFontName=F["r"],
                    bulletFontSize=11 * esc)

    op = d["oposiciones"]
    h = [Paragraph("Análisis de marca", titulo), Spacer(1, 22)]

    h.append(Paragraph(f"<b>Marca:</b> {_xml(d['marca'])}", dato))
    h.append(Paragraph(f"<b>Acta:</b> {_xml(d['acta'])}", dato))
    h.append(Paragraph(f"<b>Titular:</b> {_xml(d['titular'])}", dato))
    h.append(Paragraph(f"<b>Oposiciones:</b> {_xml(resumen_oposiciones(op))}", dato))
    for n, i in enumerate(op["items"], 1):
        h.append(Paragraph(_xml(linea_oposicion(i)), dato_op, bulletText=f"{n}."))
    if op["origen"] == "sistema":
        h.append(Spacer(1, 3))
        h.append(Paragraph("Dato del sistema: no se verificó contra el expediente de INPI.", chico))

    h.append(Spacer(1, 16))
    h.append(Paragraph("Análisis", seccion))
    h.append(Spacer(1, 4))
    bloques = _bloques_de_texto(d.get("texto") or "")
    if not bloques:
        h.append(Paragraph("(Sin análisis escrito.)", chico))
    previo = None
    for tipo, t in bloques:
        if tipo == "v":
            h.append(Paragraph(_xml(t), vineta, bulletText="•"))
            h.append(Spacer(1, 3))
        else:
            if previo == "v":
                h.append(Spacer(1, 5))  # un poco de aire al volver al texto después de una lista
            h.append(Paragraph(_xml(t), cuerpo))
            h.append(Spacer(1, 8))
        previo = tipo

    cuando = pres.mes_anio(_dt.date.fromisoformat(d["fecha"]))

    def decorar(c: _canvas.Canvas, doc):
        pres.dibujar_membrete_y_pie(c, cuando)

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, title="Análisis de marca", author=pres.MARCA_NOMBRE)
    # Mismo arranque que el presupuesto: el título apoya su base 167.7 pt bajo el borde de arriba;
    # el marco termina antes del pie.
    arriba = 167.7 - pres.AJUSTE - titulo.fontSize
    marco = Frame(75.4, 70.0, 520.1 - 75.4, alto - arriba - 70.0, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0, id="cuerpo")
    doc.addPageTemplates([PageTemplate(id="pagina", frames=[marco], onPage=decorar)])
    doc.build(h)
    return buf.getvalue()
