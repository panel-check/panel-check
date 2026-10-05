"""
Presupuestos en PDF que se mandan por mail desde el panel.

Hoy hay un solo modelo: «Registro de marca» (el mismo que se armaba a mano en
Canva). Lo que cambia mes a mes (honorarios, valor de las tasas, costo del
título, vigencia, datos de transferencia) se carga una vez en Mails → plantilla
«Presupuesto de registro de marca» y queda guardado en vigilancia_config
(clave presupuesto_registro_marca, como JSON). Al mandarlo desde la ficha de un
lead se pueden ajustar solo para ese envío los montos, el alcance y la vigencia.

Qué sale en cada envío (se guarda completo en mails_envios.detalle): es el
"snapshot" con el que se arma el PDF, así el mismo PDF se puede volver a bajar
desde el historial aunque después cambien los valores.

Tipografía: el modelo usa Open Sans. Si en panel/recursos/fuentes/ están
OpenSans-Regular.ttf, OpenSans-Bold.ttf y OpenSans-Italic.ttf se usan; si no,
se usa Carlito (incluida en el repo), que es muy parecida.

Este módulo no toca la base: las funciones de lectura/escritura reciben un
cursor. Lo usan mails_api.py (panel).
"""

import datetime as _dt
import io
import json
import os
import re
from xml.sax.saxutils import escape

RECURSOS = os.path.join(os.path.dirname(__file__), "recursos")
LOGO = os.path.join(RECURSOS, "logo-smarties.png")
FUENTES = os.path.join(RECURSOS, "fuentes")

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]

# Encabezado y pie del PDF (los mismos datos que la firma de los mails).
MARCA_NOMBRE = "Smarties Consultora"
MARCA_WEB = "www.smartiesconsultora.com.ar"
MARCA_EMAIL = "info@smartiesconsultora.com.ar"
PIE_PARTES = ("Pamela Guzzardi | Agente de la Propiedad Industrial – Matrícula INPI N.º 2906 | "
              "Smarties Consultora | Registro de Marcas Nacional e Internacional | ")
PIE_TELEFONO = "+54 9 11 5589-0784"

TIPOS = {
    "registro_marca": {
        "nombre": "Registro de marca",
        "archivo": "Presupuesto Registro de Marca",
        "config_clave": "presupuesto_registro_marca",
    },
}

# Valores por defecto (los del modelo). Los datos de transferencia NO tienen
# valor por defecto a propósito: el repo es público; se cargan desde el panel.
DEFAULTS = {
    "honorarios": 180000,
    "tasas": 40569,
    "titulo_costo": 105000,
    "vigencia_dias": 10,
    "alcance": "1 marca en 1 clase",
    "valores_mes": "",   # vacío = el mes en que se genera el PDF
    "pago": "50% para iniciar y saldo una vez definida la estrategia y preparada la solicitud.",
    "alias": "",
    "cvu": "",
    "titular_cuenta": "",
}

# Etiquetas para mostrar los campos faltantes y para el formulario.
ETIQUETAS = {
    "honorarios": "Honorarios", "tasas": "Valor de las tasas", "titulo_costo": "Costo del título",
    "vigencia_dias": "Vigencia (días)", "alcance": "Alcance", "valores_mes": "Mes de los valores",
    "pago": "Forma de pago", "alias": "Alias", "cvu": "CVU", "titular_cuenta": "Nombre del titular de la cuenta",
}
OBLIGATORIOS_PARA_ENVIAR = ("alias", "cvu", "titular_cuenta")
# Lo que se puede ajustar en cada envío desde la ficha del lead.
AJUSTABLES_POR_ENVIO = ("honorarios", "tasas", "alcance", "vigencia_dias")


# ── Formatos ──────────────────────────────────────────────────────────────
def hoy_ar() -> _dt.date:
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-3))).date()


def mes_anio(f) -> str:
    if isinstance(f, str):
        f = _dt.date.fromisoformat(f)
    return f"{MESES[f.month - 1]} {f.year}"


def fmt_pesos(n) -> str:
    return "$" + f"{int(n):,}".replace(",", ".")


