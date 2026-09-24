"""
Paso 3 — Parsear el texto extraído de un boletín (formato tipo WIPO ST.60) a filas
estructuradas.

Uso:
    python3 parse_boletin.py --numero 11121 --in 11121_text.txt --out 11121.csv
    python3 parse_boletin.py --text-dir textos/ --out-dir csv/   # procesa varios .txt

Bug ya resuelto (ver references/campos-boletin.md): el campo (74) a veces trae un
número de basura pegado entre la matrícula real y "- (44)" (interferencia de la
columna vecina en la extracción del PDF). El regex usado acá,
`\\(74\\)\\s*(Part\\.|\\d+)`, captura solo el primer token válido después de (74) e
ignora el resto — recupera ~1/3 de las matrículas que un regex más ingenuo pierde.
"""

import argparse
import csv
import glob
import os
import re
import sys

PATTERN_SOLICITUD = re.compile(
    r"\(21\) Acta (?P<acta>\d+) - \(51\) Clase\s*\n?\s*(?P<clase>\d+)\s*\n?"
    r"\(40\)\s*(?P<tipo>\S+)\s*\(54\)\s*(?P<denominacion>.*?)\s*\(22\)\s*"
    r"(?P<fecha>[\d/]+\s[\d:.]+)\s*-\s*\(73\)\s*\n?(?P<titular>.*?)\s*-\s*(?P<pais>[A-Z]{2})\s*\*"
    r"(?P<resto>.*?)(?=\(21\) Acta|\Z)",
    re.S,
)

# Fix del bug de (74): capturar solo el primer token (número o "Part."), sin exigir
# que "- (44)" venga pegado inmediatamente después.
PATTERN_MATRICULA = re.compile(r"\(74\)\s*(Part\.|\d+)")


def parse_boletin(text: str, numero_boletin: str) -> list[dict]:
    records = []
    for m in PATTERN_SOLICITUD.finditer(text):
        d = {k: m.group(k) for k in ["acta", "clase", "tipo", "denominacion", "fecha", "titular", "pais"]}
        for k in d:
            d[k] = re.sub(r"\s+", " ", d[k]).strip()

        resto = m.group("resto")
        am = PATTERN_MATRICULA.search(resto)
        d["matricula_agente"] = am.group(1).strip() if am else ""

        d["boletin"] = numero_boletin
        d["link"] = f"https://portaltramites.inpi.gob.ar/MarcasConsultas/Resultado?acta={d['acta']}"
        records.append(d)
    return records


FIELDNAMES = [
    "boletin", "acta", "clase", "tipo", "denominacion", "fecha",
    "titular", "pais", "matricula_agente", "link",
]


def _guardar_csv(records: list[dict], out_path: str):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(records)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--numero", help="número de boletín (con --in)")
    ap.add_argument("--in", dest="in_path", help="archivo .txt de un solo boletín")
    ap.add_argument("--out", help="CSV de salida (con --in)")
    ap.add_argument("--text-dir", help="directorio con varios *_text.txt (nombre = <numero>_text.txt)")
    ap.add_argument("--out-dir", help="directorio de salida para --text-dir")
    ap.add_argument(
        "--merged-out",
        help="además de los CSV individuales, escribe un CSV único con todo (con --text-dir)",
    )
    args = ap.parse_args()

    if args.in_path:
        if not args.numero or not args.out:
            sys.exit("--in requiere --numero y --out")
        text = open(args.in_path, encoding="utf-8").read()
        recs = parse_boletin(text, args.numero)
        _guardar_csv(recs, args.out)
        tipos = {}
        for r in recs:
            tipos[r["tipo"]] = tipos.get(r["tipo"], 0) + 1
        print(f"Boletín {args.numero}: {len(recs)} marcas. Por tipo: {tipos}")
        return

    if args.text_dir:
        os.makedirs(args.out_dir or ".", exist_ok=True)
        todos = []
        for path in sorted(glob.glob(os.path.join(args.text_dir, "*_text.txt"))):
            numero = os.path.basename(path).split("_text.txt")[0]
            text = open(path, encoding="utf-8").read()
            recs = parse_boletin(text, numero)
            print(f"Boletín {numero}: {len(recs)} marcas")
            out_path = os.path.join(args.out_dir or ".", f"{numero}.csv")
            _guardar_csv(recs, out_path)
            todos.extend(recs)
        print(f"\nTotal: {len(todos)} marcas en {len(glob.glob(os.path.join(args.text_dir, '*_text.txt')))} boletines")
        if args.merged_out:
            _guardar_csv(todos, args.merged_out)
            print(f"Guardado consolidado: {args.merged_out}")
        return

    sys.exit("Usar --in (un boletín) o --text-dir (varios)")


if __name__ == "__main__":
    main()
