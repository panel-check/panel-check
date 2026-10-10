"""
Copia acotada de la lógica de scripts/validar_leads.py y
scripts/listar_boletines.py, para que el panel pueda volver a consultar
INPI directamente (botón "Reintentar", sección /boletines) sin depender
del resto del repo (Railway deploya este servicio con root
directory = panel/, así que no tiene acceso a scripts/).

Si INPI cambia el HTML o los endpoints, hay que actualizar este archivo Y
los scripts equivalentes en scripts/ — quedan separados a propósito, no
importan uno del otro.

Excepción a propósito (30/09/2026): scripts/validar_leads.py.revisar_acta
también parsea denominación/tipo/titular/fecha de depósito del Formulario
(para scripts/escanear_actas_nuevas.py, que no tiene boletín del que
sacarlos). Ese script no corre en el panel -- solo el botón "Reintentar",
que ya tiene esos datos del boletín -- así que no se duplicó acá.
"""

import base64
import html as _html
import io
import re
import sys
import time
import xml.etree.ElementTree as ET
from html import escape as _xml_escape

import pdfplumber
import requests
from bs4 import BeautifulSoup

BASE = "https://portaltramites.inpi.gob.ar"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
}

RE_GESTION = re.compile(r"GESTION DEL TRAMITE.*?</h4>\s*</div>\s*<div[^>]*>(.*?)</div>\s*</div>\s*</div>", re.S)
RE_CARACTER_SPAN = re.compile(r"CARACTER\s*:?\s*<span[^>]*>(.*?)</span>", re.S)
# El CUIT viene envuelto en <span> en algunas fichas (personas jurídicas) y
# como texto plano dentro del mismo <label> en otras (personas físicas) —
# "CUIT: 20302361291" sin span. El <span> es opcional para cubrir ambos casos.
RE_CUIT_SPAN = re.compile(r"CUIT\s*:?\s*(?:<span[^>]*>)?\s*([\d.\-]{6,})", re.S)
RE_CLASE_SPAN = re.compile(r"CLASE\s*:?\s*(?:<span[^>]*>)?\s*(\d{1,2})", re.S)

# Ver el mismo comentario en validar_leads.py: sección RESOLUCIÓN, en la
# misma página. VENCE ya viene calculado por INPI (concesión + 10 años).
# DISPOSICION envuelve la ETIQUETA "Fecha:" en su propio <span>, separado
# del <span class="text-danger"> que envuelve el valor — ver el mismo
# comentario, con el HTML real confirmado, en validar_leads.py.
RE_RESOLUCION_BLOQUE = re.compile(r"RESOLUCI\wN.*?(?=DICT\wMENES|$)", re.S)
RE_TIPO_RESOLUCION = re.compile(
    r"TIPO\s*:?\s*(?:<span[^>]*>)?\s*([^<\n]+?)(?=\s+[A-ZÁÉÍÓÚÑ]{2,}\s*:|\s*<|\n|$)"
)
RE_DISPOSICION = re.compile(
    r"DISPOSICION\s*:?\s*(?:<span[^>]*>)?\s*Fecha\s*:?\s*(?:</span>)?\s*(?:<span[^>]*>)?\s*([\d/]+)"
    r"\s*(?:</span>)?\s*(?:<span[^>]*>)?\s*-\s*Numero\s*:?\s*(?:</span>)?\s*(?:<span[^>]*>)?\s*([^\s<]+)",
    re.S,
)
RE_VENCE = re.compile(r"VENCE\s*:?\s*(?:<span[^>]*>)?\s*([\d/]+)")


def _fecha_ddmmyyyy_a_iso(valor: str):
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", valor.strip())
    if not m:
        return None
    d, mes, anio = m.groups()
    return f"{anio}-{mes.zfill(2)}-{d.zfill(2)}"


def _parsear_resolucion(texto_pagina: str) -> dict:
    resultado = {
        "estado_tramite": None, "fecha_concesion": None,
        "numero_disposicion": None, "fecha_vencimiento_marca": None,
    }
    m_bloque = RE_RESOLUCION_BLOQUE.search(texto_pagina)
    if not m_bloque:
        return resultado
    bloque = m_bloque.group(0)
    m_tipo = RE_TIPO_RESOLUCION.search(bloque)
    if m_tipo:
        resultado["estado_tramite"] = re.sub(r"\s+", " ", m_tipo.group(1)).strip()
    m_disp = RE_DISPOSICION.search(bloque)
    if m_disp:
        resultado["fecha_concesion"] = _fecha_ddmmyyyy_a_iso(m_disp.group(1))
        resultado["numero_disposicion"] = m_disp.group(2).strip()
    m_vence = RE_VENCE.search(bloque)
    if m_vence:
        resultado["fecha_vencimiento_marca"] = _fecha_ddmmyyyy_a_iso(m_vence.group(1))
    return resultado


def _crear_sesion() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(f"{BASE}/MarcasConsultas/Grilla", timeout=30)
    except requests.RequestException as e:
        print(f"aviso: no se pudo pre-cargar sesión ({e})", file=sys.stderr)
    return s


def _get_con_reintentos(fn, intentos: int = 3, espera: int = 3):
    ultimo_error = None
    for _ in range(intentos):
        try:
            return fn()
        except requests.RequestException as e:
            ultimo_error = e
            time.sleep(espera)
    raise ultimo_error


