"""
Paso 5 — Para las marcas con matrícula "Part." o vacía, entra al expediente
completo en el portal de trámites de INPI, lee CARACTER (GESTION DEL TRAMITE) y,
si está vacío (lead real), abre GRILLA DIGITAL y descarga el Formulario para
sacarle el email.

Usa Playwright (no Apify, sin costo extra más allá del cómputo). Pensado para
correr dentro del mismo GitHub Action, ~10-15s por marca revisada.

Uso:
    python3 validar_leads.py --in 11121_completo.csv --out 11121_leads.csv --limit 20

Nota: este script asume la estructura de la página que se relevó a mano (ver
SKILL.md, Paso 4 y 5). Si INPI cambia el HTML del portal, los selectores de abajo
van a necesitar un ajuste — quedan comentados los puntos exactos a revisar.
"""

import argparse
import csv
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "https://portaltramites.inpi.gob.ar"


def revisar_acta(page, acta: str, timeout_ms: int = 30000) -> dict:
    """Devuelve {caracter, es_lead, email, email_apoderado}. Si algo falla,
    devuelve caracter=None para marcarlo como "no se pudo verificar"."""
    resultado = {"caracter": None, "es_lead": None, "email": "", "email_apoderado": ""}
    try:
        # Buscar por acta en Consultas de Marcas -> Seguimiento de Trámite
        page.goto(f"{BASE}/MarcasConsultas/Grilla?acta={acta}", timeout=timeout_ms)

        # Expandir el resultado (botón "+") para llegar a la página Resultado completa
        boton_expandir = page.locator("text=+").first
        if boton_expandir.count() > 0:
            boton_expandir.click(timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)

        # Sección "GESTION DEL TRAMITE": campos AGENTE y CARACTER
        # AJUSTAR SELECTOR si INPI cambia el markup: buscamos la celda que sigue
        # a la etiqueta "CARACTER" dentro de esa sección.
        caracter_locator = page.locator("text=CARACTER").locator("xpath=following::td[1]")
        caracter = caracter_locator.inner_text(timeout=timeout_ms).strip() if caracter_locator.count() > 0 else ""
        resultado["caracter"] = caracter
        resultado["es_lead"] = caracter == ""

        if not resultado["es_lead"]:
            return resultado  # ya tiene apoderado/gestor, no hace falta el email

        # GRILLA DIGITAL -> descargar "Formulario" -> leer EMAIL
        grilla_btn = page.locator("text=GRILLA DIGITAL").first
        if grilla_btn.count() == 0:
            return resultado
        grilla_btn.click(timeout=timeout_ms)
        page.wait_for_load_state("networkidle", timeout=timeout_ms)

        fila_formulario = page.locator("tr", has_text="Formulario").first
        if fila_formulario.count() == 0:
            return resultado

        with page.expect_download(timeout=timeout_ms) as download_info:
            fila_formulario.locator("text=descargar").click(timeout=timeout_ms)
        download = download_info.value
        pdf_path = download.path()

        # Extraer texto del PDF del formulario
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            texto = "\n".join(p.extract_text() or "" for p in pdf.pages)

        m_email = re.search(r"EMAIL:\s*([\w.+-]+@[\w-]+\.[\w.-]+)", texto)
        if m_email:
            resultado["email"] = m_email.group(1)

        m_email_apoderado = re.search(
            r"REPRESENTACION.*?EMAIL:\s*([\w.+-]+@[\w-]+\.[\w.-]+)", texto, re.S
        )
        if m_email_apoderado:
            resultado["email_apoderado"] = m_email_apoderado.group(1)

    except Exception as e:
        print(f"  acta {acta}: error ({e})", file=sys.stderr)
    return resultado


def calcular_lead_score(row: dict) -> int:
    """Puntaje simple: suma puntos si no tiene apoderado/matrícula, resta si sí."""
    score = 0
    matricula = (row.get("matricula_agente") or "").strip()
    caracter = row.get("caracter")
    if matricula == "" or matricula == "Part.":
        score += 50
    if caracter == "":
        score += 50
    elif caracter:  # Apoderado, Gestor Ratificado, etc.
        score -= 100
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
    ap.add_argument("--headless", action="store_true", default=True)
    args = ap.parse_args()

    with open(args.in_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    candidatas = [r for r in rows if (r.get("matricula_agente") or "").strip() in ("", "Part.")]
    if args.limit:
        candidatas = candidatas[: args.limit]
    print(f"Actas candidatas a revisar (sin matrícula o 'Part.'): {len(candidatas)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()
        for i, row in enumerate(candidatas, 1):
            info = revisar_acta(page, row["acta"])
            row.update(info)
            row["lead_score"] = calcular_lead_score(row)
            print(f"  [{i}/{len(candidatas)}] acta {row['acta']}: caracter={info['caracter']!r} "
                  f"es_lead={info['es_lead']} email={'sí' if info['email'] else 'no'}")
            time.sleep(1)  # freno de mano, no golpear el portal
        browser.close()

    for row in rows:
        row.setdefault("caracter", "")
        row.setdefault("es_lead", "")
        row.setdefault("email", "")
        row.setdefault("email_apoderado", "")
        row.setdefault("lead_score", row.get("lead_score", 0))

    fieldnames = list(rows[0].keys())
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    leads = sum(1 for r in rows if r.get("es_lead") is True or r.get("es_lead") == "True")
    print(f"\nLeads confirmados: {leads}. Guardado: {args.out}")


if __name__ == "__main__":
    main()
