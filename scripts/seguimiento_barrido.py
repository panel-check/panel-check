"""
Vigilancia de expediente: barrido diario (13/11/2026). Todavía NO manda mails.

Lee en el INPI el expediente de cada acta con seguimiento activo y guarda:
  - la ubicación actual (sector + fecha) y, si cambió, un evento para la línea de tiempo;
  - el corte del seguimiento si la marca se concedió, se denegó o desistió, o si se sumó un apoderado.
Los eventos quedan marcados como «pendiente» (mail que habrá que mandar), «alerta» (lo ve Pame en el
panel) o «no_corresponde». El envío de mails es otra etapa.

Con --inscribir-prueba N suma N titulares de prueba (leads pre-boletín recientes, sin apoderado): se siguen
igual que los reales, pero nunca reciben mails. Sirven para validar el barrido con actas reales.

El log de Actions es público: solo se imprimen actas, sectores y eventos (nunca titulares ni mails).

Uso:
    DATABASE_URL=... python3 seguimiento_barrido.py [--inscribir-prueba 50] [--limite 500] [--max-minutes 25]
"""

import argparse
import os
import secrets
import sys
import time

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import inpi_lead  # noqa: E402
import seguimiento_expediente as se  # noqa: E402

import monitor_bloqueo  # noqa: E402
from registro import registrar  # noqa: E402
from validar_leads import crear_sesion  # noqa: E402