def _buscar_archivos_grilla(s: requests.Session, acta: str, timeout: int = 30) -> list:
    """Igual que validar_leads.buscar_archivos_grilla."""
    r_gd = _get_con_reintentos(
        lambda: s.post(
            f"{BASE}/Home/GrillaDigital",
            headers={"Referer": f"{BASE}/MarcasConsultas/Resultado"},
            data={"fname": f"1-{acta}"},
            timeout=timeout,
        )
    )
    if "Web Page Blocked" in r_gd.text or "Attack ID" in r_gd.text:
        return []
    r_archivos = _get_con_reintentos(
        lambda: s.post(
            f"{BASE}/Home/GrillaDigitales",
            headers={"Referer": f"{BASE}/Home/GrillaDigital", "X-Requested-With": "XMLHttpRequest"},
            data={"acta": acta, "limit": 50, "offset": 0, "direccion": 1},
            timeout=timeout,
        )
    )
    try:
        return r_archivos.json().get("rows", [])
    except ValueError:
        return []


# Ver el mismo comentario en validar_leads.py — términos universales que usa
# INPI para marcar una oposición de tercero o una vista de oficio.
TERMINOS_OPOSICION = ("OPO", "VISTA", "OPOSICION", "OPOSICIÓN")


def _detectar_oposicion(archivos: list, fecha_publicacion=None) -> tuple:
    """Ver el comentario completo en validar_leads.detectar_oposicion: solo
    cuenta una oposición/vista fechada en o después de la publicación
    vigente — una vista vieja de una presentación anterior ya está resuelta."""
    for a in archivos:
        indice = (a.get("Indice") or "").upper()
        referencia = (a.get("Referencia") or "").upper()
        if not any(t in indice or t in referencia for t in TERMINOS_OPOSICION):
            continue
        if fecha_publicacion:
            fecha_fila = _parsear_fecha_grilla(a.get("Fecha") or "")
            if not fecha_fila or fecha_fila < fecha_publicacion:
                continue
        detalle = f"{a.get('Fecha', '')} - {a.get('Indice', '')} - {a.get('Referencia', '')}"
        return True, detalle
    return False, ""


# Ver el mismo comentario en validar_leads.py: el campo Fecha no siempre
# viene como "DD/MM/YYYY" — a veces viene en un formato de fecha .NET con
# los milisegundos desde 1970 dentro de "Date(...)".
_RE_FECHA_DOTNET = re.compile(r"Date\((-?\d+)")


def _parsear_fecha_grilla(valor: str):
    m_dotnet = _RE_FECHA_DOTNET.search(valor)
    if m_dotnet:
        try:
            import datetime as _dt

            ms = int(m_dotnet.group(1))
            return _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc).date().isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    m_ddmmyyyy = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", valor.strip())
    if not m_ddmmyyyy:
        return None  # formato inesperado: mejor None que adivinar mal
    d, m, y = m_ddmmyyyy.groups()
    return f"{y}-{m.zfill(2)}-{d.zfill(2)}"


def _fecha_publicacion_de_archivos(archivos: list):
    fila = next((a for a in archivos if a.get("Indice") == "Hoja Publicacion"), None)
    if not fila or not fila.get("Fecha"):
        return None
    return _parsear_fecha_grilla(fila["Fecha"])


