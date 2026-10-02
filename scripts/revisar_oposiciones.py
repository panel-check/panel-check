"""
Paso 6 (corre por separado del pipeline principal) — Reviso, para los leads
reales (sin agente/apoderado) ya publicados en el boletín, si en su Grilla
Digital apareció una oposición de tercero o una vista de oficio de INPI
durante la ventana de 30 días para oponerse.

Esquema de revisión (días contados desde fecha_publicacion de ESE expediente):
  - HITOS: a los 10, 23 y 33 días se hace una revisión de cada lead. Muchas
    oposiciones llegan antes de los 30 días y conviene saberlo con tiempo.
  - Si en alguna revisión aparece una oposición y todavía no hay apoderado/
    gestor posterior, se vuelve a mirar TODOS LOS DÍAS hasta el día 33 y cada
    3 días hasta el día 60 (por si el titular suma un representante).
  - Contadores en marcas: opo_chequeos (0-3, hitos cumplidos),
    opo_ultimo_chequeo_en, oposicion_detectada_en (primera vez que se vio).
  - Los leads ya publicados cuando se puso en marcha este esquema se ponen al
    día con UNA pasada (workflow manual "Oposiciones: puesta al día"): para
    ellos cuenta el hito que les corresponde por edad. Si una corrida se corta
    por tiempo o por bloqueos, lo que falta queda pendiente y se retoma solo.

Cómo se detecta: reutiliza la misma llamada a Grilla Digital que ya hacía
validar_leads.py para buscar el email (Home/GrillaDigital + GrillaDigitales),
sin pedir nada nuevo a INPI. Ahí:
  - la fila con Indice == "Hoja Publicacion" da la fecha real de publicación
    (se guarda en fecha_publicacion en validar_leads.py; acá solo se LEE).
  - si aparece una fila nueva con "OPO"/"VISTA"/"OPOSICION" en Indice o
    Referencia (ver TERMINOS_OPOSICION en validar_leads.py), es una
    oposición de tercero o una vista — ejemplo real confirmado a mano:
    Indice="Recibo de Ingreso", Referencia="Opo. de Marcas".

Uso:
    DATABASE_URL=... python3 revisar_oposiciones.py
    DATABASE_URL=... python3 revisar_oposiciones.py --max-minutes 90 --delay 1.5

Alcance: solo es_lead = true (particulares/empresas sin agente ni apoderado).
Las que ya tienen agente quedan afuera a propósito.
"""

import argparse
import os
import sys
import time
import psycopg2

import monitor_bloqueo
from registro import registrar
import psycopg2.extras

from validar_leads import (
    _parsear_fecha_grilla,
    buscar_archivos_grilla,
    buscar_fila_oposicion,
    buscar_fila_representacion_posterior,
    crear_sesion,
    descargar_formulario_oposicion,
    detectar_oposicion,
)


HITOS = (10, 23, 33)          # días desde la publicación en que se revisa cada lead
RECHEQUEO_DIARIO_HASTA = 33   # con oposición y sin apoderado: todos los días hasta este día...
RECHEQUEO_CADA_3_HASTA = 60   # ...y cada 3 días hasta este otro

SQL_PENDIENTES = f"""
    SELECT acta, titular, fecha_publicacion,
           (CURRENT_DATE - fecha_publicacion) AS edad,
           oponente_nombre
    FROM marcas
    WHERE es_lead = true
      AND fecha_publicacion IS NOT NULL
      AND fecha_publicacion <= CURRENT_DATE - {HITOS[0]}
      AND (
        COALESCE(opo_chequeos, 0) < 1
        OR (COALESCE(opo_chequeos, 0) < 2 AND fecha_publicacion <= CURRENT_DATE - {HITOS[1]})
        OR (COALESCE(opo_chequeos, 0) < 3 AND fecha_publicacion <= CURRENT_DATE - {HITOS[2]})
        OR (
          tuvo_oposicion IS TRUE
          AND representacion_posterior_oposicion IS NOT TRUE
          AND fecha_publicacion > CURRENT_DATE - {RECHEQUEO_CADA_3_HASTA}
          AND (
            opo_ultimo_chequeo_en IS NULL
            OR opo_ultimo_chequeo_en::date <= CURRENT_DATE - (
                 CASE WHEN fecha_publicacion > CURRENT_DATE - {RECHEQUEO_DIARIO_HASTA} THEN 1 ELSE 3 END)
          )
        )
      )
    ORDER BY CASE WHEN fecha_publicacion > CURRENT_DATE - {RECHEQUEO_CADA_3_HASTA} THEN 0 ELSE 1 END,
             fecha_publicacion DESC
"""


