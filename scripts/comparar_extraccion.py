"""
Diagnóstico: compara el resultado del PARSEO de un boletín usando el texto de
Apify contra el texto extraído localmente (extraer_pdf_local.py) con un motor
dado. Sale con código 0 solo si ambos dan exactamente las mismas marcas
(acta, clase, tipo, denominación, titular, país, matrícula). No imprime
datos personales, solo cantidades.

Uso:
    APIFY_TOKEN=... python3 comparar_extraccion.py --numero 11116 --motor pdfplumber
"""

import argparse
import os
import sys

from extraer_pdf_apify import correr_actor
from extraer_pdf_local import BASE, MOTORES, bajar_pdf
from parse_boletin import parse_boletin

CAMPOS = ["acta", "clase", "tipo", "denominacion", "titular", "pais", "matricula_agente"]


def claves(recs):
    return {tuple(r[c] for c in CAMPOS) for r in recs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--numero", required=True)
    ap.add_argument("--motor", choices=list(MOTORES), required=True)
    args = ap.parse_args()
    url = f"{BASE}/Uploads/Boletines/{args.numero}_3_.pdf"

    items = correr_actor([url], os.environ["APIFY_TOKEN"])
    texto_apify = (items[0].get("fullText") if items else "") or ""
    if not texto_apify:
        sys.exit("Apify no devolvió texto (¿timeout?) -- no se puede comparar")
    recs_apify = parse_boletin(texto_apify, args.numero)

    recs_local = parse_boletin(MOTORES[args.motor](bajar_pdf(url)), args.numero)

    a, b = claves(recs_apify), claves(recs_local)
    print(f"Apify: {len(recs_apify)} marcas | {args.motor}: {len(recs_local)} marcas | "
          f"iguales: {len(a & b)} | solo Apify: {len(a - b)} | solo {args.motor}: {len(b - a)}")
    for c in CAMPOS:  # qué campo difiere, por acta, sin mostrar el valor
        da = {r["acta"]: r[c] for r in recs_apify}
        dl = {r["acta"]: r[c] for r in recs_local}
        n = sum(1 for k in da if k in dl and da[k] != dl[k])
        if n:
            print(f"  campo {c}: {n} actas con valor distinto")
    if not recs_apify or a != b:
        sys.exit(1)
    print("OK: el texto local se parsea idéntico al de Apify")


if __name__ == "__main__":
    main()