def revisar_acta(acta: str, timeout: int = 30) -> dict:
    """Igual que validar_leads.revisar_acta: devuelve
    {caracter, es_lead, email, email_apoderado, motivo_sin_email,
    fecha_publicacion, tuvo_oposicion, detalle_oposicion} y, cuando se pudo
    leer de la sección TITULARIDAD, también "cuit" (clave real para unificar
    leads del mismo titular, mejor que comparar texto)."""
    s = _crear_sesion()
    resultado = {
        "caracter": None, "es_lead": None, "email": "", "email_apoderado": "",
        "motivo_sin_email": "", "fecha_publicacion": None,
        "estado_tramite": None, "fecha_concesion": None,
        "numero_disposicion": None, "fecha_vencimiento_marca": None,
        "clase": None,  # ver RE_CLASE_SPAN -- mismo patrón que CUIT/CARACTER
    }
    try:
        r = _get_con_reintentos(
            lambda: s.post(
                f"{BASE}/MarcasConsultas/Resultado",
                headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
                data={"acta": acta},
                timeout=timeout,
            )
        )
        if "Web Page Blocked" in r.text or "Attack ID" in r.text:
            resultado["motivo_sin_email"] = "bloqueado por el WAF de INPI al consultar el expediente"
            return resultado

        m_gestion = RE_GESTION.search(r.text)
        bloque_gestion = m_gestion.group(1) if m_gestion else r.text

        m_caracter = RE_CARACTER_SPAN.search(bloque_gestion)
        caracter = re.sub(r"\s+", " ", m_caracter.group(1)).strip() if m_caracter else ""

        resultado["caracter"] = caracter
        resultado["es_lead"] = caracter == ""

        m_cuit = RE_CUIT_SPAN.search(r.text)
        if m_cuit:
            cuit_encontrado = re.sub(r"[^\d]", "", m_cuit.group(1))
            if len(cuit_encontrado) in (10, 11):
                resultado["cuit"] = cuit_encontrado

        m_clase = RE_CLASE_SPAN.search(r.text)
        if m_clase:
            resultado["clase"] = int(m_clase.group(1))

        resultado.update(_parsear_resolucion(r.text))

        if not resultado["es_lead"]:
            return resultado

        archivos = _buscar_archivos_grilla(s, acta, timeout=timeout)
        if not archivos:
            resultado["motivo_sin_email"] = "bloqueado por el WAF de INPI al abrir Grilla Digital"
            return resultado

        resultado["fecha_publicacion"] = _fecha_publicacion_de_archivos(archivos)
        resultado["tuvo_oposicion"], resultado["detalle_oposicion"] = _detectar_oposicion(
            archivos, resultado["fecha_publicacion"]
        )

        # Un expediente puede tener MÁS DE UN documento con Indice=="Formulario"
        # (ej. una versión inicial incompleta y una ampliatoria/corregida
        # después) -- antes del 29/09/2026 se tomaba siempre el primero
        # (next(...)), y si ese primero no tenía el EMAIL completo se
        # descartaba como "sin email" aunque uno posterior sí lo tuviera (caso
        # real: acta con EL DARU / VELARDE DARÍO SEBASTIAN, EMAIL presente en
        # el Formulario pero el sistema seguía diciendo que no estaba). Ahora
        # se prueban TODOS, del más reciente al más viejo (así se prioriza la
        # versión más probable de ser la definitiva), y nos quedamos con el
        # primero que efectivamente tenga un EMAIL parseable.
        formularios = [a for a in archivos if a.get("Indice") == "Formulario"]
        if not formularios:
            resultado["motivo_sin_email"] = "el expediente no tiene un archivo Formulario en Grilla Digital"
            return resultado

        motivo_formulario = "el Formulario no tiene el campo EMAIL completo"
        for formulario in reversed(formularios):
            id_doc = formulario["id_Documento_encriptado"]
            nombre_archivo = formulario["ruta"].rsplit("/", 1)[-1]
            try:
                r_pdf = _get_con_reintentos(
                    lambda: s.get(
                        f"{BASE}/Home/edmsxidd",
                        params={"id": id_doc, "nombre": nombre_archivo},
                        headers={"Referer": f"{BASE}/Home/GrillaDigital"},
                        timeout=timeout,
                    )
                )
            except Exception:
                continue
            if r_pdf.headers.get("Content-Type", "").lower() != "application/pdf":
                motivo_formulario = "no se pudo descargar el PDF del Formulario"
                continue

            with pdfplumber.open(io.BytesIO(r_pdf.content)) as pdf:
                texto = "\n".join(p.extract_text() or "" for p in pdf.pages)

            # ":" opcional -- igual que en RE_CUIT_SPAN, algunos Formulario lo
            # traen pegado sin dos puntos por cómo INPI arma el PDF.
            m_email = re.search(r"EMAIL\s*:?\s*([\w.+-]+@[\w-]+\.[\w.-]+)", texto)
            if m_email:
                resultado["email"] = m_email.group(1)
                m_email_apoderado = re.search(
                    r"REPRESENTACION.*?EMAIL\s*:?\s*([\w.+-]+@[\w-]+\.[\w.-]+)", texto, re.S
                )
                if m_email_apoderado:
                    resultado["email_apoderado"] = m_email_apoderado.group(1)
                break

        if not resultado["email"]:
            resultado["motivo_sin_email"] = motivo_formulario

    except Exception as e:
        resultado["caracter"] = None
        resultado["es_lead"] = None
        resultado["motivo_sin_email"] = f"error de conexión al consultar INPI: {e}"
    return resultado


