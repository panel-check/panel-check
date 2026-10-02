"""
Seguimiento del estado del trámite (Concedida/Denegada/etc.) y de las fechas
clave del ciclo de vida de la marca, una vez publicada.

Por qué es un paso separado (mismo criterio que revisar_oposiciones.py): el
pipeline principal carga la marca apenas se publica el boletín, cuando el
trámite recién empieza (examen de forma / publicación / ventana de
oposición). La Resolución (Concedida/Denegada) suele demorar varios meses
más. Este script corre solo, con su propio cron, y va revisando periódica-
mente los expedientes que todavía no tienen una resolución firme.

Qué guarda (sección RESOLUCIÓN del expediente, ver parsear_resolucion en
validar_leads.py):
  - estado_tramite: "Concedida" / "Denegada" / lo que diga TIPO:.
  - fecha_concesion: DISPOSICION: Fecha: — la antigüedad de la marca para
    los plazos legales (declaración jurada de uso a los 5 años, renovación
    a los 10) se cuenta desde ACÁ, no desde fecha_presentacion.
  - numero_disposicion: DISPOSICION: Numero: — para citar el acto administra-
    tivo si hace falta.
  - fecha_vencimiento_marca: VENCE: — INPI ya la calcula (concesión + 10
    años), así que se guarda tal cual en vez de recalcularla.

Alcance: todas las marcas (es_lead true o false) que todavía no tienen un
estado_tramite final ("Concedida"/"Denegada"). Una vez que llega a uno de
esos dos estados, se deja de revisar (no vuelve a cambiar).

Uso:
    DATABASE_URL=... python3 revisar_estado.py
    DATABASE_URL=... python3 revisar_estado.py --limit 50 --delay 1.5
"""

import argparse
import os
import sys
import time
import traceback

import psycopg2

import monitor_bloqueo
from registro import registrar
import psycopg2.extras

from validar_leads import consultar_resolucion, crear_sesion

ESTADOS_FINALES = ("Concedida", "Denegada")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="tope de actas a revisar en esta corrida")
    ap.add_argument("--max-minutes", type=float, default=None, help="tope de tiempo de la corrida; lo que falte queda para la próxima")
    ap.add_argument("--delay", type=float, default=1.5, help="segundos entre acta y acta")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT acta, titular FROM marcas
                WHERE estado_tramite IS NULL OR estado_tramite NOT IN %s
                ORDER BY fecha_presentacion
                """,
                (ESTADOS_FINALES,),
            )
            pendientes = cur.fetchall()

        if args.limit:
            pendientes = pendientes[: args.limit]
        print(f"Marcas sin resolución firme a revisar: {len(pendientes)}")

        s = crear_sesion()
        concedidas = 0
        denegadas = 0
        sin_resolucion_aun = 0
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
            info = consultar_resolucion(s, acta)

            if info["bloqueado"]:
                bloqueadas += 1
                print(f"  [{i}/{len(pendientes)}] acta {acta}: bloqueado por el WAF de INPI, se reintenta la próxima corrida")
                # commit "vacío": no cambió nada, pero cierra la transacción
                # abierta por el SELECT inicial. Sin esto, una corrida sin
                # límite (la mayoría de las actas siguen "sin resolución")
                # puede mantener esa transacción abierta minutos u horas,
                # y si mientras tanto algo pide un lock exclusivo sobre
                # marcas (ej. la migración de arranque del panel), se pone
                # en cola detrás — y con eso, TODAS las consultas nuevas
                # sobre marcas quedan encoladas también, aunque en teoría
                # sean compatibles entre sí (Postgres respeta el orden de
                # pedido de locks). Ver incidente del 29/09/2026 en el manual.
                conn.commit()
                time.sleep(args.delay)
                continue

            if not info["estado_tramite"]:
                sin_resolucion_aun += 1
                print(f"  [{i}/{len(pendientes)}] acta {acta}: todavía sin RESOLUCIÓN")
                conn.commit()  # ídem: cerrar la transacción aunque no haya cambios
                time.sleep(args.delay)
                continue

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE marcas
                    SET estado_tramite = %s, fecha_concesion = %s,
                        numero_disposicion = %s, fecha_vencimiento_marca = %s,
                        actualizado_en = now()
                    WHERE acta = %s
                    """,
                    (
                        info["estado_tramite"], info["fecha_concesion"],
                        info["numero_disposicion"], info["fecha_vencimiento_marca"],
                        acta,
                    ),
                )
            conn.commit()

            if info["estado_tramite"] == "Concedida":
                concedidas += 1
            elif info["estado_tramite"] == "Denegada":
                denegadas += 1
            # No se imprime el titular: dato personal de terceros que no
            # debe quedar en los logs (repo público). Se sigue guardando en
            # la base (UPDATE de arriba) sin cambios.
            print(f"  [{i}/{len(pendientes)}] acta {acta}: {info['estado_tramite']}"
                  f" (concesión {info['fecha_concesion']}, vence {info['fecha_vencimiento_marca']})")

            time.sleep(args.delay)

        resumen = (
            f"Revisadas: {len(pendientes)}. Concedidas: {concedidas}. Denegadas: {denegadas}. "
            f"Todavía sin resolución: {sin_resolucion_aun}. Bloqueadas (se reintentan): {bloqueadas}."
        )
        # ::notice:: además de stdout, para poder confirmar el resultado de
        # una corrida vieja por la API de GitHub sin bajar el log completo
        # (mismo truco que en backfill_fecha_publicacion.py — ver ese
        # archivo para el motivo: Azure Blob Storage está bloqueado acá).
        print(f"::notice::{resumen}")
        print(f"\n{resumen}")
        registrar("revisar_estado.yml", {
            "revisadas": len(pendientes), "concedidas": concedidas, "denegadas": denegadas,
            "sin_resolucion": sin_resolucion_aun, "bloqueos": bloqueadas,
        }, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        linea = f"{type(e).__name__}: {e}".replace("\n", " ")
        print(f"::error::revisar_estado falló: {linea}")
        traceback.print_exc()
        sys.exit(1)
