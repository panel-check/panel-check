"""
Extracción de texto de un boletín SIN Apify: baja el PDF directo de INPI y lo
lee en el runner de GitHub Actions.

Existe porque el actor de Apify (automation-lab/pdf-text-extractor) tiene un
tope FIJO de 300 s por PDF (timeoutPerPdfSecs <= 300, no se puede subir) y
los boletines grandes (34-40 MB, ~120+ páginas, ej. 11126/11127/11128 del
30/09/2026) tardan eso o más -- casi todo es la descarga desde INPI hacia
los servidores de Apify. Cuando se pasa, Apify devuelve el item sin
fullText y el boletín quedaba sin procesar (Pipeline #27).

Motores disponibles (se elige con --motor o con motor=... en extraer()):
  * pdfplumber (ya estaba en requirements)
  * pypdf

Uso:
    python3 extraer_pdf_local.py --numero 11128 --out 11128_text.txt [--motor pypdf]
"""

import argparse
import io
import sys
import time

import requests

BASE = "https://portaltramites.inpi.gob.ar"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def bajar_pdf(url: str, intentos: int = 3, timeout: int = 300) -> bytes:
    ultimo = None
    for i in range(intentos):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            if not r.content.startswith(b"%PDF"):
                raise ValueError(f"la respuesta no es un PDF (Content-Type {r.headers.get('Content-Type')})")
            return r.content
        except (requests.RequestException, ValueError) as e:
            ultimo = e
            print(f"  descarga intento {i + 1} falló ({e})", file=sys.stderr)
            time.sleep(5)
    raise RuntimeError(f"no se pudo bajar {url}: {ultimo}")


def texto_pdfplumber(pdf_bytes: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def texto_pypdf(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


MOTORES = {"pdfplumber": texto_pdfplumber, "pypdf": texto_pypdf}
MOTOR_DEFAULT = "pdfplumber"


def extraer(url: str, motor: str = MOTOR_DEFAULT, pdf_bytes: bytes | None = None) -> str:
    if pdf_bytes is None:
        pdf_bytes = bajar_pdf(url)
    return MOTORES[motor](pdf_bytes)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--numero", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--motor", choices=list(MOTORES), default=MOTOR_DEFAULT)
    args = ap.parse_args()
    url = f"{BASE}/Uploads/Boletines/{args.numero}_3_.pdf"
    t = time.time()
    texto = extraer(url, args.motor)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(texto)
    print(f"boletín {args.numero}: {len(texto)} caracteres ({args.motor}, {time.time() - t:.0f} s) -> {args.out}")


if __name__ == "__main__":
    main()
