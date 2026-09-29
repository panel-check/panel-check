"""
Validación puntual (no forma parte del pipeline, se borra después de usarla):
para una acta real con tuvo_oposicion = true, bajamos:
  1) la sección OPOSICIONES de /MarcasConsultas/Resultado (texto plano,
     igual que ya hace inspeccionar_acta.py con otras secciones), y
  2) el/los PDF "Formulario" y "Recibo de Ingreso" de Grilla Digital cuya
     Fecha coincide con la de la oposición detectada (no el Formulario
     original del expediente, que es el de la solicitud de la marca).

Objetivo: confirmar con datos reales si esos dos PDF (o la sección
OPOSICIONES de la página) traen el detalle de POR QUÉ/QUIÉN se opone
(marca/expediente del oponente, fundamentos) o si son solo constancias
administrativas de que se presentó un escrito.

Uso:
    DATABASE_URL=... python3 validar_pdf_oposicion.py
    DATABASE_URL=... python3 validar_pdf_oposicion.py --acta 1234567
"""

import argparse
import io
import os
import re
import sys

import psycopg2
import psycopg2.extras

from validar_leads import (
    BASE,
    crear_sesion,
    _get_con_reintentos,
    buscar_archivos_grilla,
)
from inspeccionar_acta import _bloque_seccion, _texto_sin_tags


def elegir_acta(dsn: str, acta_forzada: str | None) -> tuple[str, str, str]:
    if acta_forzada:
        return acta_forzada, "(forzada por --acta)", "(forzada por --acta)"
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT acta, titular, detalle_oposicion, fecha_publicacion
                FROM marcas
                WHERE tuvo_oposicion = true
                ORDER BY fecha_publicacion DESC NULLS LAST
                LIMIT 5
                """
            )
            filas = cur.fetchall()
    finally:
        conn.close()
    if not filas:
        sys.exit("No hay ninguna acta con tuvo_oposicion = true en la base todavía")
    print("::notice::Candidatas con tuvo_oposicion=true (más recientes primero):")
    for f in filas:
        print(f"::notice::  acta {f['acta']} ({f['titular']}) — {f['detalle_oposicion']}")
    elegida = filas[0]
    return elegida["acta"], elegida["titular"], elegida["detalle_oposicion"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--acta", default=None)
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn and not args.acta:
        sys.exit("Falta DATABASE_URL (o pasar --acta a mano)")

    acta, titular, detalle = elegir_acta(dsn, args.acta)
    print(f"::notice::Usando acta {acta} ({titular}) — detalle guardado en DB: {detalle}")

    s = crear_sesion()

    # 1) Sección OPOSICIONES de la página de resultado del expediente.
    r = _get_con_reintentos(
        lambda: s.post(
            f"{BASE}/MarcasConsultas/Resultado",
            headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
            data={"acta": acta},
            timeout=30,
        )
    )
    if "Web Page Blocked" in r.text or "Attack ID" in r.text:
        print(f"::error::acta {acta} bloqueada por el WAF de INPI al pedir Resultado")
    else:
        for nombre in ("OPOSICIONES", "VISTAS Y NOTIFICACIONES"):
            bloque = _bloque_seccion(r.text, nombre)
            bloque = re.sub(r"\s+", " ", bloque).strip()[:1500]
            print(f"::notice::SECCION {nombre}: {bloque}")

    # 2) Archivos de Grilla Digital: nos quedamos con los que tengan fecha
    #    de días recientes a la oposición (no el Formulario original de la
    #    solicitud, que es viejo).
    archivos = buscar_archivos_grilla(s, acta)
    if not archivos:
        print(f"::error::acta {acta}: no se pudo consultar Grilla Digital (WAF)")
        sys.exit(1)

    print(f"::notice::Total de archivos en Grilla Digital: {len(archivos)}")
    for a in archivos:
        print(
            f"::notice::ARCHIVO Indice={a.get('Indice')!r} Referencia={a.get('Referencia')!r} "
            f"Fecha={a.get('Fecha')!r}"
        )

    # Candidatos: cualquier archivo cuya Referencia mencione "Opo" (el de la
    # oposición), más el/los "Formulario"/"Recibo de Ingreso" con la MISMA
    # fecha que ese archivo (mismo trámite, mismo día de ingreso).
    referencia_opo = next(
        (a for a in archivos if "OPO" in (a.get("Referencia") or "").upper()), None
    )
    if not referencia_opo:
        print("::error::no encontré ninguna fila con Referencia conteniendo 'Opo' en esta acta")
        sys.exit(1)

    fecha_opo = referencia_opo.get("Fecha")
    print(f"::notice::Fila de la oposición: {referencia_opo}")

    relacionados = [a for a in archivos if a.get("Fecha") == fecha_opo]
    print(f"::notice::Archivos con la misma fecha ({fecha_opo}): {len(relacionados)}")

    for a in relacionados:
        indice = a.get("Indice")
        id_doc = a.get("id_Documento_encriptado")
        ruta = a.get("ruta") or ""
        if not id_doc or not ruta:
            print(f"::notice::  {indice}: sin id_Documento_encriptado/ruta, no se puede descargar")
            continue
        nombre_archivo = ruta.rsplit("/", 1)[-1]
        r_pdf = _get_con_reintentos(
            lambda: s.get(
                f"{BASE}/Home/edmsxidd",
                params={"id": id_doc, "nombre": nombre_archivo},
                headers={"Referer": f"{BASE}/Home/GrillaDigital"},
                timeout=30,
            )
        )
        ctype = r_pdf.headers.get("Content-Type", "").lower()
        if ctype != "application/pdf":
            print(f"::error::  {indice}: no se pudo descargar como PDF (Content-Type={ctype})")
            continue

        import pdfplumber

        with pdfplumber.open(io.BytesIO(r_pdf.content)) as pdf:
            texto = "\n".join(p.extract_text() or "" for p in pdf.pages)
        texto_plano = re.sub(r"\s+", " ", texto).strip()
        print(f"::notice::PDF {indice} ({len(texto_plano)} caracteres) primeros 1500: {texto_plano[:1500]}")
        if len(texto_plano) > 1500:
            print(f"::notice::PDF {indice} resto (1500-3000): {texto_plano[1500:3000]}")

    print(f"::notice::acta {acta} — validación de PDF de oposición terminada")


if __name__ == "__main__":
    main()
