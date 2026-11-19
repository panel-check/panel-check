"""
Prueba de las notificaciones del webservice de Autogestión del INPI (solo lectura).

Llama a ConsultaNotificaciones y a ConsultaNotificacionesAdjunto (ws.inpi.gob.ar/wsinpi.asmx) con el
"Token de esta relación" de la delegación de Pame (apoderada de todas las marcas con las que trabajamos),
para ver qué devuelve el INPI antes de diseñar la tabla y la pantalla del panel: qué campos trae cada
notificación, qué tipos aparecen y en qué campo viene el PDF adjunto (la página del INPI no lo documenta).

Los logs de GitHub Actions son públicos: acá solo se imprime la ESTRUCTURA (nombres de campos, cantidad de
notificaciones, códigos de tipo y largo de los valores), nunca nombres ni datos de personas. La respuesta
completa se manda por mail (Resend) a NOTIFICAR_A, con los base64 recortados y hasta 3 PDF adjuntos.

Variables:
    INPI_HASH_DELEGACION   token de la relación integrador ↔ Pame (secret de GitHub)
    RESEND_API_KEY / RESEND_FROM / NOTIFICAR_A   para el mail con la respuesta
    INPI_RELAY_URL / INPI_RELAY_CLAVE            reenvío por Argentina (panel/inpi_relay.py)

Uso:
    python3 probar_notificaciones_ws.py [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD] [--acta N] [--tipo N]
    (sin fechas, el INPI toma el último año)
"""

import argparse
import base64
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from html import escape as xml_escape

import requests

SOAP_URL = "https://ws.inpi.gob.ar/wsinpi.asmx"
NS_TEM = "http://tempuri.org/"
LARGO_BASE64 = 200          # un valor más largo que esto se trata como base64 (adjunto)
MAX_PDF_MAIL = 3


def _envelope(operacion: str, filtros: dict, hash_delegacion: str) -> str:
    campos = "".join(
        f"<{k}>{xml_escape(v)}</{k}>" for k, v in filtros.items() if v
    )
    if operacion == "ConsultaNotificaciones":
        credencial = f"<datosUsuario><HashDelegacion>{xml_escape(hash_delegacion)}</HashDelegacion></datosUsuario>"
    else:   # ConsultaNotificacionesAdjunto: el hash va directo, sin datosUsuario
        credencial = f"<hashDelegacion>{xml_escape(hash_delegacion)}</hashDelegacion>"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <{operacion} xmlns="{NS_TEM}">{campos}{credencial}</{operacion}>
  </soap:Body>