def buscar_marca_por_denominacion(denominacion: str, clase: str = "", timeout: int = 30) -> list:
    """Busca marcas por denominación (contiene) en la API JSON
    GrillaMarcasAvanzada — usada por el botón "Ver marca opuesta" del
    popup de oposición, cuando el fundamento solo da el nombre + número de
    registro de la marca del oponente (no un ACTA directa para linkear).

    Ojo: esta API necesita la sesión/cookies de la página de "búsqueda
    avanzada" puntual (Cod_Funcion=NQA0ADE) — la de _crear_sesion() (que
    visita /MarcasConsultas/Grilla) no alcanza, devuelve 0 resultados
    aunque el status sea 200. Confirmado a mano el 29/09/2026.

    Mejor esfuerzo, no siempre encuentra el registro exacto: la
    denominación tal como aparece en el texto legal del fundamento puede
    tener espaciado distinto al que está cargado en INPI (confirmado a
    mano: "BALI STONE" con espacio no encontró nada, la marca real puede
    estar cargada distinto). Por eso el panel muestra la lista de
    resultados para que la persona elija, no un solo "mejor match"
    automático."""
    s = _crear_sesion()
    try:
        s.get(f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE", timeout=timeout)
    except requests.RequestException as e:
        print(f"aviso: no se pudo pre-cargar sesión de búsqueda avanzada ({e})", file=sys.stderr)

    payload = {
        "Tipo_Resolucion": "",
        "Clase": str(clase or ""),
        "TipoBusquedaDenominacion": "1",  # CONTIENE
        "Denominacion": denominacion,
        "Titular": "",
        "TipoBusquedaTitular": "0",
        "Fecha_IngresoDesde": "",
        "Fecha_IngresoHasta": "",
        "Fecha_ResolucionDesde": "",
        "Fecha_ResolucionHasta": "",
        "vigentes": False,
        "limit": 20,
        "offset": 0,
    }
    r = _get_con_reintentos(
        lambda: s.post(
            f"{BASE}/MarcasConsultas/GrillaMarcasAvanzada",
            json=payload,
            headers={
                "Referer": f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=timeout,
        )
    )
    try:
        data = r.json()
    except ValueError:
        return []
    filas = []
    for row in (data.get("rows") or [])[:20]:
        filas.append({
            "acta": str(row.get("Acta", "")).strip(),
            "denominacion": str(row.get("Denominacion", "")).strip(),
            "clase": row.get("Clase"),
            "numero_resolucion": str(row.get("Numero_Resolucion") or "").strip(),
            "estado": row.get("Estado"),
        })
    return filas


def calcular_lead_score(matricula_agente: str, es_lead, tiene_email: bool) -> int:
    """Misma fórmula que validar_leads.calcular_lead_score."""
    score = 0
    matricula = (matricula_agente or "").strip()
    if matricula == "" or matricula == "Part.":
        score += 50
    if es_lead is True:
        score += 50
    elif es_lead is False:
        score -= 100
    if tiene_email:
        score += 20
    return score


# --- Listado de boletines (para la sección /boletines) ----------------------
# Copia acotada de scripts/listar_boletines.py (ver docstring del módulo).
LISTADO_URL = f"{BASE}/Boletines?Tipo_Item=3"


def _parse_tabla_boletines(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    boletines = []
    for row in soup.select("table tr"):
        celdas = [c.get_text(strip=True) for c in row.find_all("td")]
        if not celdas:
            continue
        fila_txt = " | ".join(celdas)
        if "Boletines" not in fila_txt:
            continue
        if "MARCAS NUEVAS" not in fila_txt.upper():
            continue
        m = re.search(r"\b(\d{4,6})\b", fila_txt)
        if not m:
            continue
        numero = m.group(1)
        fecha_m = re.search(r"\d{1,2}/\d{1,2}/\d{4}", fila_txt)
        boletines.append({"numero": numero, "fecha": fecha_m.group(0) if fecha_m else None})
    return boletines


def listar_boletines_marcas_nuevas(timeout: int = 30) -> list[dict]:
    """Lista los boletines "MARCAS NUEVAS" que INPI tiene publicados (la
    misma página que usa scripts/listar_boletines.py en modo simple, sin
    rango de fechas). Devuelve [{"numero", "fecha"}], más nuevo primero."""
    r = requests.get(LISTADO_URL, timeout=timeout)
    r.raise_for_status()
    boletines = _parse_tabla_boletines(r.text)
    boletines.sort(key=lambda b: int(b["numero"]), reverse=True)
    return boletines


# --- Ficha completa de un expediente (cartera de clientes) ------------------
# Para la pestaña Clientes: dada un acta, trae TODO lo que INPI muestra sin
# depender de que la marca haya pasado por un boletín nuestro. Lo usan el
# panel (alta por acta / por CUIT, botón "Actualizar") y los scripts de
# seguimiento (scripts/revisar_cartera.py, scripts/vigilancia.py), pasando su
# propia sesión (con el monitor de bloqueos instalado).

TIPOS_MARCA_INVERSO = {"DENOMINATIVA": "D", "MIXTA": "M", "FIGURATIVA": "F", "TRIDIMENSIONAL": "T"}


def _texto_sin_tags(fragmento: str) -> str:
    texto = re.sub(r"<[^>]+>", " ", fragmento)
    texto = _html.unescape(texto).replace("\xa0", " ")
    return re.sub(r"\s+", " ", texto).strip()


def _bloque_seccion(pagina: str, nombre: str) -> str:
    patron = re.compile(
        re.escape(nombre) + r".*?</h4>\s*</div>\s*<div[^>]*>(.*?)</div>\s*</div>\s*</div>", re.S
    )
    matches = patron.findall(pagina)
    return _texto_sin_tags(matches[-1]) if matches else ""


RE_TITULAR_NOMBRE = re.compile(r"NOMBRE\s*:?\s*(?:<span[^>]*>)?\s*([^<%]+?)\s*[\d.,]+\s*%")
_CORTE_TITULAR = re.compile(
    r"\s+(?:DOMICILIO|CODIGO POSTAL|C[ÓO]DIGO POSTAL|PAIS|PA[ÍI]S|TIPO DOC|NUMERO|N[ÚU]MERO|GENERO|G[ÉE]NERO|"
    r"CUIT|EMAIL|PORCENTAJE|ESTADO CIVIL|LOCALIDAD)\b.*$",
    re.S,
)


def _limpiar_titular(valor):
    if not valor:
        return None
    v = _html.unescape(valor).replace("\xa0", " ")
    v = _CORTE_TITULAR.sub("", v)
    v = re.sub(r"\s+", " ", v).strip(" -:;,")
    if not v or re.fullmatch(r"[\d.\- ]+", v):
        return None
    return v[:200]


def _titulares_de_pagina(pagina: str):
    nombres = []
    for m in RE_TITULAR_NOMBRE.finditer(pagina):
        n = _limpiar_titular(m.group(1))
        if n and n not in nombres:
            nombres.append(n)
    return " / ".join(nombres) if nombres else None


def _datos_generales(pagina: str) -> dict:
    out = {"denominacion": None, "tipo": None, "fecha_presentacion": None}
    bloque = _bloque_seccion(pagina, "DATOS GENERALES") or _texto_sin_tags(pagina)
    m = re.search(r"DENOMINACI[ÓO]N\s*:\s*(.*?)\s*TIPO DE MARCA\s*:", bloque, re.I)
    if m:
        v = re.sub(r"\s+", " ", m.group(1)).strip(" -:;,")
        if v and not re.match(r"^(TIPO DE MARCA|CLASE|FECHA|TITULAR|NOMBRE)\b", v, re.I):
            out["denominacion"] = v[:300]
    m = re.search(r"TIPO DE MARCA\s*:\s*(Denominativa|Mixta|Figurativa|Tridimensional)", bloque, re.I)
    if m:
        out["tipo"] = TIPOS_MARCA_INVERSO.get(m.group(1).upper())
    for texto in (bloque, _texto_sin_tags(pagina)):
        m = re.search(r"PRESENTACI[ÓO]N\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", texto, re.I)
        if m:
            out["fecha_presentacion"] = _fecha_ddmmyyyy_a_iso(m.group(1))
            break
    return out


def _agente_de_bloque(bloque_gestion: str) -> dict:
    """AGENTE / matrícula desde GESTION DEL TRAMITE. El texto exacto del campo
    AGENTE no está confirmado para todos los casos (los particulares dicen
    "0 PARTICULAR"); se guarda el texto tal cual y, si empieza con un número
    mayor a 0, ese número como matrícula."""
    texto = _texto_sin_tags(bloque_gestion)
    m = re.search(r"AGENTE\s*:?\s*(.*?)(?=\s+CARACTER\b|\s+[A-ZÁÉÍÓÚÑ]{3,}\s*:|$)", texto)
    agente = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    matricula = ""
    m_mat = re.match(r"^(\d+)\b", agente)
    if m_mat and int(m_mat.group(1)) > 0:
        matricula = m_mat.group(1)
    if not matricula:
        # variantes posibles del texto (no confirmadas): "GOMEZ JUAN (Mat. 1234)", "MATRICULA 1234"
        m_alt = re.search(r"MAT(?:R[IÍ]CULA)?\.?\s*(?:N[°ºo]\.?)?\s*:?\s*(\d{2,7})", agente, re.I)
        if m_alt:
            matricula = m_alt.group(1)
    particular = bool(re.search(r"PARTICULAR", agente, re.I)) or agente in ("", "0")
    return {"agente": agente, "matricula_agente": matricula, "particular": particular}


def _firma_grilla(a: dict) -> str:
    return f"{_parsear_fecha_grilla(a.get('Fecha') or '') or ''}|{(a.get('Indice') or '').strip()}|{(a.get('Referencia') or '').strip()}"


def consultar_expediente(acta: str, s=None, timeout: int = 30, con_grilla: bool = True) -> dict:
    """Ficha completa de un expediente. Devuelve siempre un dict:
      estado_consulta: "ok" | "no_existe" | "bloqueado" | "error"
      y, si "ok": denominacion, tipo, clase, titular, cuit, fecha_presentacion,
      fecha_publicacion, agente, matricula_agente, caracter, particular,
      estado_tramite, fecha_concesion, numero_disposicion,
      fecha_vencimiento_marca, tuvo_oposicion, detalle_oposicion,
      grilla_claves (lista de "fecha|indice|referencia"), movimientos,
      ultimo_movimiento, ultimo_movimiento_fecha.
    La grilla falla por separado (WAF): en ese caso grilla_claves queda None
    y el resto de los datos igual se devuelve."""
    s = s or _crear_sesion()
    out = {"acta": str(acta), "estado_consulta": "error", "error": ""}
    try:
        r = _get_con_reintentos(
            lambda: s.post(
                f"{BASE}/MarcasConsultas/Resultado",
                headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
                data={"acta": str(acta)},
                timeout=timeout,
            )
        )
    except requests.RequestException as e:
        out["error"] = f"error de conexión al consultar INPI: {e}"
        return out
    if "Web Page Blocked" in r.text or "Attack ID" in r.text:
        out["estado_consulta"] = "bloqueado"
        out["error"] = "bloqueado por el WAF de INPI"
        return out
    if "GESTION DEL TRAMITE" not in r.text and "TITULARIDAD" not in r.text:
        out["estado_consulta"] = "no_existe"
        out["error"] = "INPI no tiene un expediente con ese número de acta"
        return out

    texto = r.text
    out["estado_consulta"] = "ok"
    m_gestion = RE_GESTION.search(texto)
    bloque_gestion = m_gestion.group(1) if m_gestion else ""
    m_car = RE_CARACTER_SPAN.search(bloque_gestion or texto)
    out["caracter"] = re.sub(r"\s+", " ", m_car.group(1)).strip() if m_car else ""
    out.update(_agente_de_bloque(bloque_gestion))

    m_cuit = RE_CUIT_SPAN.search(texto)
    cuit = re.sub(r"[^\d]", "", m_cuit.group(1)) if m_cuit else ""
    out["cuit"] = cuit if len(cuit) in (10, 11) else None
    m_clase = RE_CLASE_SPAN.search(texto)
    out["clase"] = int(m_clase.group(1)) if m_clase else None
    out["titular"] = _titulares_de_pagina(texto)
    out.update(_datos_generales(texto))
    out.update(_parsear_resolucion(texto))

    out.update({"fecha_publicacion": None, "tuvo_oposicion": None, "detalle_oposicion": "",
                "grilla_claves": None, "movimientos": None,
                "ultimo_movimiento": None, "ultimo_movimiento_fecha": None})
    if con_grilla:
        archivos = _buscar_archivos_grilla(s, str(acta), timeout=timeout)
        if archivos:
            out["fecha_publicacion"] = _fecha_publicacion_de_archivos(archivos)
            out["tuvo_oposicion"], out["detalle_oposicion"] = _detectar_oposicion(archivos, out["fecha_publicacion"])
            out["grilla_claves"] = [_firma_grilla(a) for a in archivos]
            out["movimientos"] = len(archivos)
            con_fecha = [(_parsear_fecha_grilla(a.get("Fecha") or ""), a) for a in archivos]
            con_fecha = [(f, a) for f, a in con_fecha if f]
            if con_fecha:
                f, a = max(con_fecha, key=lambda x: x[0])
                out["ultimo_movimiento"] = f"{(a.get('Indice') or '').strip()} {(a.get('Referencia') or '').strip()}".strip()
                out["ultimo_movimiento_fecha"] = f
    return out


# ── Oposiciones del expediente (pestaña «Análisis de marca») ──────────────
# Copia acotada de scripts/oposiciones_expediente.py (el panel no importa de
# scripts/, ver el comentario del principio): el expediente trae la tabla
# OPOSICIONES como `opos = JSON.parse('[...]')`, con los campos Oponente,
# Fecha_Presentacion, Fecha_Notificacion, Fecha_Vencimiento, Numero, Agente,
# Caracter, Fecha_Levantamiento, Fundamento, Motivo_Levantamiento. Una fecha
# vacía viene como 01/01/0001. "Agente"/"Caracter" son los del OPONENTE.
_ANIO_MINIMO_OPOSICION = 1990


def _fecha_valida_opo(valor):
    """Fecha ISO o None si viene vacía (01/01/0001) o no se entiende."""
    if not valor or not isinstance(valor, str):
        return None
    iso = _parsear_fecha_grilla(valor)
    if not iso or int(iso[:4]) < _ANIO_MINIMO_OPOSICION:
        return None
    return iso


def _json_de_js(html: str, variable: str):
    """Lee `variable = JSON.parse('...')` de la página del expediente.
    Devuelve (lista | None, hubo_error): None sin error = la tabla viene vacía;
    hubo_error=True = hay datos pero no se pudieron leer."""
    m = re.search(
        r"\b" + re.escape(variable) + r"\s*=\s*JSON\.parse\('((?:[^'\\]|\\.)*)'\)",
        html, re.S,
    )
    if not m:
        return None, False
    crudo = m.group(1).strip()
    if not crudo:
        return None, False
    try:
        import json as _json

        datos = _json.loads(crudo.replace("\\'", "'"))
    except ValueError:
        return None, True
    return (datos if isinstance(datos, list) else None), False


FUNDAMENTO_MAX = 8000


def _texto_libre(valor, maximo: int = FUNDAMENTO_MAX):
    """Texto escrito por una persona (fundamento de una oposición): sin espacios de más
    y con un tope de largo. None si viene vacío."""
    t = re.sub(r"[ \t\r\f\v]+", " ", str(valor or "")).strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    # INPI suele pegar las oraciones («solicitante.Invocamos»): se les devuelve el espacio.
    t = re.sub(r"(?<=[a-záéíóúñ)\]])\.(?=[A-ZÁÉÍÓÚÑ])", ". ", t)
    if not t:
        return None
    return t if len(t) <= maximo else t[:maximo].rstrip() + "…"


def parsear_oposiciones(html: str) -> dict:
    """Las oposiciones de la página del expediente, ya normalizadas:
    {"items": [{oponente, numero, presentacion, notificacion, vencimiento,
    levantamiento, agente_oponente, fundamento}], "error_lectura": bool}."""
    crudas, error = _json_de_js(html, "opos")
    items = []
    for o in crudas or []:
        if not isinstance(o, dict):
            continue
        # Agente 0 / vacío = el oponente se presentó sin agente matriculado.
        agente = o.get("Agente")
        agente = None if agente in (None, "", 0, "0") else agente
        caracter = (o.get("Caracter") or "").strip()
        agente_txt = (f"{agente} ({caracter})" if agente and caracter
                      else str(agente) if agente else None)
        items.append({
            "oponente": " ".join(str(o.get("Oponente") or "").split()) or None,
            "numero": o.get("Numero"),
            "presentacion": _fecha_valida_opo(o.get("Fecha_Presentacion")),
            "notificacion": _fecha_valida_opo(o.get("Fecha_Notificacion")),
            "vencimiento": _fecha_valida_opo(o.get("Fecha_Vencimiento")),
            "levantamiento": _fecha_valida_opo(o.get("Fecha_Levantamiento")),
            "agente_oponente": agente_txt,
            # Lo que alega el oponente (texto libre, a veces largo).
            "fundamento": _texto_libre(o.get("Fundamento")),
        })
    items.sort(key=lambda x: x["presentacion"] or "")
    return {"items": items, "error_lectura": error}


# Datos de la marca que figuran en el expediente (cada uno es un <label class="input">
# con el texto «ETIQUETA: valor»). La PUBLICACIÓN es un bloque aparte (#collapse-six)
# con FECHA / NÚMERO (link al PDF del boletín) / TIPO.
_URL_BOLETIN_OK = "https://portaltramites.inpi.gob.ar/"


def _etiquetas_expediente(nodo) -> list:
    """[(ETIQUETA en mayúsculas sin tildes, valor, <a> o None)] de los labels de un bloque."""
    import unicodedata

    out = []
    for lab in nodo.select("label.input"):
        txt = " ".join(lab.get_text(" ", strip=True).split())
        if ":" not in txt:
            continue
        clave, valor = txt.split(":", 1)
        clave = "".join(c for c in unicodedata.normalize("NFD", clave) if unicodedata.category(c) != "Mn").upper().strip()
        out.append((clave, valor.strip(), lab.find("a")))
    return out


def parsear_datos_expediente(html: str) -> dict:
    """Lo que el expediente informa de la marca: {denominacion, tipo_marca, clase, proteccion,
    limitacion, publicaciones: [{fecha (ISO), numero, url, tipo}]}. Lo que no figura queda vacío."""
    soup = BeautifulSoup(html or "", "html.parser")
    out = {"denominacion": None, "tipo_marca": None, "clase": None, "proteccion": None, "limitacion": None,
           "publicaciones": []}
    for clave, valor, _ in _etiquetas_expediente(soup):
        if clave == "DENOMINACION" and not out["denominacion"]:
            out["denominacion"] = valor or None
        elif clave == "TIPO DE MARCA" and not out["tipo_marca"]:
            out["tipo_marca"] = valor or None
        elif clave == "CLASE" and not out["clase"]:
            m_cl = re.match(r"\d{1,2}", valor)
            out["clase"] = m_cl.group(0) if m_cl else None
        elif clave == "PROTECCION" and not out["proteccion"]:
            out["proteccion"] = valor or None
        elif clave == "LIMITACION" and not out["limitacion"]:
            # «A ;B ;C ;» -> «A; B; C»
            partes = [p.strip() for p in valor.split(";") if p.strip()]
            out["limitacion"] = "; ".join(partes) or None
    bloque = soup.find(id="collapse-six")
    actual = None
    for clave, valor, a in _etiquetas_expediente(bloque) if bloque else []:
        if clave == "FECHA":
            actual = {"fecha": None, "numero": None, "url": None, "tipo": None}
            out["publicaciones"].append(actual)
            m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", valor)
            actual["fecha"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None
        elif actual is not None and clave in ("NUMERO", "TIPO"):
            if clave == "NUMERO":
                actual["numero"] = valor or None
                href = (a.get("href") or "").strip() if a is not None else ""
                actual["url"] = href if href.startswith(_URL_BOLETIN_OK) else None
            else:
                actual["tipo"] = valor or None
    out["publicaciones"] = [p for p in out["publicaciones"] if p["fecha"] or p["numero"]]
    return out


# ── Marca del oponente ────────────────────────────────────────────────────
# El fundamento de una oposición suele citar la marca propia del oponente como
# «Acta N° X» (copia acotada de scripts/validar_leads.py, que el panel no puede
# importar). Si cita varias actas se toman las primeras; la propia marca (a veces se
# la repite entre paréntesis) se descarta.
RE_ACTA_CITADA = re.compile(r"ACTAS?\s+N[°ºo]\.?\s*(\d{5,8})", re.IGNORECASE)
RE_GRUPO_ACTAS = re.compile(
    r"ACTAS?\s+N[°ºo]\.?\s*\d{5,8}\s*\(clase[^)]*\)(?:\s*[,y]\s*\d{5,8}\s*\(clase[^)]*\))*",
    re.IGNORECASE,
)
RE_NUMERO_EN_GRUPO_ACTAS = re.compile(r"(\d{5,8})\s*\(clase", re.IGNORECASE)
RE_ACTA_REFERENCIA = re.compile(
    r"actas?\s+de\s+referencia\s*(\d{5,8}(?:\s*[,y]\s*\d{5,8})*)", re.IGNORECASE
)
RE_MARCA_OPONENTE_COMILLAS = re.compile(r'["“]([^"”]{2,60})["”]\s*Nro\.?\s*([\d.]{4,})')
RE_MARCA_OPONENTE_REG = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9]*(?:\s+[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9]*){0,4})\s+Reg\.?\s*Nr\.?\s*([\d.]{4,})"
)
RE_MARCA_OPONENTE_REGISTROS = re.compile(
    r'["“]([^"”]{2,60})["”][^"0-9]{0,40}?Registro?s?\.?\s*N?[°ºo]?\.?\s*([\d.]{4,})',
    re.IGNORECASE,
)
MAX_ACTAS_OPUESTAS_POR_OPOSICION = 3


def marcas_opuestas_citadas(fundamento, acta_propia=None) -> dict:
    """{"actas": [acta, ...], "citada": "ALIGN (Registro N° 123)" | None} a partir del
    fundamento. «actas» son las que el fundamento cita con su número de acta (se pueden
    abrir en INPI); «citada» solo se llena cuando no hay actas y el fundamento da el
    nombre y número de registro de la marca (no hay expediente que abrir)."""
    out = {"actas": [], "citada": None}
    if not fundamento:
        return out
    propia = str(acta_propia or "")
    actas = []

    def sumar(a):
        if a != propia and a not in actas:
            actas.append(a)

    for m in RE_ACTA_CITADA.finditer(fundamento):
        sumar(m.group(1))
    for grupo in RE_GRUPO_ACTAS.finditer(fundamento):
        for m in RE_NUMERO_EN_GRUPO_ACTAS.finditer(grupo.group(0)):
            sumar(m.group(1))
    m_ref = RE_ACTA_REFERENCIA.search(fundamento)
    if m_ref:
        for a in re.findall(r"\d{5,8}", m_ref.group(1)):
            sumar(a)
    if actas:
        out["actas"] = actas[:MAX_ACTAS_OPUESTAS_POR_OPOSICION]
        return out
    m = (RE_MARCA_OPONENTE_COMILLAS.search(fundamento) or RE_MARCA_OPONENTE_REG.search(fundamento)
         or RE_MARCA_OPONENTE_REGISTROS.search(fundamento))
    if m:
        out["citada"] = f"{m.group(1).strip()} (Registro N° {m.group(2).replace('.', '')})"
    return out


def parsear_marca_opuesta(html_expediente: str) -> dict:
    """Los datos de la marca del oponente que se muestran: denominación, tipo de marca,
    clase, protección, limitación, y el agente de su expediente (texto tal cual, carácter
    y si es particular)."""
    datos = parsear_datos_expediente(html_expediente)
    m_gestion = RE_GESTION.search(html_expediente or "")
    bloque = m_gestion.group(1) if m_gestion else ""
    m_car = RE_CARACTER_SPAN.search(bloque or "")
    ag = _agente_de_bloque(bloque)
    return {
        "denominacion": datos["denominacion"], "tipo_marca": datos["tipo_marca"], "clase": datos["clase"],
        "proteccion": datos["proteccion"], "limitacion": datos["limitacion"],
        "agente": ag["agente"] or None, "matricula_agente": ag["matricula_agente"] or None,
        "particular": bool(ag["particular"]),
        "caracter": re.sub(r"\s+", " ", m_car.group(1)).strip() if m_car else None,
    }


def consultar_marca_opuesta(acta: str, s=None, timeout: int = 30) -> dict:
    """Expediente de la marca del oponente (UN pedido a INPI). estado_consulta: "ok" |
    "no_existe" | "bloqueado" | "error"; si "ok": `datos` (ver parsear_marca_opuesta) y
    `logo` ((bytes, mime) o None)."""
    s = s or _crear_sesion()
    out = {"acta": str(acta), "estado_consulta": "error", "error": "", "datos": None, "logo": None}
    try:
        r = _get_con_reintentos(
            lambda: s.post(
                f"{BASE}/MarcasConsultas/Resultado",
                headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
                data={"acta": str(acta)},
                timeout=timeout,
            )
        )
    except requests.RequestException as e:
        out["error"] = f"error de conexión al consultar INPI: {e}"
        return out
    if "Web Page Blocked" in r.text or "Attack ID" in r.text:
        out["estado_consulta"] = "bloqueado"
        out["error"] = "INPI bloqueó la consulta (WAF)"
        return out
    if "GESTION DEL TRAMITE" not in r.text and "TITULARIDAD" not in r.text:
        out["estado_consulta"] = "no_existe"
        out["error"] = "INPI no tiene un expediente con ese número de acta"
        return out
    out["datos"] = parsear_marca_opuesta(r.text)
    out["logo"] = extraer_logo(r.text)
    out["estado_consulta"] = "ok"
    return out


# El expediente de INPI trae el logo de la marca embebido en la página:
# <div id="logo" ...><a id="logo"><img src="data:image/jpg;base64,..."></a></div>.
# Las marcas sin logo (denominativas) no tienen ese bloque.
_RE_LOGO = re.compile(
    r"""<div[^>]*\bid=["']logo["'][^>]*>.{0,400}?<img[^>]*\bsrc=["']data:image/(?P<tipo>[A-Za-z0-9.+-]+);base64,(?P<b64>[A-Za-z0-9+/=\s]+)["']""",
    re.S | re.I,
)
_MIME_LOGO = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
LOGO_MAX_BYTES = 5_000_000


def extraer_logo(html_expediente: str):
    """(bytes, mime) del logo del expediente, o None si la marca no tiene o no se
    puede leer. No valida que sea una imagen sana: eso lo hace quien la guarda."""
    m = _RE_LOGO.search(html_expediente or "")
    if not m:
        return None
    mime = _MIME_LOGO.get(m.group("tipo").lower())
    if not mime:
        return None
    b64 = re.sub(r"\s+", "", m.group("b64"))
    if len(b64) > LOGO_MAX_BYTES * 4 // 3:
        return None
    try:
        datos = base64.b64decode(b64, validate=True)
    except ValueError:
        return None
    return (datos, mime) if datos else None


def consultar_oposiciones(acta: str, s=None, timeout: int = 30) -> dict:
    """Consulta el expediente y devuelve todas las oposiciones que figuran.
    estado_consulta: "ok" | "no_existe" | "bloqueado" | "error" (con `error`
    explicando). Hace UN pedido a INPI (el del expediente), del que salen también las
    oposiciones y el logo de la marca (`logo`: (bytes, mime) o None)."""
    s = s or _crear_sesion()
    out = {"acta": str(acta), "estado_consulta": "error", "error": "", "items": [], "error_lectura": False,
           "logo": None, "expediente": None}
    try:
        r = _get_con_reintentos(
            lambda: s.post(
                f"{BASE}/MarcasConsultas/Resultado",
                headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
                data={"acta": str(acta)},
                timeout=timeout,
            )
        )
    except requests.RequestException as e:
        out["error"] = f"error de conexión al consultar INPI: {e}"
        return out
    if "Web Page Blocked" in r.text or "Attack ID" in r.text:
        out["estado_consulta"] = "bloqueado"
        out["error"] = "INPI bloqueó la consulta (WAF)"
        return out
    if "GESTION DEL TRAMITE" not in r.text and "TITULARIDAD" not in r.text:
        out["estado_consulta"] = "no_existe"
        out["error"] = "INPI no tiene un expediente con ese número de acta"
        return out
    res = parsear_oposiciones(r.text)
    out.update(res)
    out["logo"] = extraer_logo(r.text)   # (bytes, mime) o None si la marca no tiene logo
    out["expediente"] = parsear_datos_expediente(r.text)
    if res["error_lectura"]:
        out["error"] = "INPI tiene oposiciones cargadas pero no se pudieron leer"
        return out
    out["estado_consulta"] = "ok"
    return out


# Webservice SOAP público (ver references/inpi-webservice.md): marcas de un
# titular por CUIT o por nombre. No trae agente ni estado.
WS_URL = "https://ws.inpi.gob.ar/wsinpi.asmx"


def consultar_titular_ws(cuit: str = "", titular: str = "", timeout: int = 60, reintentos: int = 2) -> list:
    """[{Acta, Titulares, Fecha_Ingreso, Clase, Denominacion, Tipo_Marca}, ...].
    Lista vacía si no hay resultados o el servicio no respondió."""
    cuerpo = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        '<soap:Body><ConsultaCuitOTitular xmlns="http://tempuri.org/">'
        f"<cuit>{_xml_escape(cuit)}</cuit><titular>{_xml_escape(titular)}</titular>"
        "</ConsultaCuitOTitular></soap:Body></soap:Envelope>"
    )
    for _ in range(reintentos + 1):
        try:
            r = requests.post(
                WS_URL, data=cuerpo.encode("utf-8"), timeout=timeout,
                headers={"Content-Type": "text/xml; charset=utf-8",
                         "SOAPAction": '"http://tempuri.org/ConsultaCuitOTitular"'},
            )
            root = ET.fromstring(r.text)
        except (requests.RequestException, ET.ParseError):
            continue
        filas = []
        for g in root.iter("{http://tempuri.org/}GrillaMarcas"):
            filas.append({c.tag.split("}")[-1]: (c.text or "") for c in g})
        return filas
    return []