def hito_por_edad(edad: int) -> int:
    """Cuántos hitos (1-3) ya cumplió un lead con esta antigüedad en días."""
    return sum(1 for h in HITOS if edad >= h)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-minutes", type=float, default=None,
        help="tope de tiempo de la corrida; lo que falte queda pendiente para la próxima",
    )
    ap.add_argument("--delay", type=float, default=1.5, help="segundos entre acta y acta (freno de mano)")
    ap.add_argument("--limit", type=int, default=None, help="tope de actas a revisar, útil para pruebas")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")

    inicio = time.time()
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            for col, tipo in (("opo_chequeos", "INTEGER"), ("opo_ultimo_chequeo_en", "TIMESTAMPTZ"),
                              ("oposicion_detectada_en", "TIMESTAMPTZ")):
                cur.execute(f"ALTER TABLE marcas ADD COLUMN IF NOT EXISTS {col} {tipo}")
            # Alta inicial de los contadores: los leads que ya fueron revisados
            # con el esquema anterior (una sola revisión, a los 33+ días) cuentan
            # con los 3 hitos cumplidos; el resto arranca en 0.
            cur.execute(
                """
                UPDATE marcas
                SET opo_chequeos = CASE WHEN revisado_oposicion_en IS NOT NULL THEN 3 ELSE 0 END
                WHERE opo_chequeos IS NULL AND es_lead = true
                """
            )
        conn.commit()

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(SQL_PENDIENTES)
            pendientes = cur.fetchall()

        if args.limit:
            pendientes = pendientes[: args.limit]

        print(f"Leads con revisión de oposición pendiente (hitos {HITOS[0]}/{HITOS[1]}/{HITOS[2]} días + rechequeos): {len(pendientes)}")

        s = crear_sesion()
        con_oposicion = 0
        revisadas = 0
        sin_consulta = 0
        for i, fila in enumerate(pendientes, 1):
            if args.max_minutes and (time.time() - inicio) / 60 >= args.max_minutes:
                print(f"Se llegó al tope de {args.max_minutes:g} minutos: el resto sigue en la próxima corrida.")
                break
            if monitor_bloqueo.debe_cortar():
                print("Se corta la corrida por bloqueos seguidos de INPI: el resto sigue en la próxima.")
                break
            revisadas += 1
            edad = int(fila["edad"])
            acta = fila["acta"]
            archivos = buscar_archivos_grilla(s, acta)
            if not archivos:
                # bloqueo del WAF u otro fallo de red: no marcamos como revisado,
                # para que la próxima corrida lo vuelva a intentar.
                sin_consulta += 1
                print(f"  [{i}/{len(pendientes)}] acta {acta}: no se pudo consultar Grilla Digital, reintento en la próxima corrida")
                # commit "vacío" para cerrar la transacción del SELECT inicial
                # aunque no haya cambios — ver revisar_estado.py para el
                # motivo (incidente del 29/09/2026 en el manual).
                conn.commit()
                time.sleep(args.delay)
                continue

            fila_opo = buscar_fila_oposicion(archivos, fila["fecha_publicacion"].isoformat())
            tuvo_oposicion = fila_opo is not None
            detalle = (
                f"{fila_opo.get('Fecha', '')} - {fila_opo.get('Indice', '')} - {fila_opo.get('Referencia', '')}"
                if fila_opo else ""
            )

            # Si es una oposición de TERCERO (no una vista de oficio de
            # INPI), bajamos y parseamos el Formulario real para sacar quién
            # se opone y por qué (ver descargar_formulario_oposicion) —
            # mejor esfuerzo: si falla o no está, seguimos solo con el
            # detalle crudo de Grilla Digital, no frena la detección.
            detalle_rico = {}
            if fila_opo and not fila.get("oponente_nombre") and "OPO" in (fila_opo.get("Referencia") or "").upper():
                detalle_rico = descargar_formulario_oposicion(s, archivos, fila_opo, acta_propia=acta)

            # Si ya hay oposición, buscamos además si DESPUÉS de esa fecha
            # apareció alguien sumándose como apoderado/gestor ("Acompaña
            # Poder"/"Ratifica") -- señal de que el titular ya está
            # trabajando con alguien para responderla, así que deja de ser
            # un lead frío prioritario. Mientras esto no aparezca, NO
            # marcamos revisado_oposicion_en (ver el SELECT de arriba): se
            # sigue reintentando en corridas futuras hasta encontrarlo o
            # hasta que deje de tener sentido seguir mirando.
            representacion_posterior = None
            detalle_representacion = None
            if tuvo_oposicion:
                fecha_opo = _parsear_fecha_grilla(fila_opo.get("Fecha") or "")
                fila_rep = buscar_fila_representacion_posterior(archivos, fecha_opo)
                representacion_posterior = fila_rep is not None
                if fila_rep:
                    detalle_representacion = (
                        f"{fila_rep.get('Fecha', '')} - {fila_rep.get('Indice', '')} - "
                        f"{fila_rep.get('Referencia', '')}"
                    )

            # "revisado_oposicion_en" queda como antes: solo cuando ya no hay nada
            # más que mirar (sin oposición pasado el día 33, o con apoderado posterior).
            revisado_en = "now()" if ((not tuvo_oposicion and edad >= HITOS[-1]) or representacion_posterior) else None
            hito = hito_por_edad(edad)

            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE marcas
                    SET tuvo_oposicion = %s, detalle_oposicion = %s,
                        revisado_oposicion_en = {revisado_en or 'revisado_oposicion_en'},
                        opo_chequeos = GREATEST(COALESCE(opo_chequeos, 0), %s),
                        opo_ultimo_chequeo_en = now(),
                        oposicion_detectada_en = CASE WHEN %s THEN COALESCE(oposicion_detectada_en, now())
                                                      ELSE oposicion_detectada_en END,
                        oponente_nombre = COALESCE(%s, oponente_nombre),
                        oponente_tipo_doc = COALESCE(%s, oponente_tipo_doc),
                        oponente_numero_doc = COALESCE(%s, oponente_numero_doc),
                        oponente_cuit = COALESCE(%s, oponente_cuit),
                        fundamento_oposicion = COALESCE(%s, fundamento_oposicion),
                        actas_marca_oponente = COALESCE(%s, actas_marca_oponente),
                        marca_oponente_denominacion = COALESCE(%s, marca_oponente_denominacion),
                        marca_oponente_numero_registro = COALESCE(%s, marca_oponente_numero_registro),
                        representacion_posterior_oposicion = %s,
                        detalle_representacion_posterior = %s
                    WHERE acta = %s
                    """,
                    (
                        tuvo_oposicion, detalle or None,
                        hito, tuvo_oposicion,
                        detalle_rico.get("oponente_nombre"),
                        detalle_rico.get("oponente_tipo_doc"),
                        detalle_rico.get("oponente_numero_doc"),
                        detalle_rico.get("oponente_cuit"),
                        detalle_rico.get("fundamento_oposicion"),
                        detalle_rico.get("actas_marca_oponente"),
                        detalle_rico.get("marca_oponente_denominacion"),
                        detalle_rico.get("marca_oponente_numero_registro"),
                        representacion_posterior,
                        detalle_representacion,
                        acta,
                    ),
                )
            conn.commit()

            if tuvo_oposicion:
                con_oposicion += 1
                # No se imprime el titular ni el detalle/oponente: son datos
                # personales de terceros (nombre, CUIT/DNI, fundamento) que no
                # deben quedar en los logs de Actions (repo público). Sí se
                # siguen guardando en la base (UPDATE de arriba), sin cambios.
                extra = " (ya con apoderado/gestor posterior)" if representacion_posterior else ""
                print(f"  [{i}/{len(pendientes)}] acta {acta}: CON OPOSICIÓN/VISTA{extra}")
            else:
                print(f"  [{i}/{len(pendientes)}] acta {acta}: sin oposición")

            time.sleep(args.delay)

        print(f"\nRevisadas: {revisadas - sin_consulta} de {len(pendientes)} pendientes "
              f"({sin_consulta} sin poder consultar). Con oposición/vista detectada: {con_oposicion}.")
        registrar("revisar_oposiciones.yml", {
            "revisadas": revisadas - sin_consulta, "con_oposicion": con_oposicion,
            "sin_oposicion": revisadas - sin_consulta - con_oposicion,
            "pendientes_al_arrancar": len(pendientes),
        }, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