</soap:Envelope>"""


def llamar(operacion: str, filtros: dict, hash_delegacion: str, reintentos: int = 3) -> tuple[int, str]:
    """POST SOAP 1.1. Ante error o bloqueo espera 5, 10, 20 s, como pide el INPI."""
    body = _envelope(operacion, filtros, hash_delegacion)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"{NS_TEM}{operacion}"',
        "User-Agent": "panel-check/1.0 (+consulta de notificaciones propias)",
        "Accept": "text/xml",
    }
    ultimo = (0, "")
    for intento in range(reintentos + 1):
        try:
            r = requests.post(SOAP_URL, data=body.encode("utf-8"), headers=headers, timeout=(20, 120))
            ultimo = (r.status_code, r.text)
            if r.status_code == 200:
                return ultimo
        except requests.RequestException as e:
            ultimo = (0, f"ERROR DE CONEXION: {type(e).__name__}")
        if intento < reintentos:
            time.sleep(5 * (2 ** intento))
    return ultimo


def _tag(e) -> str:
    return e.tag.split("}")[-1]


def estructura(xml_texto: str) -> list[str]:
    """Describe el XML sin mostrar valores: ruta de cada campo, cuántas veces aparece y largo máximo.
    Los campos de tipo/código/estado sí muestran sus valores distintos (no son datos personales)."""
    try:
        root = ET.fromstring(xml_texto)
    except ET.ParseError as e:
        return [f"(la respuesta no es XML válido: {e}; {len(xml_texto)} caracteres)"]
    conteo, largo, valores = Counter(), {}, {}

    def recorrer(nodo, ruta):
        for hijo in nodo:
            r = f"{ruta}/{_tag(hijo)}"
            conteo[r] += 1
            texto = (hijo.text or "").strip()
            largo[r] = max(largo.get(r, 0), len(texto))
            if texto and re.search(r"tipo|codigo|cod|estado|direccion|faultcode|resultado|error", _tag(hijo), re.I) \
                    and len(texto) <= 60:
                valores.setdefault(r, Counter())[texto] += 1
            recorrer(hijo, r)

    recorrer(root, "")
    lineas = []
    for r, n in conteo.items():
        extra = ""
        if largo[r] > LARGO_BASE64:
            extra = "  ← probable base64 / adjunto"
        if r in valores:
            extra += "  valores: " + ", ".join(f"{v} ({c})" for v, c in valores[r].most_common(25))
        lineas.append(f"{r}  ×{n}  largo máx {largo[r]}{extra}")
    return lineas


def recortar_y_extraer(xml_texto: str) -> tuple[str, list[tuple[str, bytes]]]:
    """Recorta los base64 largos (para que el mail sea legible) y devuelve hasta MAX_PDF_MAIL archivos."""
    archivos = []

    def reemplazo(m):
        contenido = m.group(2)
        if len(archivos) < MAX_PDF_MAIL:
            try:
                datos = base64.b64decode(contenido, validate=False)
                ext = "pdf" if datos[:4] == b"%PDF" else "bin"
                archivos.append((f"adjunto_{len(archivos) + 1}.{ext}", datos))
            except Exception:
                pass
        return f"{m.group(1)}[base64 recortado: {len(contenido)} caracteres]"

    recortado = re.sub(r"(>)([A-Za-z0-9+/=\s]{%d,})(?=<)" % LARGO_BASE64, reemplazo, xml_texto)
    return recortado, archivos


def mandar_mail(asunto: str, cuerpo: str, adjuntos: list[tuple[str, bytes]]) -> None:
    clave = os.environ.get("RESEND_API_KEY")
    destino = [d.strip() for d in (os.environ.get("NOTIFICAR_A") or "").split(",") if d.strip()]
    remitente = os.environ.get("RESEND_FROM")
    if not (clave and destino and remitente):
        print("No hay RESEND_API_KEY / RESEND_FROM / NOTIFICAR_A: no se manda el mail.")
        return
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {clave}"},
        json={
            "from": remitente, "to": destino, "subject": asunto,
            "text": cuerpo,
            "attachments": [
                {"filename": n, "content": base64.b64encode(d).decode()} for n, d in adjuntos
            ],
        },
        timeout=60,
    )
    print(f"Mail con la respuesta completa: HTTP {r.status_code}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--desde", default="")
    p.add_argument("--hasta", default="")
    p.add_argument("--acta", default="")
    p.add_argument("--tipo", default="")
    a = p.parse_args()

    hash_delegacion = (os.environ.get("INPI_HASH_DELEGACION") or "").strip()
    if not hash_delegacion:
        print("Falta el secret INPI_HASH_DELEGACION (Token de esta relación, desde la Autogestión WS).")
        sys.exit(1)

    filtros = {
        "fechaInicial": a.desde, "fechafinal": a.hasta, "expediente": a.acta,
        "direccion": "marcas", "tipoNotificacion": a.tipo,
    }
    cuerpo_mail, adjuntos_mail = [], []
    for operacion in ("ConsultaNotificaciones", "ConsultaNotificacionesAdjunto"):
        t0 = time.time()
        estado, texto = llamar(operacion, filtros, hash_delegacion)
        print(f"\n=== {operacion}: HTTP {estado}, {len(texto)} caracteres, {time.time() - t0:.1f} s ===")
        for linea in estructura(texto):
            print("  " + linea)
        recortado, archivos = recortar_y_extraer(texto)
        adjuntos_mail += archivos[: MAX_PDF_MAIL - len(adjuntos_mail)]
        cuerpo_mail.append(f"===== {operacion} (HTTP {estado}) =====\n\n{recortado}\n")
        time.sleep(3)

    mandar_mail(
        "Prueba notificaciones INPI (webservice Autogestión)",
        "Filtros: " + ", ".join(f"{k}={v or '(vacío)'}" for k, v in filtros.items()) + "\n\n" + "\n".join(cuerpo_mail),
        adjuntos_mail,
    )


if __name__ == "__main__":
    main()
