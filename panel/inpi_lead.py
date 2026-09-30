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

import io
import re
import sys
import time

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
