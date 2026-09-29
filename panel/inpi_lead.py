"""
Copia acotada de la lógica de scripts/validar_leads.py, para que el botón
"Reintentar" del panel pueda volver a consultar un acta puntual contra INPI
sin depender del resto del repo (Railway deploya este servicio con root
directory = panel/, así que no tiene acceso a scripts/).

Si INPI cambia el HTML o los endpoints, hay que actualizar este archivo Y
scripts/validar_leads.py — quedan separados a propósito, no importan uno
del otro.
"""

import io
import re
import sys
import time

import pdfplumber
import requests

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


def revisar_acta(acta: str, timeout: int = 30) -> dict:
    """Igual que validar_leads.revisar_acta: devuelve
    {caracter, es_lead, email, email_apoderado, motivo_sin_email} y, cuando
    se pudo leer de la sección TITULARIDAD, también "cuit" (clave real para
    unificar leads del mismo titular, mejor que comparar texto)."""
    s = _crear_sesion()
    resultado = {
        "caracter": None, "es_lead": None, "email": "", "email_apoderado": "",
        "motivo_sin_email": "",
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

        if not resultado["es_lead"]:
            return resultado

        r_gd = _get_con_reintentos(
            lambda: s.post(
                f"{BASE}/Home/GrillaDigital",
                headers={"Referer": f"{BASE}/MarcasConsultas/Resultado"},
                data={"fname": f"1-{acta}"},
                timeout=timeout,
            )
        )
        if "Web Page Blocked" in r_gd.text or "Attack ID" in r_gd.text:
            resultado["motivo_sin_email"] = "bloqueado por el WAF de INPI al abrir Grilla Digital"
            return resultado

        r_archivos = _get_con_reintentos(
            lambda: s.post(
                f"{BASE}/Home/GrillaDigitales",
                headers={"Referer": f"{BASE}/Home/GrillaDigital", "X-Requested-With": "XMLHttpRequest"},
                data={"acta": acta, "limit": 50, "offset": 0, "direccion": 1},
                timeout=timeout,
            )
        )
        try:
            archivos = r_archivos.json().get("rows", [])
        except ValueError:
            archivos = []

        formulario = next((a for a in archivos if a.get("Indice") == "Formulario"), None)
        if not formulario:
            resultado["motivo_sin_email"] = "el expediente no tiene un archivo Formulario en Grilla Digital"
            return resultado

        id_doc = formulario["id_Documento_encriptado"]
        nombre_archivo = formulario["ruta"].rsplit("/", 1)[-1]
        r_pdf = _get_con_reintentos(
            lambda: s.get(
                f"{BASE}/Home/edmsxidd",
                params={"id": id_doc, "nombre": nombre_archivo},
                headers={"Referer": f"{BASE}/Home/GrillaDigital"},
                timeout=timeout,
            )
        )
        if r_pdf.headers.get("Content-Type", "").lower() != "application/pdf":
            resultado["motivo_sin_email"] = "no se pudo descargar el PDF del Formulario"
            return resultado

        with pdfplumber.open(io.BytesIO(r_pdf.content)) as pdf:
            texto = "\n".join(p.extract_text() or "" for p in pdf.pages)

        m_email = re.search(r"EMAIL:\s*([\w.+-]+@[\w-]+\.[\w.-]+)", texto)
        if m_email:
            resultado["email"] = m_email.group(1)
        else:
            resultado["motivo_sin_email"] = "el Formulario no tiene el campo EMAIL completo"

        m_email_apoderado = re.search(
            r"REPRESENTACION.*?EMAIL:\s*([\w.+-]+@[\w-]+\.[\w.-]+)", texto, re.S
        )
        if m_email_apoderado:
            resultado["email_apoderado"] = m_email_apoderado.group(1)

    except Exception as e:
        resultado["caracter"] = None
        resultado["es_lead"] = None
        resultado["motivo_sin_email"] = f"error de conexión al consultar INPI: {e}"
    return resultado


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
