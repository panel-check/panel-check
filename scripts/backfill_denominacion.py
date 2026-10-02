"""
Completa el nombre de las marcas que en el panel aparecen como "(mixta/fig.)"
(sin `denominacion` ni `denominacion_inpi`).

Casos que cubre:
  - Actas del escaneo directo (fuente='escaneo_directo') guardadas antes del
    arreglo del 30/09/2026: el nombre solo se leía del Formulario, y solo si
    ese Formulario traía EMAIL -> las que no tenían email (o tenían el campo
    DENOMINACION vacío en el PDF) quedaban sin nombre.
  - Mixtas/Figurativas de boletín que el webservice de INPI no devolvió en su
    momento (las "amarillas" del proceso original, ~5-10 % por boletín).

Para cada una, en orden:
  1. Ficha del expediente (POST /MarcasConsultas/Resultado, sección DATOS
     GENERALES) -> denominación, tipo y fecha de presentación.
  2. Si la ficha no trae texto: webservice SOAP ConsultaCuitOTitular por CUIT.
Una Figurativa pura (logo sin texto) no tiene nombre en ningún lado: una vez
que quedó guardada con tipo F no se vuelve a consultar (el panel la muestra
como "(figurativa, sin texto)").

Dónde se guarda: escaneo directo -> `denominacion` (lo que el boletín deja
vacío en M/F; cargar_db.py no lo pisa con NULL). Boletín -> `denominacion_inpi`
(la columna que ya usa el panel para M/F). Nunca pisa un nombre existente.

Se dispara solo:
  - al final de cada "Escanear actas nuevas" y de cada "Pipeline de boletines"
    (si en esa corrida quedó alguna marca sin nombre, se busca en el momento;
    si no quedó ninguna, termina al instante sin consultar INPI);
  - además, como red de seguridad, 3 veces por día por su cuenta
    (workflow backfill_denominacion.yml), y a mano desde Automatizaciones.

Uso:
    DATABASE_URL=... python3 backfill_denominacion.py [--limit N] [--delay 1.5] [--incluir-no-leads]
"""

import argparse
import os
import sys
import time
import traceback

import psycopg2
import psycopg2.extras

import monitor_bloqueo
from registro import registrar

