"""
Paso 5 — Para las marcas con matrícula "Part." o vacía, entra al expediente
completo en el portal de trámites de INPI, lee CARACTER (GESTION DEL TRAMITE) y,
si está vacío (lead real), abre GRILLA DIGITAL y descarga el Formulario para
sacarle el email.

Reescrito para usar `requests` puro (sin navegador). El WAF de INPI bloquea
las visitas hechas con Chromium/Playwright headless (respuesta "Web Page
Blocked! Attack ID: 20000051"), pero no bloquea peticiones HTTP simples con
`requests` — probablemente por fingerprinting del navegador headless. Además,
el dato de CARACTER no está en la página `Resultado?acta=X` (GET, la que
aparece en el CSV) ni en `Grilla?acta=X`: hay que hacer un POST a
`/MarcasConsultas/Resultado` con `acta` en el body (replicando el form
`frmGD` que usa el propio sitio), que devuelve la página completa con la
sección "GESTION DEL TRAMITE".

Flujo real (confirmado a mano, ver docs/proceso-original.md):
  1. GET  /MarcasConsultas/Grilla                     (cookies de sesión)
  2. POST /MarcasConsultas/Resultado  {acta}           -> HTML con GESTION DEL TRAMITE
     - Si no aparece ningún campo AGENTE/CARACTER en esa sección: lead real.
     - Si aparece CARACTER: <valor> (ej. "Apoderado", "Gestor Ratificado"): no es lead.
  3. Si es lead, para sacar el email:
     POST /Home/GrillaDigital       {fname: "1-{acta}"}
     POST /Home/GrillaDigitales     {acta, limit, offset, direccion:1}  -> JSON de archivos
     buscar el archivo con Indice == "Formulario"
     GET  /Home/edmsxidd?id={id_Documento_encriptado}&nombre={archivo}  -> PDF
     leer el PDF con pdfplumber y buscar "EMAIL:" por regex

Uso:
    python3 validar_leads.py --in 11121_completo.csv --out 11121_leads.csv --limit 20

Nota: si INPI cambia el HTML o los endpoints, ajustar las constantes/regex de
abajo (quedan comentados los puntos exactos revisados el 2026-09-25).
"""

import argparse
import csv
import re
import sys
import time

import requests

BASE = "https://portaltramites.inpi.gob.ar"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
}

# Dentro del bloque "GESTION DEL TRAMITE", el campo CARACTER aparece como:
#   <label class="input">CARACTER:<span class="text-danger"> Apoderado </span></label>
RE_GESTION = re.compile(r"GESTION DEL TRAMITE.*?</h4>\s*</div>\s*<div[^>]*>(.*?)</div>\s*</div>\s*</div>", re.S)
RE_CARACTER = re.compile(r"CARACTER\s*:?\s*</label>|CARACTER\s*:", re.S)
RE_CARACTER_SPAN = re.compile(r"CARACTER\s*:?\s*<span[^>]*>(.*?)</span>", re.S)


def crear_sesion() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(f"{BASE}/MarcasConsultas/Grilla", timeout=30)
    except requests.RequestException as e:
        print(f"  aviso: no se pudo pre-cargar sesión ({e})", file=sys.stderr)
    return s


def _get_con_reintentos(fn, intentos: int = 3, espera: int = 3):
    ultimo_error = None
    for i in range(intentos):
        try:
            return fn()
        except requests.RequestException as e:
            ultimo_error = e
            time.sleep(espera)
    raise ultimo_error


def revisar_acta(s: requests.Session, acta: str, timeout: int = 30) -> dict:
    """Devuelve {caracter, es_lead, email, email_apoderado, motivo_sin_email}.
    Si algo falla, devuelve es_lead=None para marcarlo como "no se pudo
    verificar" (en vez de asumir por defecto que es lead). motivo_sin_email
    queda vacío cuando sí hay email o cuando no aplica (tiene apoderado)."""
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
            print(f"  acta {acta}: bloqueado por el WAF de INPI (no se pudo verificar)", file=sys.stderr)
            resultado["motivo_sin_email"] = "bloqueado por el WAF de INPI al consultar el expediente"
            return resultado

        m_gestion = RE_GESTION.search(r.text)
        bloque_gestion = m_gestion.group(1) if m_gestion else r.text  # fallback: buscar en toda la página

        m_caracter = RE_CARACTER_SPAN.search(bloque_gestion)
        if m_caracter:
            caracter = re.sub(r"\s+", " ", m_caracter.group(1)).strip()
        else:
            caracter = ""

        resultado["caracter"] = caracter
        resultado["es_lead"] = caracter == ""

        if not resultado["es_lead"]:
            return resultado  # ya tiene apoderado/gestor, no hace falta el email

        # GRILLA DIGITAL -> listar archivos -> descargar "Formulario" -> leer EMAIL
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

        import io
        import pdfplumber

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
        print(f"  acta {acta}: error ({e})", file=sys.stderr)
        resultado["caracter"] = None
        resultado["es_lead"] = None
        resultado["motivo_sin_email"] = f"error de conexión al consultar INPI: {e}"
    return resultado


def calcular_lead_score(row: dict) -> int:
    """Puntaje simple: suma puntos si no tiene apoderado/matrícula, resta si sí.
    Si no se pudo verificar (es_lead is None), no se suma ni resta nada por
    ese concepto: mejor subestimar el score que arriesgar un falso positivo."""
    score = 0
    matricula = (row.get("matricula_agente") or "").strip()
    caracter = row.get("caracter")
    es_lead = row.get("es_lead")
    if matricula == "" or matricula == "Part.":
        score += 50
    if es_lead is True:
        score += 50
    elif es_lead is False:
        score -= 100
    # es_lead is None (no verificado): no se suma ni resta
    if row.get("email"):
        score += 20
    return score


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--limit", type=int, default=None,
        help="revisar como máximo N actas candidatas (matrícula vacía o 'Part.'); útil para pruebas",
    )
    ap.add_argument("--delay", type=float, default=1.5, help="segundos de espera entre actas (freno de mano)")
    args = ap.parse_args()

    with open(args.in_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    candidatas = [r for r in rows if (r.get("matricula_agente") or "").strip() in ("", "Part.")]
    if args.limit:
        candidatas = candidatas[: args.limit]
    print(f"Actas candidatas a revisar (sin matrícula o 'Part.'): {len(candidatas)}")

    s = crear_sesion()
    for i, row in enumerate(candidatas, 1):
        info = revisar_acta(s, row["acta"])
        row.update(info)
        row["lead_score"] = calcular_lead_score(row)
        print(f"  [{i}/{len(candidatas)}] acta {row['acta']}: caracter={info['caracter']!r} "
              f"es_lead={info['es_lead']} email={'sí' if info['email'] else 'no'}")
        time.sleep(args.delay)  # freno de mano, no golpear el portal

    for row in rows:
        row.setdefault("caracter", "")
        row.setdefault("es_lead", "")
        row.setdefault("email", "")
        row.setdefault("email_apoderado", "")
        row.setdefault("motivo_sin_email", "")
        row.setdefault("lead_score", row.get("lead_score", 0))

    fieldnames = list(rows[0].keys())
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    leads = sum(1 for r in rows if r.get("es_lead") is True or r.get("es_lead") == "True")
    no_verificados = sum(1 for r in rows if r.get("es_lead") is None)
    print(f"\nLeads confirmados: {leads}. No verificados (bloqueo/error): {no_verificados}. Guardado: {args.out}")


if __name__ == "__main__":
    main()
