"""
Paso 4 — Completa la Denominación de marcas Mixtas/Figurativas cruzando contra
el webservice SOAP público de INPI (ws.inpi.gob.ar/wsinpi.asmx),
operación ConsultaCuitOTitular.

Uso:
    python3 completar_mixtas.py --in 11121.csv --out 11121_completo.csv
    python3 completar_mixtas.py --in 11121.csv --out 11121_completo.csv --workers 4 --delay 2

--delay agrega una espera (segundos) entre consultas secuenciales en vez de usar
ThreadPoolExecutor — usar esto para el barrido de boletines históricos (ver
SKILL.md), donde no conviene golpear el webservice de INPI en paralelo.
"""

import argparse
import csv
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape as xml_escape

SOAP_URL = "https://ws.inpi.gob.ar/wsinpi.asmx"
SOAP_ACTION = "http://tempuri.org/ConsultaCuitOTitular"
NS = {"a": "http://tempuri.org/"}


def consultar_cuit(cuit: str, timeout: int = 60, reintentos: int = 2) -> list[dict]:
    """Igual que consultar_titular pero buscando por CUIT (más preciso: no
    depende de cómo esté escrito el nombre). Lo usa validar_leads.py como
    último recurso para el nombre de Mixtas del escaneo directo de actas."""
    return consultar_titular("", timeout=timeout, reintentos=reintentos, cuit=cuit)


def consultar_titular(titular: str, timeout: int = 60, reintentos: int = 2, cuit: str = "") -> list[dict]:
    """Llama a ConsultaCuitOTitular y devuelve la lista de GrillaMarcas (dicts).
    El servicio de INPI tiene latencia variable (1s a 60s+), así que reintenta
    antes de darse por vencido.

    Advertencia de seguridad conocida: un titular con apóstrofe (ej. O'Brien) puede
    hacer que el webservice devuelva un error SQL crudo (indicio de inyección SQL
    del lado de INPI). No explotar esto; si aparece, reportarlo a
    soportews@inpi.gob.ar y seguir de largo con el resto de los titulares."""
    titular_safe = xml_escape(titular)
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ConsultaCuitOTitular xmlns="http://tempuri.org/">
      <cuit>{xml_escape(cuit)}</cuit>
      <titular>{titular_safe}</titular>
    </ConsultaCuitOTitular>
  </soap:Body>
</soap:Envelope>"""

    for intento in range(reintentos + 1):
        result = subprocess.run(
            [
                "curl", "-sS", "-m", str(timeout),
                "-X", "POST", SOAP_URL,
                "-H", "Content-Type: text/xml; charset=utf-8",
                "-H", f'SOAPAction: "{SOAP_ACTION}"',
                "--data-binary", "@-",
            ],
            input=body, capture_output=True, text=True,
        )

        if result.returncode == 0 and result.stdout.strip():
            try:
                root = ET.fromstring(result.stdout)
            except ET.ParseError:
                continue
            filas = []
            for grilla in root.iter("{http://tempuri.org/}GrillaMarcas"):
                fila = {child.tag.split("}")[-1]: (child.text or "") for child in grilla}
                filas.append(fila)
            return filas
        # si falló (timeout, sin datos), reintenta
    return []


def completar(rows: list[dict], workers: int = 8, delay: float = 0.0) -> tuple[list[dict], list[str]]:
    objetivo = [r for r in rows if r["tipo"] in ("M", "F")]
    titulares_unicos = sorted(set(r["titular"] for r in objetivo))
    print(f"Marcas a completar: {len(objetivo)} | Titulares únicos a consultar: {len(titulares_unicos)}")

    cache: dict[str, dict] = {}
    fallidos = []
    completadas_count = 0

    if delay > 0:
        # secuencial, con freno de mano — usar para barridos históricos masivos
        for titular in titulares_unicos:
            filas = consultar_titular(titular)
            if not filas:
                fallidos.append(titular)
            cache[titular] = {str(f.get("Acta", "")): f for f in filas}
            completadas_count += 1
            if completadas_count % 20 == 0 or completadas_count == len(titulares_unicos):
                print(f"  ...{completadas_count}/{len(titulares_unicos)} titulares consultados "
                      f"({len(fallidos)} fallidos hasta ahora)")
            time.sleep(delay)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futuros = {ex.submit(consultar_titular, t, 30, 1): t for t in titulares_unicos}
            for fut in as_completed(futuros):
                titular = futuros[fut]
                try:
                    filas = fut.result()
                except Exception:
                    filas = []
                if not filas:
                    fallidos.append(titular)
                cache[titular] = {str(f.get("Acta", "")): f for f in filas}
                completadas_count += 1
                if completadas_count % 20 == 0 or completadas_count == len(titulares_unicos):
                    print(f"  ...{completadas_count}/{len(titulares_unicos)} titulares consultados "
                          f"({len(fallidos)} fallidos hasta ahora)")

    completados = 0
    for r in rows:
        if r["tipo"] not in ("M", "F"):
            r["denominacion_inpi"] = ""
            r["cuit"] = ""
            continue
        match = cache.get(r["titular"], {}).get(r["acta"])
        if match:
            r["denominacion_inpi"] = match.get("Denominacion", "")
            titulares_raw = match.get("Titulares", "")
            # el campo Titulares trae "CUIT    NOMBRE   PORCENTAJE%". A veces
            # el webservice devuelve "0" en vez de vacío cuando no tiene el
            # CUIT del titular registrado — lo tratamos igual que vacío en
            # vez de guardar "0" como si fuera un CUIT real.
            cuit_bruto = titulares_raw.strip().split()[0] if titulares_raw.strip() else ""
            r["cuit"] = cuit_bruto if re.match(r"^\d{10,11}$", cuit_bruto) else ""
            if match.get("Denominacion"):
                completados += 1
        else:
            r["denominacion_inpi"] = ""
            r["cuit"] = ""

    print(f"\nCompletadas con éxito: {completados}/{len(objetivo)}")
    return rows, fallidos


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="in_path", required=True, help="CSV de entrada (salida de parse_boletin.py)")
    ap.add_argument("--out", required=True, help="CSV de salida, con columnas denominacion_inpi y cuit agregadas")
    ap.add_argument("--workers", type=int, default=8, help="hilos concurrentes (default 8, ignorado si --delay > 0)")
    ap.add_argument("--delay", type=float, default=0.0, help="segundos entre consultas secuenciales (usar para historial masivo)")
    args = ap.parse_args()

    with open(args.in_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"{args.in_path} está vacío")

    rows, fallidos = completar(rows, workers=args.workers, delay=args.delay)

    if fallidos:
        # No se imprimen los titulares (dato personal de terceros): sólo la
        # cantidad. La lista completa igual queda en el CSV de salida, que
        # no se publica en los logs de Actions.
        print(f"Titulares sin respuesta del webservice: {len(fallidos)}")

    fieldnames = list(rows[0].keys())
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"\nGuardado: {args.out}")


if __name__ == "__main__":
    main()
