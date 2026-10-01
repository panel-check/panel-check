"""
Paso 6 (nuevo, corre por separado del pipeline principal) — Reviso, para los
leads reales (sin agente/apoderado) cuya publicación en el boletín ya tiene
al menos N días, si en su Grilla Digital apareció una oposición de tercero o
una vista de oficio de INPI durante la ventana de 30 días para oponerse.

Por qué es un paso separado: el pipeline principal (parse_boletin.py ->
completar_mixtas.py -> validar_leads.py -> cargar_db.py) corre apenas se
publica un boletín nuevo, cuando todavía no pasó tiempo suficiente para que
haya oposiciones. Este script corre solo (con su propio cron en GitHub
Actions) y mira boletines de semanas anteriores, no el más reciente.

Cómo se detecta: reutiliza la misma llamada a Grilla Digital que ya hacía
validar_leads.py para buscar el email (Home/GrillaDigital + GrillaDigitales),
sin pedir nada nuevo a INPI. Ahí:
  - la fila con Indice == "Hoja Publicacion" da la fecha real de publicación
    de ESE expediente (se guarda en fecha_publicacion la primera vez que se
    ve, en validar_leads.py — este script solo la LEE para decidir a quién
    revisar, no la vuelve a buscar).
  - si aparece una fila nueva con "OPO"/"VISTA"/"OPOSICION" en Indice o
    Referencia (ver TERMINOS_OPOSICION en validar_leads.py), es una
    oposición de tercero o una vista — ejemplo real confirmado a mano:
    Indice="Recibo de Ingreso", Referencia="Opo. de Marcas".

Marca cada acta revisada con revisado_oposicion_en = now(), así una acta se
chequea una sola vez (no todos los días para siempre). Si algún día INPI
tarda en resolver la oposición y hay que revisar de nuevo más adelante, por
ahora se puede resetear esa columna a mano.

Modo --parcial (revisión anticipada): mira los leads cuyo plazo de oposición
todavía está ABIERTO (publicados hace menos de --dias días). Una oposición
puede entrar desde el día de la publicación y ya figura en la Grilla Digital,
aunque el titular recién se entera cuando cierra el plazo; detectarla antes
nos deja contactarlo antes que INPI. Diferencias con la revisión normal:
  - NUNCA marca revisado_oposicion_en: la revisión definitiva del día 33
    sigue corriendo igual después, aunque la parcial haya dado "sin oposición".
  - Guarda revisado_parcial_en = now() en cada acta y, en la próxima corrida
    parcial, revisa primero las que hace más que no se miran (--min-horas
    evita volver a mirar una acta revisada hace poco).
  - Las que ya tienen oposición detectada no se vuelven a mirar en parcial
    (el seguimiento de representación posterior lo hace la revisión normal).
Si detecta una oposición, la guarda igual que la revisión normal, así el
aviso por mail (notificar_oposiciones.py) la toma como cualquier otra.

Uso:
    DATABASE_URL=... python3 revisar_oposiciones.py
    DATABASE_URL=... python3 revisar_oposiciones.py --dias 33 --delay 1.5
    DATABASE_URL=... python3 revisar_oposiciones.py --parcial

Alcance: solo es_lead = true (particulares/empresas sin agente ni apoderado).
Las que ya tienen agente quedan afuera a propósito — ese trámite lo maneja
su propio apoderado.
"""

import argparse
import os
import sys
import time
from datetime import date, timedelta

