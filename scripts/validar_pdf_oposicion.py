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


def _archivos_relacionados(s, acta: str):
    """Devuelve (fecha_opo, lista_de_archivos_del_mismo_dia).

    Ojo: comparar el campo Fecha como string exacto es un error — el
    Formulario y el Recibo de Ingreso de un mismo trámite se cargan con
    unos segundos/milisegundos de diferencia (confirmado a mano: acta
    4764327, Recibo a las 13:12:17.833 pero el Formulario con otro
    timestamp), así que compara por DÍA calendario, no por el string
    completo de /Date(ms)/."""
    from validar_leads import _parsear_fecha_grilla

    archivos = buscar_archivos_grilla(s, acta)
    if not archivos:
        print(f"::error::acta {acta}: no se pudo consultar Grilla Digital (WAF)")
        sys.exit(1)
    referencia_opo = next(
        (a for a in archivos if "OPO" in (a.get("Referencia") or "").upper()), None
    )
    if not referencia_opo:
        print("::error::no encontré ninguna fila con Referencia conteniendo 'Opo' en esta acta")
        sys.exit(1)
    fecha_opo = referencia_opo.get("Fecha")
    dia_opo = _parsear_fecha_grilla(fecha_opo)
    if dia_opo:
        relacionados = [a for a in archivos if _parsear_fecha_grilla(a.get("Fecha") or "") == dia_opo]
    else:
        relacionados = [a for a in archivos if a.get("Fecha") == fecha_opo]
    return fecha_opo, relacionados


def modo_seccion(acta: str):
    """Vuelca la sección OPOSICIONES / VISTAS Y NOTIFICACIONES de la página
    de resultado del expediente, en chunks para no perder texto por el
    límite de annotations."""
    s = crear_sesion()
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
        return
    for nombre in ("OPOSICIONES", "VISTAS Y NOTIFICACIONES"):
        bloque = _bloque_seccion(r.text, nombre)
        bloque = re.sub(r"\s+", " ", bloque).strip()
        print(f"::notice::SECCION {nombre} ({len(bloque)} caracteres):")
        paso = 1400
        for i in range(0, min(len(bloque), 4 * paso), paso):
            print(f"::notice::  [{i}:{i+paso}] {bloque[i:i+paso]}")


def modo_archivos(acta: str):
    """Lista compacta (sin section dump) de los archivos de Grilla Digital
    que compartan fecha con la fila de oposición."""
    s = crear_sesion()
    fecha_opo, relacionados = _archivos_relacionados(s, acta)
    print(f"::notice::Fecha de la oposición: {fecha_opo}. Archivos con esa misma fecha:")
    for a in relacionados:
        print(
            f"::notice::  Indice={a.get('Indice')!r} Referencia={a.get('Referencia')!r} "
            f"id_Documento_encriptado={a.get('id_Documento_encriptado')!r} ruta={a.get('ruta')!r}"
        )


def modo_pdf(acta: str, indice: str):
    """Descarga el PDF cuyo Indice coincide (ej. 'Formulario' o
    'Recibo de Ingreso') entre los archivos con la misma fecha que la
    oposición, y vuelca su texto completo en chunks."""
    s = crear_sesion()
    _, relacionados = _archivos_relacionados(s, acta)
    candidatos = [a for a in relacionados if a.get("Indice") == indice]
    if not candidatos:
        print(f"::error::no hay ningún archivo con Indice={indice!r} en la fecha de la oposición")
        return
    for n, a in enumerate(candidatos, 1):
        id_doc = a.get("id_Documento_encriptado")
        ruta = a.get("ruta") or ""
        if not id_doc or not ruta:
            print(f"::error::  {indice} #{n}: sin id_Documento_encriptado/ruta")
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
            print(f"::error::  {indice} #{n}: no se pudo descargar como PDF (Content-Type={ctype})")
            continue

        import pdfplumber

        with pdfplumber.open(io.BytesIO(r_pdf.content)) as pdf:
            texto = "\n".join(p.extract_text() or "" for p in pdf.pages)
        texto_plano = re.sub(r"\s+", " ", texto).strip()
        print(f"::notice::PDF {indice} #{n} ({len(texto_plano)} caracteres):")
        paso = 1400
        for i in range(0, min(len(texto_plano), 4 * paso), paso):
            print(f"::notice::  [{i}:{i+paso}] {texto_plano[i:i+paso]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--acta", default=None)
    ap.add_argument(
        "--modo", choices=["elegir", "seccion", "archivos", "pdf"], default="elegir",
        help="elegir = solo mostrar candidatas (default); seccion = texto de OPOSICIONES/VISTAS; "
             "archivos = listar archivos de la oposición; pdf = bajar y leer un PDF puntual",
    )
    ap.add_argument("--pdf-indice", default=None, help="Indice del archivo a leer con --modo pdf (ej. Formulario)")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn and not args.acta:
        sys.exit("Falta DATABASE_URL (o pasar --acta a mano)")

    if args.modo == "elegir" or not args.acta:
        acta, titular, detalle = elegir_acta(dsn, args.acta)
        print(f"::notice::ACTA_ELEGIDA={acta}")
        print(f"::notice::Usando acta {acta} ({titular}) — detalle guardado en DB: {detalle}")
        if args.modo == "elegir":
            return
    else:
        acta = args.acta

    if args.modo == "seccion":
        modo_seccion(acta)
    elif args.modo == "archivos":
        modo_archivos(acta)
    elif args.modo == "pdf":
        if not args.pdf_indice:
            sys.exit("--modo pdf necesita --pdf-indice")
        modo_pdf(acta, args.pdf_indice)

    print(f"::notice::acta {acta} — modo {args.modo} terminado")


if __name__ == "__main__":
    main()
