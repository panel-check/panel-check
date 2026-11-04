"""
Paso 1 — Listar boletines de "MARCAS NUEVAS" publicados por INPI.

No requiere Apify: la página es HTML plano.

Uso:
    python3 listar_boletines.py                          # últimos boletines (GET simple)
    python3 listar_boletines.py --start 2026-01-01 --finish 2026-09-24   # rango de fechas (POST)
    python3 listar_boletines.py --start 2026-01-01 --finish 2026-09-24 --out boletines.json

Salida: JSON en stdout (o en --out) con lista de {numero, fecha, pdf_url}.
"""

import argparse
import json
import re
import sys

import requests
from bs4 import BeautifulSoup

BASE = "https://portaltramites.inpi.gob.ar"
LISTADO_URL = f"{BASE}/Boletines?Tipo_Item=3"
LISTADO_POST_URL = f"{BASE}/Boletines/Index"


def _es_boletin_marcas_nuevas(celdas: list, fila_txt: str) -> bool:
    """¿La fila es un boletín de marcas nuevas? INPI lo marca con «MARCAS NUEVAS» en la
    columna Comentario, pero a veces publica el boletín con el comentario vacío (pasó con
    los 11144 a 11147 del 04/11/2026). Se acepta también la fila sin comentario si es del
    tipo «Boletines» y del sector «Marcas» (los Anexos y los de otros sectores no entran).
    Columnas: Número | Tipo | Sector | Fecha | Archivo | Comentario."""
    if "MARCAS NUEVAS" in fila_txt.upper():
        return True
    if len(celdas) < 4:
        return False
    tipo, sector = celdas[1].strip().lower(), celdas[2].strip().lower()
    comentario = celdas[5].strip() if len(celdas) > 5 else ""
    return tipo == "boletines" and sector == "marcas" and comentario == ""


def _parse_tabla(html: str):
    soup = BeautifulSoup(html, "html.parser")
    boletines = []
    for row in soup.select("table tr"):
        celdas = [c.get_text(strip=True) for c in row.find_all("td")]
        if not celdas:
            continue
        fila_txt = " | ".join(celdas)
        if "Boletines" not in fila_txt:
            continue
        if not _es_boletin_marcas_nuevas(celdas, fila_txt):
            continue
        m = re.search(r"\b(\d{4,6})\b", fila_txt)
        if not m:
            continue
        numero = m.group(1)
        fecha_m = re.search(r"\d{1,2}/\d{1,2}/\d{4}", fila_txt)
        boletines.append(
            {
                "numero": numero,
                "fecha": fecha_m.group(0) if fecha_m else None,
                "pdf_url": f"{BASE}/Uploads/Boletines/{numero}_3_.pdf",
                "comentario": fila_txt,
            }
        )
    return boletines


def listar(start: str | None, finish: str | None, timeout: int = 60, reintentos: int = 3):
    for intento in range(reintentos):
        try:
            if start and finish:
                resp = requests.post(
                    LISTADO_POST_URL,
                    data={
                        "Tipo_Item": "3",
                        "Tipo_Boletin": "",
                        "start": start,
                        "finish": finish,
                        "numero": "",
                    },
                    timeout=timeout,
                )
            else:
                resp = requests.get(LISTADO_URL, timeout=timeout)
            resp.raise_for_status()
            return _parse_tabla(resp.text)
        except requests.RequestException as e:
            if intento == reintentos - 1:
                raise
            print(f"  intento {intento + 1} falló ({e}), reintentando...", file=sys.stderr)
    return []


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", help="fecha desde, YYYY-MM-DD")
    ap.add_argument("--finish", help="fecha hasta, YYYY-MM-DD")
    ap.add_argument("--out", help="archivo de salida (JSON). Si se omite, imprime a stdout")
    args = ap.parse_args()

    boletines = listar(args.start, args.finish)
    print(f"Encontrados {len(boletines)} boletines de MARCAS NUEVAS", file=sys.stderr)

    out_data = json.dumps(boletines, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_data)
        print(f"Guardado en {args.out}", file=sys.stderr)
    else:
        print(out_data)


if __name__ == "__main__":
    main()
