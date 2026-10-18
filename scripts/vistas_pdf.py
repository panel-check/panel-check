"""
Lectura del PDF de una VISTA de INPI (fila «Vista de Marcas» de la Grilla Digital).

Una vista de una solicitud todavía sin publicar puede ser de dos tipos, con plazos
distintos (confirmado con los textos reales del 17/10/2026):

  fondo   "Visto el expediente bajo análisis se notifica al solicitante que: el signo
          pretendido se encuentra alcanzado por el/los ART. 3 A de la Ley 22.362 ...
          La presente vista se notifica por 30 días corridos de acuerdo a la Resolución
          INPI P 123/2019, anexo, artículo 5°, pudiendo hacer uso de las extensiones
          automáticas (art. 6°, arancel cód. 770.001)".
          A veces trae el antecedente: "acta_antecedente: 4011896; denom: FELICIANO".
  formal  "Se hace saber al interesado que deberá acompañar nuevos logotipos ...
          Notifíquese: por el plazo de 10 (diez) días hábiles bajo apercibimiento de
          dictar resolución denegatoria, conforme al artículo 12 del decreto 242/19".
  convenio_paris  (visto el 17/10/2026, 30 días corridos) "El signo bajo análisis podría constituir
          la reproducción, imitación o traducción de una marca notoriamente conocida, a saber
          "NIKE" ... Convenio de París, artículo 6bis" (marca notoria), o "El término "PATAGONIA"
          ha obtenido el reconocimiento ... como Símbolo/Emblema de Estado Nacional ... art. 6 Ter
          Convenio Unión de París" (emblema oficial).
  clasificacion  (formal, 10 días hábiles) "deberá presentar escrito limitando correctamente los
          productos/servicios a distinguir, toda vez que la expresión "STOCK" resulta imprecisa ...
          deberá eliminar los siguientes productos/servicios por no corresponder a la clase".

El texto que sale del PDF viene con palabras pegadas ("seencuentraalcanzado",
"30 díascorridos") y con el orden de los bloques mezclado, así que todo se busca
tolerando espacios de más o de menos.

El plazo que se calcula acá sale de lo que DICE la vista: es una estimación. El campo
Fecha_Vencimiento del expediente de INPI puede dar otra fecha (en las primeras 4 vistas
daba notificación + 45 días); se guardan las dos y para decidir se usa la más cercana.
Los días hábiles se cuentan sin sábados ni domingos pero SIN descontar feriados, así que
la fecha puede quedar unos días corta o larga.
"""

import datetime as dt
import io
import re
import unicodedata

from validar_leads import BASE, _get_con_reintentos, _parsear_fecha_grilla

MAX_TEXTO_GUARDADO = 6000  # caracteres del texto de la vista que se guardan (para poder mejorar el lector)

RE_PLAZO = re.compile(
    r"(\d{1,3})\s*(?:\(\s*[a-záéíóúñ ]+\s*\))?\s*d[ií]as\s*(h[aá]biles|corridos)", re.I)
RE_ARTICULO = re.compile(
    r"\bART\.?\s*(\d+)\s*(?:INC\.?\s*)?\(?\s*((?-i:[A-Z]))?\s*\)?\s*(?:d\s*e\s*)?(?:l\s*a\s*|l\s*)?Ley",
    re.I)
# Variante «Dese vista al interesado de lo establecido en el artículo 3, inciso a), de la Ley 22.362»
RE_ARTICULO_LARGO = re.compile(
    r"\bart[ií]culos?\s*(\d+)\s*,?\s*inc(?:iso|\.)?\s*\(?\s*([a-z])\s*\)?\s*,?\s*(?:d\s*e\s*)?(?:l\s*a\s*|l\s*)?Ley\s*(?:N[°º.]?\s*)?22\.?362",
    re.I)
# Convenio de París: 6 bis (marca notoriamente conocida) o 6 ter (emblemas oficiales)
RE_PARIS = re.compile(r"\b6\s*(bis|ter)\b", re.I)
RE_CONVENIO = re.compile(r"Convenio\s+(?:de\s+|Uni[oó]n\s+de\s+)?(?:la\s+Uni[oó]n\s+de\s+)?Par[ií]s", re.I)
RE_MOTIVO_NOTORIA = re.compile(r"a\s*saber\s*[\"“«]([^\"”»]{1,80})[\"”»]", re.I)
RE_MOTIVO_TERMINO = re.compile(r"t[eé]rmino\s*[\"“«]([^\"”»]{1,80})[\"”»]", re.I)
RE_MOTIVO_EXPRESION = re.compile(r"expresi[oó]n\s*[\"“«]([^\"”»]{1,80})[\"”»]", re.I)
RE_CLASIFICACION = re.compile(
    r"limitando\s*correctamente\s*los\s*productos|no\s*corresponder\s*a\s*la\s*clase|clasificaci[oó]n\s*de\s*(?:los\s*)?productos", re.I)