def fmt_dias(n) -> str:
    n = int(n)
    return f"{n} día" if n == 1 else f"{n} días"


def _a_entero(v, campo: str, minimo: int, maximo: int) -> int:
    """'180.000', '$180000', 180000 -> 180000. Sin centavos."""
    if isinstance(v, bool):
        raise ValueError(f"{ETIQUETAS[campo]}: escribí un número")
    if isinstance(v, (int, float)):
        if int(v) != v:
            raise ValueError(f"{ETIQUETAS[campo]}: escribilo en pesos enteros, sin centavos")
        n = int(v)
    else:
        t = re.sub(r"[\s$]", "", str(v or ""))
        if not re.fullmatch(r"\d{1,3}(\.\d{3})+|\d+", t):
            raise ValueError(f"{ETIQUETAS[campo]}: escribí un número entero (por ejemplo 180000 o 180.000)")
        n = int(t.replace(".", ""))
    if not minimo <= n <= maximo:
        raise ValueError(f"{ETIQUETAS[campo]}: tiene que estar entre {minimo} y {maximo}")
    return n


def _texto(v, campo: str, largo: int, obligatorio: bool = False) -> str:
    t = " ".join(str(v or "").split())
    if obligatorio and not t:
        raise ValueError(f"{ETIQUETAS[campo]}: no puede quedar vacío")
    if len(t) > largo:
        raise ValueError(f"{ETIQUETAS[campo]}: es demasiado largo (máximo {largo} caracteres)")
    return t


def validar(valores: dict, completo: bool = False) -> dict:
    """Limpia y valida los campos que vengan en `valores` (los que falten no se
    tocan). Con completo=True exige los que no pueden estar vacíos. Levanta
    ValueError con un mensaje claro."""
    out = {}
    v = valores or {}
    if v.get("honorarios") not in (None, ""):
        out["honorarios"] = _a_entero(v["honorarios"], "honorarios", 1, 99_999_999)
    if v.get("tasas") not in (None, ""):
        out["tasas"] = _a_entero(v["tasas"], "tasas", 1, 99_999_999)
    if v.get("titulo_costo") not in (None, ""):
        out["titulo_costo"] = _a_entero(v["titulo_costo"], "titulo_costo", 1, 99_999_999)
    if v.get("vigencia_dias") not in (None, ""):
        out["vigencia_dias"] = _a_entero(v["vigencia_dias"], "vigencia_dias", 1, 90)
    if "alcance" in v and v["alcance"] is not None:
        out["alcance"] = _texto(v["alcance"], "alcance", 80, obligatorio=True)
    if "valores_mes" in v and v["valores_mes"] is not None:
        out["valores_mes"] = _texto(v["valores_mes"], "valores_mes", 30)
    if "pago" in v and v["pago"] is not None:
        out["pago"] = _texto(v["pago"], "pago", 300, obligatorio=True)
    if "alias" in v and v["alias"] is not None:
        alias = _texto(v["alias"], "alias", 30)
        if alias and not re.fullmatch(r"[A-Za-z0-9._-]{6,30}", alias):
            raise ValueError("Alias: usá entre 6 y 30 letras, números, puntos o guiones, sin espacios")
        out["alias"] = alias
    if "cvu" in v and v["cvu"] is not None:
        cvu = re.sub(r"\s", "", str(v["cvu"]))
        if cvu and not re.fullmatch(r"\d{22}", cvu):
            raise ValueError("CVU/CBU: tiene que tener 22 números")
        out["cvu"] = cvu
    if "titular_cuenta" in v and v["titular_cuenta"] is not None:
        out["titular_cuenta"] = _texto(v["titular_cuenta"], "titular_cuenta", 80)
    if completo:
        for c in OBLIGATORIOS_PARA_ENVIAR:
            if not out.get(c):
                raise ValueError(f"{ETIQUETAS[c]}: falta completarlo")
    return out


# ── Configuración guardada ────────────────────────────────────────────────
def config_efectiva(guardada: dict = None) -> dict:
    """Valores por defecto con lo guardado encima."""
    cfg = dict(DEFAULTS)
    for k, v in (guardada or {}).items():
        if k in DEFAULTS and v is not None:
            cfg[k] = v
    return cfg


