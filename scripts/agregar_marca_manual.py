"""
Herramienta de uso puntual: agrega al panel una marca que NO vino de un
boletín procesado por el pipeline (por eso no está en la base) — pensada
para casos como el de "TODO SOBRE TENIS" (acta 4534497), que la pidió el
usuario a mano para ver cómo se ven las columnas de estado/vencimiento en
el panel.

Combina:
  - RESOLUCIÓN (parsear_resolucion, ya verificado) -> estado_tramite,
    fecha_concesion, numero_disposicion, fecha_vencimiento_marca.
  - CUIT (RE_CUIT_SPAN, ya verificado).
  - DATOS GENERALES (denominación, tipo, fecha de presentación) y CLASE,
    con el mismo patrón de "último bloque real" usado en
    inspeccionar_acta.py — confirmado a mano para esta acta puntual, no
    tan probado como los anteriores.

El nombre del titular NO se pudo ubicar con confianza en esta página (la
única tabla "TIPO Y NOMBRE DEL TITULAR" que aparece es de historial de
cambios de rubro, no la ficha principal) — queda como placeholder explícito
para no inventar un dato.

Uso:
    DATABASE_URL=... python3 agregar_marca_manual.py 4534497
"""

import os
import re
import sys

import psycopg2

from validar_leads import (
    BASE, crear_sesion, _get_con_reintentos, parsear_resolucion, RE_CUIT_SPAN,
)


def _texto_sin_tags(html: str) -> str:
    texto = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", texto).strip()


def _bloque_seccion(html: str, nombre: str) -> str:
    patron = re.compile(
        re.escape(nombre) + r".*?</h4>\s*</div>\s*<div[^>]*>(.*?)</div>\s*</div>\s*</div>",
        re.S,
    )
    matches = patron.findall(html)
    return _texto_sin_tags(matches[-1]) if matches else ""


def _fecha_ddmmyyyy_a_iso(valor: str):
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", valor.strip())
    if not m:
        return None
    d, mes, anio = m.groups()
    return f"{anio}-{mes.zfill(2)}-{d.zfill(2)}"


TIPOS_MARCA_INVERSO = {"Denominativa": "D", "Mixta": "M", "Figurativa": "F", "Tridimensional": "T"}


def main():
    if len(sys.argv) < 2:
        sys.exit("Uso: python3 agregar_marca_manual.py <acta>")
    acta = sys.argv[1]

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

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
        print(f"::error::acta {acta} bloqueada por el WAF de INPI")
        sys.exit(1)

    resolucion = parsear_resolucion(r.text)
    m_cuit = RE_CUIT_SPAN.search(r.text)
    cuit = m_cuit.group(1) if m_cuit else None

    datos_generales = _bloque_seccion(r.text, "DATOS GENERALES")
    m_denom = re.search(r"DENOMINACI[ÓO]N:\s*([^:]+?)\s+TIPO DE MARCA:", datos_generales)
    m_tipo = re.search(r"TIPO DE MARCA:\s*([^:]+?)\s+(?:DOMICILO|RENOVACION|NRO)", datos_generales)
    m_pres = re.search(r"PRESENTACI[ÓO]N:\s*(\d{1,2}/\d{1,2}/\d{4})", datos_generales)

    titularidad = _bloque_seccion(r.text, "TITULARIDAD")
    m_clase = re.search(r"CLASE:\s*(\d+)", titularidad)

    # El nombre real del titular está en un <label class="input"> aparte,
    # como "NOMBRE: <span class="text-danger"> BOTTERO TOMAS 100.00%</span>"
    # (el % es el porcentaje de titularidad, no parte del nombre). Confirmado
    # a mano contra esta misma acta — no tan probado como CARACTER/CUIT.
    m_nombre = re.search(
        r"NOMBRE\s*:?\s*(?:<span[^>]*>)?\s*([^<]+?)\s*[\d.]+\s*%", r.text
    )
    titular_nombre = re.sub(r"\s+", " ", m_nombre.group(1)).strip() if m_nombre else None

    denominacion = m_denom.group(1).strip() if m_denom else None
    tipo_legible = m_tipo.group(1).strip() if m_tipo else None
    tipo = TIPOS_MARCA_INVERSO.get(tipo_legible)
    fecha_presentacion = _fecha_ddmmyyyy_a_iso(m_pres.group(1)) if m_pres else None
    clase = int(m_clase.group(1)) if m_clase else None

    print(f"::notice::Datos a insertar acta {acta}: denominacion={denominacion!r} tipo={tipo!r} "
          f"clase={clase} fecha_presentacion={fecha_presentacion} cuit={cuit} "
          f"titular={titular_nombre!r} resolucion={resolucion}")

    if not denominacion:
        print(f"::error::no se pudo extraer la denominación de DATOS GENERALES, no se inserta nada")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO marcas (
                acta, boletin, clase, tipo, denominacion, denominacion_inpi,
                fecha_presentacion, titular, pais, cuit, matricula_agente, link,
                caracter, es_lead, revisado_manual,
                estado_tramite, fecha_concesion, numero_disposicion, fecha_vencimiento_marca
            ) VALUES (
                %(acta)s, NULL, %(clase)s, %(tipo)s, %(denominacion)s, %(denominacion)s,
                %(fecha_presentacion)s, %(titular)s, 'AR', %(cuit)s, NULL, %(link)s,
                NULL, NULL, true,
                %(estado_tramite)s, %(fecha_concesion)s, %(numero_disposicion)s, %(fecha_vencimiento_marca)s
            )
            ON CONFLICT (acta) DO UPDATE SET
                titular = EXCLUDED.titular,
                estado_tramite = EXCLUDED.estado_tramite,
                fecha_concesion = EXCLUDED.fecha_concesion,
                numero_disposicion = EXCLUDED.numero_disposicion,
                fecha_vencimiento_marca = EXCLUDED.fecha_vencimiento_marca,
                actualizado_en = now()
            """,
            {
                "acta": acta, "clase": clase, "tipo": tipo, "denominacion": denominacion,
                "fecha_presentacion": fecha_presentacion,
                # Placeholder explícito solo si no se pudo ubicar el nombre
                # real (ver regex de NOMBRE arriba) — mejor esto que dejarlo
                # vacío sin explicación en el panel.
                "titular": titular_nombre or "(titular a confirmar manualmente)",
                "cuit": cuit, "link": f"{BASE}/MarcasConsultas/Resultado?acta={acta}",
                "estado_tramite": resolucion["estado_tramite"],
                "fecha_concesion": resolucion["fecha_concesion"],
                "numero_disposicion": resolucion["numero_disposicion"],
                "fecha_vencimiento_marca": resolucion["fecha_vencimiento_marca"],
            },
        )
    conn.commit()
    conn.close()
    print(f"::notice::acta {acta} insertada/actualizada en la base OK")


if __name__ == "__main__":
    main()