# La denominación del antecedente termina donde empieza lo siguiente: «El signo…», otro antecedente,
# «Dese vista…», «Notifíquese» o «Visto…» (si no, se llevaba pegado el resto del texto).
RE_ANTECEDENTE = re.compile(
    r"acta_antecedente:\s*(\d+)\s*;?\s*(?:denom:\s*(.*?))?\s*"
    r"(?=El\s*signo|acta_antecedente|D[eé]se\s*vista|Not[ií]f[ií]quese|Visto\s*el|$)", re.I)


def _plano(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def _norm(s: str) -> str:
    n = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in n if unicodedata.category(c) != "Mn").upper().strip()


def parsear_vista(texto: str) -> dict:
    """Interpreta el texto de una vista. Devuelve:
        tipo: 'fondo' | 'convenio_paris' | 'clasificacion' | 'formal' | 'fondo_y_formal' | 'desconocido'
        articulos: ['Art. 3 inc. a)', ...] de la Ley 22.362, o ['Convenio de París, art. 6 bis']
        motivo: lo que INPI cita como causa ("NIKE", "PATAGONIA", "STOCK") o ''
        antecedentes: ['4011896 (FELICIANO)', ...]
        plazos: [(30, 'corridos'), (10, 'habiles')] en el orden en que aparecen
        extensiones_automaticas: True si menciona las extensiones automáticas pagas
    """
    p = _plano(texto)
    articulos = []
    for m in sorted(list(RE_ARTICULO.finditer(p)) + list(RE_ARTICULO_LARGO.finditer(p)), key=lambda x: x.start()):
        art = f"Art. {m.group(1)}"
        if m.group(2):
            art += f" inc. {m.group(2).lower()})"
        if art not in articulos:
            articulos.append(art)
    es_ley = bool(articulos)
    antecedentes = []
    for m in RE_ANTECEDENTE.finditer(p):
        denom = (m.group(2) or "").strip(" ;.")
        antecedentes.append(f"{m.group(1)} ({denom})" if denom else m.group(1))
    plazos = []
    for m in RE_PLAZO.finditer(p):
        par = (int(m.group(1)), "habiles" if m.group(2).lower().startswith("h") else "corridos")
        if par not in plazos:
            plazos.append(par)
    # Un número de días suelto que no es plazo de la vista no se cuenta (ej. "200 y 300 DPI" no matchea).
    compacto = re.sub(r"\s+", "", p).lower()

    # Convenio de París (marca notoria 6 bis / emblema oficial 6 ter)
    es_paris = bool(RE_PARIS.search(p)) and bool(RE_CONVENIO.search(p) or "notoriamente" in compacto or "emblema" in compacto)
    motivo = ""
    if es_paris:
        for m in RE_PARIS.finditer(p):
            art = f"Convenio de París, art. 6 {m.group(1).lower()}"
            if art not in articulos:
                articulos.append(art)
        m = RE_MOTIVO_NOTORIA.search(p) or RE_MOTIVO_TERMINO.search(p)
        motivo = m.group(1).strip() if m else ""

    es_clasificacion = bool(RE_CLASIFICACION.search(p))
    if es_clasificacion and not motivo:
        m = RE_MOTIVO_EXPRESION.search(p)
        motivo = m.group(1).strip() if m else ""

    es_fondo = es_ley or ("ley22.362" in compacto and "elsignopretendido" in compacto)
    es_logos = "logotipo" in compacto
    es_formal = es_logos or es_clasificacion or "vistaformal" in compacto or "decreto242/19" in compacto
    if es_fondo and es_logos:
        tipo = "fondo_y_formal"
    elif es_fondo:
        tipo = "fondo"
    elif es_paris:
        tipo = "convenio_paris"
    elif es_clasificacion and not es_logos:
        tipo = "clasificacion"
    elif es_formal:
        tipo = "formal"
    else:
        tipo = "desconocido"
    return {
        "tipo": tipo,
        "articulos": articulos,
        "motivo": motivo,
        "antecedentes": antecedentes,
        "plazos": plazos,
        "extensiones_automaticas": "extensionesautom" in compacto,
    }


MAX_LARGO_ANTECEDENTE = 80  # un «acta (denominación)» más largo que esto casi seguro arrastra texto de más


def problemas_de_lectura(parsed: dict | None) -> list[str]:
    """Qué no pudo entender el lector de una vista ([] = todo claro). Es lo que dispara el mail
    «Una vista que el sistema no supo leer» para que alguien mire el texto y mejore el lector."""
    if not parsed:
        return ["no se pudo bajar o leer el PDF de la vista"]
    problemas = []
    if parsed["tipo"] == "desconocido":
        problemas.append("no se reconoce el tipo de vista")
    elif parsed["tipo"] in ("fondo", "fondo_y_formal") and not parsed["articulos"]:
        problemas.append("es de fondo pero no se encontró el artículo")
    if not parsed["plazos"]:
        problemas.append("no se encontró el plazo para contestar")
    if any(len(a) > MAX_LARGO_ANTECEDENTE for a in parsed["antecedentes"]):
        problemas.append("la marca anterior parece traer texto de más")
    return problemas