def faltantes(cfg: dict) -> list:
    """Etiquetas de lo que falta cargar para poder mandar un presupuesto."""
    return [ETIQUETAS[c] for c in OBLIGATORIOS_PARA_ENVIAR if not (cfg.get(c) or "").strip()]


def leer_config(cur, tipo: str) -> dict:
    """Config efectiva (defaults + guardado) del presupuesto `tipo`."""
    cur.execute("SELECT valor FROM vigilancia_config WHERE clave = %s", (TIPOS[tipo]["config_clave"],))
    f = cur.fetchone()
    crudo = (f["valor"] if isinstance(f, dict) else f[0]) if f else None
    try:
        guardada = json.loads(crudo) if crudo else {}
    except ValueError:
        guardada = {}
    return config_efectiva(guardada if isinstance(guardada, dict) else {})


def guardar_config(cur, tipo: str, valores: dict) -> dict:
    """Valida y guarda (mezclando con lo que ya había). Devuelve la config efectiva."""
    nuevos = validar(valores)
    cur.execute("SELECT valor FROM vigilancia_config WHERE clave = %s", (TIPOS[tipo]["config_clave"],))
    f = cur.fetchone()
    crudo = (f["valor"] if isinstance(f, dict) else f[0]) if f else None
    try:
        previo = json.loads(crudo) if crudo else {}
    except ValueError:
        previo = {}
    guardado = {**(previo if isinstance(previo, dict) else {}), **nuevos}
    cur.execute("INSERT INTO vigilancia_config (clave, valor) VALUES (%s, %s) "
                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor",
                (TIPOS[tipo]["config_clave"], json.dumps(guardado, ensure_ascii=False)))
    return config_efectiva(guardado)


# ── Datos de un envío ─────────────────────────────────────────────────────
def con_ajustes(cfg: dict, ajustes: dict) -> dict:
    """Config con los ajustes de UN envío (solo AJUSTABLES_POR_ENVIO)."""
    limpios = validar({k: v for k, v in (ajustes or {}).items() if k in AJUSTABLES_POR_ENVIO})
    return {**cfg, **limpios}


def armar_datos(cfg: dict, hoy: _dt.date = None) -> dict:
    """El snapshot con el que se arma el PDF (se guarda en mails_envios.detalle)."""
    hoy = hoy or hoy_ar()
    return {
        "tipo": "registro_marca",
        "fecha": hoy.isoformat(),
        "honorarios": int(cfg["honorarios"]),
        "tasas": int(cfg["tasas"]),
        "titulo_costo": int(cfg["titulo_costo"]),
        "vigencia_dias": int(cfg["vigencia_dias"]),
        "alcance": cfg["alcance"],
        "valores_mes": (cfg.get("valores_mes") or "").strip() or mes_anio(hoy),
        "pago": cfg["pago"],
        "alias": cfg.get("alias") or "",
        "cvu": cfg.get("cvu") or "",
        "titular_cuenta": cfg.get("titular_cuenta") or "",
    }


def variables_mail(datos: dict) -> dict:
    """Variables que se pueden usar en el asunto y el cuerpo del mail del presupuesto."""
    return {
        "honorarios": fmt_pesos(datos["honorarios"]),
        "tasas": fmt_pesos(datos["tasas"]),
        "total": fmt_pesos(datos["honorarios"] + datos["tasas"]),
        "vigencia": fmt_dias(datos["vigencia_dias"]),
    }


def resumen(datos: dict) -> str:
    """Una línea para el historial / el CRM."""
    return f"{fmt_pesos(datos['honorarios'])} + tasas {fmt_pesos(datos['tasas'])} ({datos['alcance']})"


def nombre_archivo(datos: dict, marca: str = "") -> str:
    base = TIPOS[datos.get("tipo", "registro_marca")]["archivo"]
    m = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ ]+", " ", marca or "")
    m = " ".join(m.split())[:40]
    return f"{base} - {m}.pdf" if m else f"{base}.pdf"


# ── PDF ───────────────────────────────────────────────────────────────────
_FUENTE = None


