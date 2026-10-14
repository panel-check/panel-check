"""Ajuste retroactivo de una sola vez (14/10/2026): deshace «tiene
gestor/apoderado» (a mano o automático) en las marcas a las que HOY ya se
les mandó, por Resend, el mail «Recibió una oposición» (prospecto_oposicion) —
antes de que el panel lo hiciera solo (ver _crm_registrar_mail en app.py,
PR #119). No hace falta correrlo de nuevo: de acá en adelante cada mail
mandado desde el panel ya se deshace solo en el momento.

No se puede correr desde el contenedor de Claude en la nube (no hay
DATABASE_URL ahí: ver CLAUDE.md / memoria del proyecto), así que corre como
workflow de GitHub Actions, igual que revisar_oposiciones.py y los demás
scripts de backfill_*.

Uso:
    DATABASE_URL=... python3 backfill_gestor_tras_mail_oposicion.py [--dry-run]
"""
import argparse
import os

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="solo mostrar qué haría, sin escribir nada")
    args = ap.parse_args()

    if not DATABASE_URL:
        raise RuntimeError("Falta la variable de entorno DATABASE_URL")

    conn = psycopg2.connect(DATABASE_URL, options="-c jit=off")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT DISTINCT me.acta, cc.clave
                FROM mails_envios me
                LEFT JOIN crm_claves cc ON cc.acta = me.acta
                WHERE me.clave_mail = 'prospecto_oposicion' AND NOT me.es_prueba
                  AND (me.enviado_en AT TIME ZONE 'America/Argentina/Buenos_Aires')::date
                      = (now() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date
                ORDER BY me.acta
                """
            )
            envios = cur.fetchall()
        print(f"Actas con «Recibió una oposición» mandado hoy por Resend: {len(envios)}")

        vueltos_manual = vueltos_auto = 0
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for e in envios:
                acta, clave = e["acta"], e["clave"]
                cur.execute(
                    "SELECT con_gestor_manual, representacion_posterior_oposicion FROM marcas WHERE acta = %s",
                    (acta,),
                )
                fila = cur.fetchone()
                if not fila:
                    continue
                if fila["con_gestor_manual"]:
                    print(f"  acta {acta}: tenía «gestor/apoderado» marcado a mano -> vuelve a lead")
                    if not args.dry_run:
                        cur.execute(
                            """
                            UPDATE marcas
                            SET es_lead = true, caracter = caracter_previo,
                                lead_score = COALESCE(lead_score_previo, lead_score),
                                caracter_previo = NULL, lead_score_previo = NULL,
                                con_gestor_manual = NULL, con_gestor_manual_en = NULL,
                                con_gestor_manual_por = NULL, gestor_nombre = NULL,
                                actualizado_en = now()
                            WHERE acta = %s AND con_gestor_manual IS TRUE
                            """,
                            (acta,),
                        )
                        if cur.rowcount and clave:
                            cur.execute(
                                "INSERT INTO crm_actividad (clave, autor, tipo, texto) VALUES (%s, %s, %s, %s)",
                                (clave, "sistema", "sistema",
                                 "Se mandó un mail de oposición: se deshace «tiene gestor/apoderado» (ajuste "
                                 "retroactivo del 14/10/2026) y vuelve a ser lead."),
                            )
                    vueltos_manual += 1
                elif fila["representacion_posterior_oposicion"]:
                    print(f"  acta {acta}: tenía el pase automático a «con agente» -> vuelve a lead")
                    if not args.dry_run:
                        cur.execute(
                            """
                            UPDATE marcas
                            SET es_lead = true,
                                caracter = '',
                                representacion_posterior_oposicion = false,
                                detalle_representacion_posterior = NULL,
                                lead_score = (CASE WHEN TRIM(COALESCE(matricula_agente, '')) IN ('', 'Part.') THEN 50 ELSE 0 END)
                                             + 50 + (CASE WHEN COALESCE(email, '') <> '' THEN 20 ELSE 0 END),
                                actualizado_en = now()
                            WHERE acta = %s AND es_lead IS FALSE
                              AND representacion_posterior_oposicion IS TRUE AND con_gestor_manual IS NOT TRUE
                            """,
                            (acta,),
                        )
                        if cur.rowcount and clave:
                            cur.execute(
                                "INSERT INTO crm_actividad (clave, autor, tipo, texto) VALUES (%s, %s, %s, %s)",
                                (clave, "sistema", "sistema",
                                 "Se mandó un mail de oposición: se deshace el pase automático a «con agente» "
                                 "(ajuste retroactivo del 14/10/2026) y vuelve a ser lead."),
                            )
                    vueltos_auto += 1
                else:
                    print(f"  acta {acta}: no estaba marcada como «con agente», no hay nada para deshacer")
        if not args.dry_run:
            conn.commit()
        print(f"\nVolvieron a ser lead: {vueltos_manual} marcados a mano + {vueltos_auto} automáticos"
              + (" (dry-run: no se escribió nada)" if args.dry_run else ""))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