def sumar_dias_habiles(inicio_iso: str, n: int) -> str:
    """n días hábiles después de inicio (lunes a viernes; no descuenta feriados)."""
    d = dt.date.fromisoformat(inicio_iso)
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d.isoformat()


def vencimiento_desde_texto(parsed: dict, notificacion_iso: str | None, fecha_vista_iso: str | None):
    """(vencimiento_iso | None, estimado: bool). Parte de la fecha de notificación; si
    todavía no hay (INPI no la cargó), de la fecha de la vista, y queda como estimado.
    Con dos plazos (vista de fondo y formal a la vez) se toma el que vence antes."""
    base = notificacion_iso or fecha_vista_iso
    if not base or not parsed["plazos"]:
        return None, False
    fechas = []
    for n, tipo in parsed["plazos"]:
        if tipo == "habiles":
            fechas.append(sumar_dias_habiles(base, n))
        else:
            fechas.append((dt.date.fromisoformat(base) + dt.timedelta(days=n)).isoformat())
    return min(fechas), not notificacion_iso


def plazo_legible(parsed: dict) -> str:
    return " + ".join(f"{n} días {'hábiles' if t == 'habiles' else 'corridos'}" for n, t in parsed["plazos"])


def filas_de_vista(archivos: list[dict], desde_iso: str | None = None) -> list[dict]:
    """Filas «Vista …» de la Grilla (más nuevas primero), fechadas en o después de desde_iso."""
    filas = []
    for a in archivos or []:
        if "VISTA" not in _norm(a.get("Indice") or ""):
            continue
        f = _parsear_fecha_grilla(a.get("Fecha") or "")
        if desde_iso and (not f or f < desde_iso):
            continue
        filas.append((f or "", a))
    return [a for _, a in sorted(filas, key=lambda x: x[0], reverse=True)]


def fila_de_cedula(archivos: list[dict], desde_iso: str | None) -> dict | None:
    for a in archivos or []:
        if "CEDULA" in _norm(a.get("Indice") or ""):
            f = _parsear_fecha_grilla(a.get("Fecha") or "")
            if f and (not desde_iso or f >= desde_iso):
                return a
    return None


def descargar_texto_pdf(s, fila: dict, timeout: int = 30) -> str:
    """Baja el PDF de una fila de la Grilla (con la sesión del portal) y devuelve su texto
    ('' si no se pudo). Es el mismo camino que usa el Formulario de la oposición: el archivo
    solo se baja con la sesión de INPI, por eso no sirve mandarlo a un servicio externo."""
    id_doc = fila.get("id_Documento_encriptado")
    ruta = fila.get("ruta") or ""
    if not id_doc or not ruta:
        return ""
    try:
        r = _get_con_reintentos(
            lambda: s.get(
                f"{BASE}/Home/edmsxidd",
                params={"id": id_doc, "nombre": ruta.rsplit("/", 1)[-1]},
                headers={"Referer": f"{BASE}/Home/GrillaDigital"},
                timeout=timeout,
            )
        )
        if r.headers.get("Content-Type", "").lower() != "application/pdf":
            return ""
        import pdfplumber

        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:  # noqa: BLE001 — no debe romper la revisión
        return ""


def leer_vista(s, archivos: list[dict], desde_iso: str | None, notificacion_iso: str | None = None) -> dict:
    """Lee la vista más nueva de la Grilla (si el plazo no aparece, prueba con la Cédula de
    Notificación). {} si no hay PDF legible. Devuelve parsear_vista() más: texto, fecha_vista,
    vencimiento_texto, vencimiento_estimado."""
    filas = filas_de_vista(archivos, desde_iso)
    if not filas:
        return {}
    fila = filas[0]
    fecha_vista = _parsear_fecha_grilla(fila.get("Fecha") or "")
    texto = descargar_texto_pdf(s, fila)
    parsed = parsear_vista(texto) if texto.strip() else None
    if parsed is None or not parsed["plazos"]:
        cedula = fila_de_cedula(archivos, fecha_vista)
        if cedula:
            texto_c = descargar_texto_pdf(s, cedula)
            if texto_c.strip():
                texto = (texto + "\n" + texto_c).strip()
                parsed = parsear_vista(texto)
    if not texto.strip() or parsed is None:
        return {}
    venc, estimado = vencimiento_desde_texto(parsed, notificacion_iso, fecha_vista)
    parsed.update({
        "texto": _plano(texto)[:MAX_TEXTO_GUARDADO],
        "fecha_vista": fecha_vista,
        "vencimiento_texto": venc,
        "vencimiento_estimado": estimado,
    })
    return parsed