def _fuentes() -> dict:
    """Registra la tipografía una sola vez y devuelve los nombres a usar."""
    global _FUENTE
    if _FUENTE:
        return _FUENTE
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for familia, escala, escala_b in (("OpenSans", 1.0, 1.0), ("Carlito", 1.1, 1.05)):
        archivos = [os.path.join(FUENTES, f"{familia}-{e}.ttf") for e in ("Regular", "Bold", "Italic")]
        if not all(os.path.exists(a) for a in archivos):
            continue
        r, b, i = (f"{familia}-{e}" for e in ("R", "B", "I"))
        for nombre, ruta in zip((r, b, i), archivos):
            pdfmetrics.registerFont(TTFont(nombre, ruta))
        pdfmetrics.registerFontFamily(r, normal=r, bold=b, italic=i, boldItalic=b)
        _FUENTE = {"r": r, "b": b, "i": i, "escala": escala, "escala_b": escala_b, "nombre": familia}
        return _FUENTE
    _FUENTE = {"r": "Helvetica", "b": "Helvetica-Bold", "i": "Helvetica-Oblique", "escala": 1.0, "escala_b": 1.0,
                   "nombre": "Helvetica"}
    return _FUENTE


COLOR_TEXTO = "#161242"
COLOR_TITULO = "#150F3D"
COLOR_SUBTITULO = "#4964D4"
AJUSTE = 1.25  # pt: calibración vertical contra el modelo de Canva