def inscribir_prueba(conn, n: int) -> int:
    """Suma hasta n titulares de prueba con todas sus actas pre-boletín recientes sin apoderado. Devuelve
    cuántos titulares se sumaron. La clave del titular sale de crm_claves (la misma del CRM) o, si no está,
    del CUIT o del acta."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT to_regclass('crm_claves') IS NOT NULL AS hay")
        hay_claves = cur.fetchone()["hay"]
        clave_sql = ("COALESCE(c.clave, 'cuit:' || NULLIF(trim(m.cuit), ''), 'acta:' || m.acta)" if hay_claves
                     else "COALESCE('cuit:' || NULLIF(trim(m.cuit), ''), 'acta:' || m.acta)")
        join = "LEFT JOIN crm_claves c ON c.acta = m.acta" if hay_claves else ""
        cur.execute(f"""
            WITH candidatas AS (
                SELECT m.acta, {clave_sql} AS clave, m.email
                FROM marcas m {join}
                WHERE m.es_lead IS TRUE AND m.boletin IS NULL
                  AND m.fecha_presentacion >= now() - interval '10 days'
                  AND NOT EXISTS (SELECT 1 FROM seguimiento_actas s WHERE s.acta = m.acta)
            ),
            titulares AS (
                SELECT clave FROM candidatas
                WHERE NOT EXISTS (SELECT 1 FROM seguimiento_titulares t WHERE t.clave = candidatas.clave)
                GROUP BY clave ORDER BY random() LIMIT %s
            )
            SELECT c.clave, c.acta, c.email FROM candidatas c JOIN titulares USING (clave)
        """, (n,))
        filas = cur.fetchall()
        por_titular = {}
        for f in filas:
            por_titular.setdefault(f["clave"], []).append(f)
        for clave, actas in por_titular.items():
            email = next((a["email"] for a in actas if a["email"]), None)
            cur.execute("""INSERT INTO seguimiento_titulares (clave, email, token, estado, prueba)
                           VALUES (%s, %s, %s, 'prueba', true) RETURNING id""",
                        (clave, email, secrets.token_urlsafe(16)))
            tid = cur.fetchone()["id"]
            for a in actas:
                cur.execute("INSERT INTO seguimiento_actas (acta, titular_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (a["acta"], tid))
    conn.commit()
    print(f"Inscriptos de prueba: {len(por_titular)} titulares, {len(filas)} actas", flush=True)
    return len(por_titular)


def a_revisar(conn, limite: int):
    """Actas activas de titulares de prueba o que aceptaron, las que hace más tiempo no se miran primero.
    Una misma acta no se revisa dos veces el mismo día (hora de Argentina)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT s.acta, s.ubicacion_actual, s.ubicacion_fecha, t.prueba
            FROM seguimiento_actas s JOIN seguimiento_titulares t ON t.id = s.titular_id
            WHERE s.activo AND t.estado IN ('prueba', 'activo')
              AND (s.revisado_en IS NULL
                   OR (s.revisado_en AT TIME ZONE 'America/Argentina/Buenos_Aires')::date
                      < (now() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date)
            ORDER BY s.revisado_en ASC NULLS FIRST
            LIMIT %s""", (limite,))
        return cur.fetchall()


def guardar(conn, acta: str, cambios: dict, eventos: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute("""UPDATE seguimiento_actas SET ubicacion_actual = %s, ubicacion_fecha = %s, sector = %s,
                           estado_tramite = %s, revisado_en = now(),
                           activo = %s, motivo_fin = COALESCE(%s, motivo_fin),
                           finalizado_en = CASE WHEN %s THEN finalizado_en ELSE COALESCE(finalizado_en, now()) END
                       WHERE acta = %s""",
                    (cambios["ubicacion_actual"], cambios["ubicacion_fecha"], cambios["sector"],
                     cambios["estado_tramite"], cambios.get("activo", True), cambios.get("motivo_fin"),
                     cambios.get("activo", True), acta))
        for e in eventos:
            cur.execute("""INSERT INTO seguimiento_eventos (acta, tipo, sector, ubicacion, ubicacion_fecha, detalle, aviso)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (acta, e["tipo"], e["sector"], e["ubicacion"], e["ubicacion_fecha"], e["detalle"], e["aviso"]))
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inscribir-prueba", type=int, default=0)
    ap.add_argument("--limite", type=int, default=500)
    ap.add_argument("--max-minutes", type=float, default=25)
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        se.asegurar_tablas(cur)
    conn.commit()
    if args.inscribir_prueba:
        inscribir_prueba(conn, args.inscribir_prueba)

    actas = a_revisar(conn, args.limite)
    print(f"Actas a revisar hoy: {len(actas)}", flush=True)
    m = {"revisadas": 0, "cambios_ubicacion": 0, "finalizadas": 0, "sectores_desconocidos": 0,
         "mails_pendientes": 0, "bloqueos": 0, "errores": 0}
    s = crear_sesion() if actas else None
    inicio = time.time()
    for i, a in enumerate(actas, 1):
        if (time.time() - inicio) / 60 >= args.max_minutes:
            print("Se agotó el tiempo: las que faltan quedan para la próxima corrida", flush=True)
            break
        if monitor_bloqueo.debe_cortar():
            print("INPI bloqueó consultas seguidas: se corta", flush=True)
            break
        exp = inpi_lead.consultar_expediente(a["acta"], s=s, con_grilla=False)
        if exp.get("estado_consulta") != "ok":
            m["bloqueos" if exp.get("estado_consulta") == "bloqueado" else "errores"] += 1
            print(f"  [{i}/{len(actas)}] acta {a['acta']}: {exp.get('estado_consulta')}", flush=True)
            time.sleep(args.delay)
            continue
        cambios, eventos = se.evaluar(a, exp, prueba=a["prueba"])
        guardar(conn, a["acta"], cambios, eventos)
        m["revisadas"] += 1
        m["cambios_ubicacion"] += sum(e["tipo"] == "ubicacion" for e in eventos)
        m["finalizadas"] += int(cambios.get("activo") is False)
        m["sectores_desconocidos"] += int(bool(cambios["ubicacion_actual"]) and not cambios["sector"])
        m["mails_pendientes"] += sum(e["aviso"] == "pendiente" for e in eventos)
        txt = ", ".join(f"{e['tipo']}→{e['aviso']}" for e in eventos) or "sin cambios"
        print(f"  [{i}/{len(actas)}] acta {a['acta']}: {se.limpiar(cambios['ubicacion_actual']) or '—'} "
              f"({cambios['sector'] or 'desconocido'}) | {txt}", flush=True)
        time.sleep(args.delay)

    print("Resumen: " + ", ".join(f"{k}={v}" for k, v in m.items()), flush=True)
    registrar("seguimiento_expediente.yml", m, conn=conn)
    conn.close()


if __name__ == "__main__":
    main()