from validar_leads import (
    BASE, RE_CUIT_SPAN, _get_con_reintentos, crear_sesion,
    datos_generales_de_pagina, denominacion_por_webservice,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="máximo de actas a revisar en esta corrida")
    ap.add_argument("--max-minutes", type=float, default=None, help="tope de tiempo de la corrida; lo que falte queda para la próxima")
    ap.add_argument("--delay", type=float, default=1.5, help="segundos de espera entre actas")
    ap.add_argument("--incluir-no-leads", action="store_true",
                    help="también marcas con apoderado (por defecto solo leads / sin verificar)")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    conn = psycopg2.connect(dsn)
    filtro_lead = "" if args.incluir_no_leads else "AND es_lead IS NOT FALSE"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT acta, cuit, fuente, boletin
            FROM marcas
            WHERE COALESCE(NULLIF(TRIM(denominacion), ''), NULLIF(TRIM(denominacion_inpi), '')) IS NULL
              {filtro_lead}
              -- Figurativa pura ya confirmada: no tiene texto, no se reintenta.
              AND COALESCE(tipo, '') <> 'F'
            ORDER BY (fuente = 'escaneo_directo') DESC, acta DESC
            """
        )
        pendientes = cur.fetchall()
    conn.commit()

    if args.limit:
        pendientes = pendientes[: args.limit]
    print(f"Marcas sin nombre a revisar: {len(pendientes)}")
    if not pendientes:
        print("::notice::No hay marcas sin nombre pendientes.")
        registrar("backfill_denominacion.yml", {"revisadas": 0, "nombres_recuperados": 0}, conn)
        conn.close()
        return

    s = crear_sesion()
    completadas = 0
    sin_texto = 0
    sin_texto_por_tipo: dict[str, list[str]] = {}
    bloqueadas = 0

    inicio_corrida = time.time()
    for i, fila in enumerate(pendientes, 1):
        if args.max_minutes and (time.time() - inicio_corrida) / 60 >= args.max_minutes:
            print(f"Se llegó al tope de {args.max_minutes:g} minutos: el resto sigue en la próxima corrida.")
            break
        if monitor_bloqueo.debe_cortar():
            print("Se corta la corrida por bloqueos seguidos de INPI: el resto sigue en la próxima.")
            break
        acta = fila["acta"]
        try:
            r = _get_con_reintentos(
                lambda: s.post(
                    f"{BASE}/MarcasConsultas/Resultado",
                    headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
                    data={"acta": acta},
                    timeout=30,
                )
            )
        except Exception as e:
            print(f"  [{i}/{len(pendientes)}] acta {acta}: error de conexión ({e})")
            bloqueadas += 1
            time.sleep(args.delay)
            continue

        if "Web Page Blocked" in r.text or "Attack ID" in r.text:
            bloqueadas += 1
            print(f"  [{i}/{len(pendientes)}] acta {acta}: bloqueado por el WAF, queda para la próxima")
            if bloqueadas >= 5 and completadas == 0:
                print("  5 bloqueos sin ningún éxito -- frenando esta corrida")
                break
            time.sleep(args.delay)
            continue

        dg = datos_generales_de_pagina(r.text)
        cuit = fila["cuit"]
        if not cuit:
            m_cuit = RE_CUIT_SPAN.search(r.text)
            if m_cuit:
                c = "".join(ch for ch in m_cuit.group(1) if ch.isdigit())
                cuit = c if len(c) in (10, 11) else None

        nombre = dg["denominacion"]
        origen = "ficha"
        if not nombre:
            nombre = denominacion_por_webservice(cuit, acta)
            origen = "webservice"

        columna = "denominacion" if fila["fuente"] == "escaneo_directo" or not fila["boletin"] else "denominacion_inpi"
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE marcas
                SET {columna} = COALESCE(NULLIF(TRIM({columna}), ''), %s),
                    tipo = COALESCE(NULLIF(tipo, ''), %s),
                    fecha_presentacion = COALESCE(fecha_presentacion, %s),
                    cuit = COALESCE(cuit, %s),
                    actualizado_en = now()
                WHERE acta = %s
                """,
                (nombre, dg["tipo"], dg["fecha_presentacion"], cuit, acta),
            )
        conn.commit()

        if nombre:
            completadas += 1
            print(f"  [{i}/{len(pendientes)}] acta {acta}: nombre recuperado ({origen}, tipo {dg['tipo'] or '?'})")
        else:
            sin_texto += 1
            sin_texto_por_tipo.setdefault(dg["tipo"] or "?", []).append(acta)
            print(f"  [{i}/{len(pendientes)}] acta {acta}: sin texto en ficha ni webservice (tipo {dg['tipo'] or '?'})")
        time.sleep(args.delay)

    registrar("backfill_denominacion.yml", {
        "revisadas": len(pendientes), "nombres_recuperados": completadas,
        "figurativas_sin_texto": sin_texto_por_tipo.get("F", []).__len__(),
        "siguen": sin_texto - len(sin_texto_por_tipo.get("F", [])), "bloqueos": bloqueadas,
    }, conn)
    conn.close()
    resumen = (f"Revisadas: {len(pendientes)}. Nombre recuperado: {completadas}. "
               f"Sin texto (figurativa pura o todavía no cargada): {sin_texto}. Bloqueadas/error: {bloqueadas}.")
    print(f"::notice::{resumen}")
    # Desglose por tipo (los logs de Actions no siempre se pueden leer; las
    # annotations sí). "?" = la ficha no trajo DATOS GENERALES reconocibles
    # -> revisar con "Inspeccionar acta" una de las actas listadas.
    for tipo, actas in sorted(sin_texto_por_tipo.items()):
        print(f"::notice::Sin nombre, tipo {tipo}: {len(actas)} -- ej.: {', '.join(actas[:8])}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"::error::backfill_denominacion falló: {type(e).__name__}: {e}".replace("\n", " "))
        traceback.print_exc()
        sys.exit(1)