def generar_pdf(d: dict) -> bytes:
    """El presupuesto de registro de marca en PDF (A4, una hoja) a partir del snapshot `d`."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer

    F = _fuentes()
    esc = F["escala"]
    ancho, alto = A4  # 595.27 x 841.89

    def estilo(nombre, fuente="r", size=11, leading=15.75, alin=TA_LEFT, color=COLOR_TEXTO, **kw):
        # La negrita de Open Sans es más ancha que la de Carlito: se compensa un poco.
        tam = size * esc * (F["escala_b"] if fuente == "b" else 1.0)
        return ParagraphStyle(nombre, fontName=F[fuente], fontSize=tam, leading=leading,
                              alignment=alin, textColor=HexColor(color), **kw)

    titulo = estilo("titulo", "b", 20, 24, TA_CENTER, COLOR_TITULO)
    subtitulo = estilo("subtitulo", "b", 14, 19, TA_CENTER, COLOR_SUBTITULO)
    cuerpo = estilo("cuerpo", alin=TA_JUSTIFY)
    negrita = estilo("negrita", "b")
    cursiva = estilo("cursiva", "i")
    chico = estilo("chico", size=9, leading=12.8)
    # Las viñetas del modelo no llegan hasta el margen derecho: se cortan antes.
    punto = estilo("punto", leftIndent=18.7, rightIndent=28, bulletIndent=6, bulletFontName=F["r"],
                   bulletFontSize=11 * esc)
    linea = estilo("linea")

    h = []
    previo = [None]

    def bloque(par, st, gap=None):
        """Agrega un párrafo con `gap` pt de línea base a línea base respecto de
        la última línea del bloque anterior (así el ritmo vertical es el del
        modelo aunque cambie cuántas líneas ocupa cada texto)."""
        if previo[0] is not None and gap is not None:
            antes = previo[0]
            h.append(Spacer(1, max(0.0, gap - (antes.leading - antes.fontSize) - st.fontSize)))
        h.append(par)
        previo[0] = st

    def p(texto, st, gap=None):
        bloque(Paragraph(texto, st), st, gap)

    def vineta(texto, gap=None):
        bloque(Paragraph(texto, punto, bulletText="•"), punto, gap)

    honorarios = fmt_pesos(d["honorarios"])
    tasas = fmt_pesos(d["tasas"])
    titulo_costo = fmt_pesos(d["titulo_costo"])
    e = escape
    L = 15.75  # interlineado del modelo (una línea en blanco = 2 L)

    p("Propuesta de Registro de Marca", titulo)
    p("Exclusividad legal para tu marca en Argentina", subtitulo, 24.7)
    p("Para solicitar el registro de una marca en Argentina y obtener el derecho exclusivo de uso, "
      f"el valor por {e(d['alcance'])} es de:", cuerpo, 23.3)
    p(f"{honorarios} + tasas oficiales", negrita, 31.6)
    p(f"Valor actual de las tasas {tasas} *", chico, 14.1)
    p("*El valor de las tasas puede variar de acuerdo con el valor vigente de la UMAPI.", chico, 12.8)
    p("Las marcas se presentan por clases, de acuerdo con el nomenclador internacional, según los "
      "productos o servicios que se desean proteger.", cuerpo, 27.1)
    p("El servicio incluye:", cursiva, 2 * L)
    vineta("Análisis previo de disponibilidad marcaria", 2 * L)
    vineta("Evaluación inicial de posibles riesgos registrales", L)
    vineta("Definición de estrategia de clases", L)
    vineta("Preparación y presentación de la solicitud", L)
    vineta("Seguimiento administrativo del expediente hasta la concesión.", L)
    p("¿Por qué registrar una marca?", negrita, 2 * L)
    p("Registrar una marca permite resguardar legalmente el nombre de tu empresa, prevenir conflictos "
      "con terceros y avanzar con mayor seguridad comercial.", cuerpo, 2 * L)
    p("Condiciones:", cursiva, 2 * L)
    vineta(f"Vigencia del presupuesto: {fmt_dias(d['vigencia_dias'])}.", L)
    vineta(f"Pago: {e(d['pago'])}", L)
    vineta("No incluye oposiciones ni contestación de vistas.", L)
    vineta("El título se gestiona una vez concedida la marca y tiene costo adicional. "
           f"(Valor {e(d['valores_mes'])} {titulo_costo})", L)
    p("Datos de transferencia:", linea, 33.0)
    p(f"Alias: {e(d.get('alias') or '—')}", linea, L)
    p(f"CVU: {e(d.get('cvu') or '—')}", linea, L)
    p(f"Nombre: {e(d.get('titular_cuenta') or '—')}", linea, L)

    pie = Paragraph(
        e(PIE_PARTES) + f'<u><a href="mailto:{MARCA_EMAIL}" color="{COLOR_TEXTO}">{MARCA_EMAIL}</a></u> | ' + e(PIE_TELEFONO),
        estilo("pie", size=9, leading=15.8))

    fecha = _dt.date.fromisoformat(d["fecha"])
    cuando = mes_anio(fecha)

    def decorar(c: _canvas.Canvas, doc):
        c.saveState()
        # Logo (mosaico) arriba a la derecha, pegado al borde.
        if os.path.exists(LOGO):
            c.drawImage(LOGO, ancho - 132.8, alto - 66.0, width=132.8, height=66.0, mask="auto")
        # Bloque de datos de la consultora, alineado a la derecha.
        c.setFillColor(HexColor(COLOR_TITULO))
        c.setFont(F["b"], 12 * esc * F["escala_b"])
        c.drawRightString(579.3, alto - (87.6 - AJUSTE), MARCA_NOMBRE)
        c.setFillColor(HexColor(COLOR_TEXTO))
        c.setFont(F["r"], 9 * esc)
        for texto, y in ((MARCA_WEB, 104.9), (MARCA_EMAIL, 120.6), (cuando, 136.4)):
            c.drawRightString(579.3, alto - (y - AJUSTE), texto)
        # Pie: la primera línea apoya su base 791.4 pt más abajo del borde de arriba.
        _, alto_pie = pie.wrap(476.5, 60)
        pie.drawOn(c, 59.5, alto - (791.4 - AJUSTE - 9 * esc) - alto_pie)
        c.restoreState()

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, title=TIPOS["registro_marca"]["archivo"], author=MARCA_NOMBRE)
    # La primera línea del título apoya su base 167.7 pt más abajo del borde de arriba.
    arriba = 167.7 - AJUSTE - titulo.fontSize
    marco = Frame(75.4, 70.0, 520.1 - 75.4, alto - arriba - 70.0, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0, id="cuerpo")
    doc.addPageTemplates([PageTemplate(id="pagina", frames=[marco], onPage=decorar)])
    doc.build(h)
    return buf.getvalue()