import psycopg2

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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dias", type=int, default=33,
        help="revisar leads cuya fecha_publicacion tenga al menos esta antigüedad (default 33, "
             "cubre la ventana de oposición de 30 días con margen)",
    )
    ap.add_argument("--delay", type=float, default=1.5, help="segundos entre acta y acta (freno de mano)")
    ap.add_argument("--limit", type=int, default=None, help="tope de actas a revisar, útil para pruebas")
    ap.add_argument(
        "--parcial", action="store_true",
        help="revisión anticipada de leads con el plazo de oposición todavía abierto "
             "(publicados hace menos de --dias días); no marca revisado_oposicion_en",
    )
    ap.add_argument(
        "--min-horas", type=float, default=20.0,
        help="solo con --parcial: no volver a mirar una acta revisada en parcial hace menos de estas horas",
    )
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")

    corte = date.today() - timedelta(days=args.dias)

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("ALTER TABLE marcas ADD COLUMN IF NOT EXISTS revisado_parcial_en TIMESTAMPTZ")
            conn.commit()
            if args.parcial:
                # Plazo abierto: publicados DESPUÉS del corte (hace menos de N días).
                # Las que ya tienen oposición detectada no se miran de nuevo.
                cur.execute(
                    """
                    SELECT acta, titular, fecha_publicacion
                    FROM marcas
                    WHERE es_lead = true
                      AND fecha_publicacion IS NOT NULL
                      AND fecha_publicacion > %s
                      AND tuvo_oposicion IS NOT TRUE
                      AND (revisado_parcial_en IS NULL
                           OR revisado_parcial_en < now() - %s * interval '1 hour')
                    ORDER BY revisado_parcial_en NULLS FIRST, fecha_publicacion
                    """,
                    (corte, args.min_horas),
                )
            else:
                cur.execute(
                    """
                    SELECT acta, titular, fecha_publicacion
                    FROM marcas
                    WHERE es_lead = true
                      AND fecha_publicacion IS NOT NULL
                      AND fecha_publicacion <= %s
                      AND (
                        revisado_oposicion_en IS NULL
                        OR (tuvo_oposicion = true AND representacion_posterior_oposicion IS NOT TRUE)
                      )
                    ORDER BY fecha_publicacion
                    """,
                    (corte,),
                )
            pendientes = cur.fetchall()

        if args.limit:
            pendientes = pendientes[: args.limit]

        if args.parcial:
            print(f"[PARCIAL] Leads con plazo abierto (publicación > {corte.isoformat()}, < {args.dias} días) a revisar: {len(pendientes)}")
        else:
            print(f"Leads con publicación <= {corte.isoformat()} (>= {args.dias} días) sin revisar todavía: {len(pendientes)}")

        s = crear_sesion()
        con_oposicion = 0
        for i, fila in enumerate(pendientes, 1):
            acta = fila["acta"]
            archivos = buscar_archivos_grilla(s, acta)
            if not archivos:
                # bloqueo del WAF u otro fallo de red: no marcamos como revisado,
                # para que la próxima corrida lo vuelva a intentar.
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
            if fila_opo and "OPO" in (fila_opo.get("Referencia") or "").upper():
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

            # En parcial el plazo sigue abierto: nunca se da por revisada.
            if args.parcial:
                revisado_en = None
            else:
                revisado_en = "now()" if (not tuvo_oposicion or representacion_posterior) else None

            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE marcas
                    SET tuvo_oposicion = %s, detalle_oposicion = %s,
                        revisado_oposicion_en = {revisado_en or 'revisado_oposicion_en'},
                        oponente_nombre = %s, oponente_tipo_doc = %s,
                        oponente_numero_doc = %s, oponente_cuit = %s,
                        fundamento_oposicion = %s,
                        actas_marca_oponente = %s, marca_oponente_denominacion = %s,
                        marca_oponente_numero_registro = %s,
                        representacion_posterior_oposicion = %s,
                        detalle_representacion_posterior = %s,
                        revisado_parcial_en = {"now()" if args.parcial else "revisado_parcial_en"}
                    WHERE acta = %s
                    """,
                    (
                        tuvo_oposicion, detalle or None,
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

        print(f"\nRevisadas: {len(pendientes)}. Con oposición/vista detectada: {con_oposicion}.")
        if not args.parcial:
            # La parcial no se registra: el registro/tarjeta "Oposiciones y vistas"
            # del panel cuenta las revisiones definitivas del día 33.
            registrar("revisar_oposiciones.yml", {
                "revisadas": len(pendientes), "con_oposicion": con_oposicion,
                "sin_oposicion": len(pendientes) - con_oposicion,
            }, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
