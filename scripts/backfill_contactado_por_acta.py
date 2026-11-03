"""Ajuste retroactivo de una sola vez (03/11/2026): «contactado» pasa a ser POR ACTA.

Hasta ahora, cuando a un titular se le mandaba un mail por UNA de sus actas (o se marcaba una acta a mano)
y el titular pasaba de «nuevo» a «contactado» en el CRM, el panel marcaba como contactadas TODAS sus actas.
Las otras clases quedaban afuera de la revisión diaria de vistas y del rechequeo de oposiciones, así que si
después les llegaba una vista no se detectaba. Desde ahora el panel marca solo la clase contactada (ver
_sincronizar_contactado en panel/app.py); este script deshace las marcas «de rebote» que ya quedaron.

Qué desmarca (contactado = false, contactado_en = NULL): un acta contactada que
  - no tiene mail propio mandado desde el panel (mails_envios) ni fue marcada a mano por su número
    (registro del CRM «marcada como contactada el acta N»), y
  - comparte el instante exacto de contactado_en con otra acta del mismo titular que sí tiene ese origen
    (el rebote se hacía en la misma transacción, con el mismo now()), y
  - el titular no está «descartado» en el CRM (descartar sigue marcando todas sus actas).
Las actas contactadas por un arrastre en el CRM sin mail ni casilla (no se sabe de qué clase era) no se tocan.

No se puede correr desde el contenedor de Claude en la nube (no hay DATABASE_URL ahí), así que corre como
workflow de GitHub Actions (backfill_contactado_por_acta.yml). El log es público: solo imprime números de acta.

Uso:
    DATABASE_URL=... python3 backfill_contactado_por_acta.py [--dry-run]
"""
import argparse
import os

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL")

SQL_REBOTE = r"""
WITH origen AS (
    SELECT DISTINCT acta FROM mails_envios
    UNION
    SELECT DISTINCT (regexp_match(texto, 'contactada el acta (\d+)'))[1]
    FROM crm_actividad
    WHERE texto ~ 'contactada el acta \d+'
),
mk AS (
    SELECT m.acta, m.contactado_en, m.boletin, cc.clave
    FROM marcas m
    JOIN crm_claves cc ON cc.acta = m.acta
    WHERE m.contactado IS TRUE AND m.contactado_en IS NOT NULL
)
SELECT a.acta, (a.boletin IS NULL) AS pre_boletin
FROM mk a
WHERE a.acta NOT IN (SELECT acta FROM origen WHERE acta IS NOT NULL)
  AND EXISTS (
        SELECT 1 FROM mk b
        WHERE b.clave = a.clave AND b.acta <> a.acta
          AND b.contactado_en = a.contactado_en
          AND b.acta IN (SELECT acta FROM origen WHERE acta IS NOT NULL)
  )
  AND COALESCE((SELECT etapa FROM crm_leads l WHERE l.clave = a.clave), 'nuevo') <> 'descartado'
ORDER BY a.acta
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="solo mostrar qué haría, sin escribir nada")
    args = ap.parse_args()

    if not DATABASE_URL:
        raise RuntimeError("Falta la variable de entorno DATABASE_URL")

    conn = psycopg2.connect(DATABASE_URL, options="-c jit=off")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT count(*) AS n FROM marcas WHERE contactado IS TRUE")
            total = cur.fetchone()["n"]
            cur.execute(SQL_REBOTE)
            filas = cur.fetchall()
            actas = [f["acta"] for f in filas]
            pre = [f["acta"] for f in filas if f["pre_boletin"]]
            print(f"Actas contactadas hoy en la base: {total}")
            print(f"Contactadas solo de rebote (otra clase del mismo titular): {len(actas)} "
                  f"(pre-boletín: {len(pre)}, ya publicadas: {len(actas) - len(pre)})")
            for i in range(0, len(actas), 20):
                print("  " + ", ".join(actas[i:i + 20]))
            if args.dry_run:
                print("DRY-RUN: no se escribió nada.")
                conn.rollback()
                return
            if actas:
                cur.execute(
                    "UPDATE marcas SET contactado = false, contactado_en = NULL WHERE acta = ANY(%s) AND contactado IS TRUE",
                    (actas,),
                )
                print(f"Desmarcadas: {cur.rowcount}")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
