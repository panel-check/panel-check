"""
Paso 2 — Extraer el texto de uno o más PDFs de boletines vía Apify.

Actor: automation-lab/pdf-text-extractor
(NO usar santamaria-automations/pdf-extractor: límite de memoria ~128 MB no
configurable, falla con boletines de más de ~100 páginas. Tampoco
eliai/pdf-text-extractor: timeout interno fijo ~30s.)

Requiere la variable de entorno APIFY_TOKEN.

Uso:
    python3 extraer_pdf_apify.py --url https://portaltramites.inpi.gob.ar/Uploads/Boletines/11121_3_.pdf --out 11121_text.txt
    python3 extraer_pdf_apify.py --urls-file boletines.json --out-dir textos/
"""

import argparse
import json
import os
import sys
import time

import requests

from extraer_pdf_local import MOTOR_DEFAULT
from extraer_pdf_local import extraer as extraer_local

ACTOR = "automation-lab~pdf-text-extractor"
APIFY_BASE = "https://api.apify.com/v2"


def correr_actor(urls: list[str], token: str, timeout_per_pdf: int = 300) -> list[dict]:
    """Corre el actor de forma síncrona y devuelve los items del dataset
    (uno por PDF: {url, fullText, pageCount, fileSizeBytes, ...})."""
    run_url = f"{APIFY_BASE}/acts/{ACTOR}/run-sync-get-dataset-items"
    resp = requests.post(
        run_url,
        params={"token": token},
        json={"urls": urls, "timeoutPerPdfSecs": timeout_per_pdf, "includePages": False},
        timeout=timeout_per_pdf * len(urls) + 120,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="una sola URL de PDF")
    g.add_argument("--urls-file", help="JSON con lista de boletines (salida de listar_boletines.py)")
    ap.add_argument("--out", help="archivo de salida de texto (con --url)")
    ap.add_argument("--out-dir", help="directorio de salida, un .txt por boletín (con --urls-file)")
    ap.add_argument("--batch-size", type=int, default=5, help="PDFs por corrida de Apify (recomendado: 5)")
    args = ap.parse_args()

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("Falta la variable de entorno APIFY_TOKEN")

    if args.url:
        items = correr_actor([args.url], token)
        if not items or not items[0].get("fullText"):
            sys.exit(f"No se pudo extraer texto de {args.url}")
        texto = items[0]["fullText"]
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(texto)
            print(f"Guardado {len(texto)} caracteres en {args.out}")
        else:
            print(texto)
        return

    with open(args.urls_file, encoding="utf-8") as f:
        boletines = json.load(f)
    if not boletines:
        print("No hay boletines para procesar.", file=sys.stderr)
        return

    os.makedirs(args.out_dir or ".", exist_ok=True)
    url_a_numero = {b["pdf_url"]: b["numero"] for b in boletines}
    urls = list(url_a_numero.keys())

    fallidos: list[str] = []
    debug_path = os.path.join(args.out_dir or ".", "_debug_apify_response.json")

    for i in range(0, len(urls), args.batch_size):
        lote = urls[i : i + args.batch_size]
        print(f"Procesando lote {i // args.batch_size + 1} ({len(lote)} PDFs)...", file=sys.stderr)
        try:
            items = correr_actor(lote, token)
        except Exception as e:
            # Error de la API de Apify (HTTP, timeout de la llamada, etc.):
            # no cortamos, todos los PDFs del lote van al respaldo local.
            print(f"  ADVERTENCIA: Apify falló para el lote ({e}); se usa la extracción local", file=sys.stderr)
            items = []

        # Volcado de diagnóstico: qué devolvió realmente Apify (claves y un resumen),
        # para poder detectar si el esquema de salida del actor cambió.
        resumen_debug = {
            "cantidad_items": len(items),
            "items": [
                {k: (v if not isinstance(v, str) else f"{v[:200]}... ({len(v)} chars)") for k, v in it.items()}
                for it in items
            ],
        }
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(resumen_debug, f, ensure_ascii=False, indent=2)
        print(f"  DEBUG: {len(items)} items devueltos. Claves del primero: "
              f"{list(items[0].keys()) if items else 'N/A'}", file=sys.stderr)

        texto_por_url = {it.get("url"): it.get("fullText") or "" for it in items}
        for url in lote:
            numero = url_a_numero[url]
            texto = texto_por_url.get(url, "")
            origen = "Apify"
            if not texto:
                # Respaldo: Apify tiene un tope FIJO de 300 s por PDF y los
                # boletines grandes (34-40 MB) lo superan -- devuelve el item
                # sin fullText (Pipeline #27, 30/09/2026). Se baja el PDF y se
                # extrae acá mismo (ver extraer_pdf_local.py).
                print(f"  ADVERTENCIA: Apify no devolvió texto del boletín {numero}; "
                      f"extrayendo localmente ({MOTOR_DEFAULT})", file=sys.stderr)
                try:
                    texto = extraer_local(url)
                    origen = f"local/{MOTOR_DEFAULT}"
                except Exception as e:
                    print(f"  ERROR: tampoco se pudo extraer localmente el boletín {numero}: {e}", file=sys.stderr)
                    fallidos.append(numero)
                    continue
            out_path = os.path.join(args.out_dir or ".", f"{numero}_text.txt")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(texto)
            print(f"  boletín {numero}: {len(texto)} caracteres ({origen}) -> {out_path}", file=sys.stderr)
        if i + args.batch_size < len(urls):
            time.sleep(2)

    if fallidos:
        sys.exit(f"No se pudo extraer texto de: {', '.join(fallidos)}")


if __name__ == "__main__":
    main()
