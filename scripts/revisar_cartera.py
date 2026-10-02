"""
Seguimiento de las marcas de la cartera de clientes contra INPI.

Para cada marca de un cliente activo vuelve a leer el expediente (mismo
"Resultado" que usa el resto del sistema + Grilla Digital) y guarda lo que
cambió: estado del trámite, concesión y vencimiento, oposición o vista, fecha
de publicación, cambio de titular y movimientos nuevos del expediente. Cada
cambio queda como "novedad" (Clientes → ficha del cliente) y, si corresponde,
sale en el mail diario (scripts/notificar_cartera.py).

Qué se revisa y con qué frecuencia:
  1. Marcas recién cargadas o que nunca se pudieron consultar (INPI bloqueó
     o falló al cargarlas): siempre, primero.
  2. Marcas en trámite (sin resolución firme): una vez por día.
  3. Marcas concedidas o denegadas: una vez cada 30 días (casi no cambian; se
     siguen mirando por el vencimiento y por si cambia el titular).

Una marca bloqueada por el WAF no se marca como revisada: se reintenta en la
próxima corrida. Si el WAF bloquea varias seguidas, se corta la corrida.

Sin datos personales en el log (el repo es público): solo actas y cantidades.

Uso:
    DATABASE_URL=... python3 revisar_cartera.py
    DATABASE_URL=... python3 revisar_cartera.py --max-minutes 10 --limit 50
    DATABASE_URL=... python3 revisar_cartera.py --todas      # ignora la frecuencia
"""

import argparse
import os
import sys
import time
import traceback

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import cartera  # noqa: E402
import inpi_lead  # noqa: E402

import monitor_bloqueo
from registro import registrar  # noqa: E402
from validar_leads import crear_sesion  # noqa: E402

SQL_PENDIENTES = """
    SELECT cm.acta, cm.cuit, cm.denominacion, cm.consultado_en, cm.estado_tramite
    FROM cartera_marcas cm
    JOIN clientes c ON c.id = cm.cliente_id
    WHERE c.activo
      AND cm.acta ~ '^[0-9]+$'
      AND (
            %(todas)s
         OR cm.consultado_en IS NULL
         OR (COALESCE(cm.estado_tramite, '') NOT IN ('Concedida', 'Denegada')
             AND cm.consultado_en < now() - interval '20 hours')
         OR (cm.estado_tramite IN ('Concedida', 'Denegada')
             AND cm.consultado_en < now() - interval '30 days')
      )
    ORDER BY (cm.consultado_en IS NULL) DESC, cm.consultado_en NULLS FIRST, cm.acta
"""


def _completar_denominacion_ws(info: dict, acta: str):
    """Mixtas/figurativas sin texto en la ficha: último recurso, el webservice
    SOAP por CUIT (devuelve la denominación de cada acta del titular)."""
    if info.get("denominacion") or not info.get("cuit"):
        return
    try:
        for r in inpi_lead.consultar_titular_ws(cuit=info["cuit"]):
            if str(r.get("Acta") or "").strip() == acta and (r.get("Denominacion") or "").strip():
                info["denominacion"] = r["Denominacion"].strip()
                return
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-minutes", type=float, default=25, help="corta la corrida pasado este tiempo")
    ap.add_argument("--limit", type=int, default=0, help="tope de marcas a revisar (0 = todas las pendientes)")
    ap.add_argument("--delay", type=float, default=1.5, help="segundos entre consulta y consulta")
    ap.add_argument("--bloqueos-para-frenar", type=int, default=5)
    ap.add_argument("--todas", action="store_true", help="revisa toda la cartera sin mirar la frecuencia")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    inicio = time.time()
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cartera.crear_tablas(cur)
        conn.commit()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(SQL_PENDIENTES, {"todas": bool(args.todas)})
            pendientes = cur.fetchall()
        conn.commit()
        if args.limit:
            pendientes = pendientes[: args.limit]
        print(f"Marcas de la cartera a revisar: {len(pendientes)}")

        s = crear_sesion()
        ok = novedades = bloqueadas = errores = inexistentes = 0
        bloqueo_seguido = 0
        revisadas = 0
        for i, fila in enumerate(pendientes, 1):
            if monitor_bloqueo.debe_cortar():
                print("Se corta la corrida por bloqueos seguidos de INPI: el resto sigue en la próxima.")
                break
            if (time.time() - inicio) / 60 > args.max_minutes:
                print(f"Se llegó al tope de {args.max_minutes:g} minutos: el resto sigue en la próxima corrida.")
                break
            acta = fila["acta"]
            info = inpi_lead.consultar_expediente(acta, s=s)
            estado = info.get("estado_consulta")
            revisadas += 1
            if estado == "bloqueado":
                bloqueadas += 1
                bloqueo_seguido += 1
                print(f"  [{i}/{len(pendientes)}] acta {acta}: bloqueado por el WAF, se reintenta la próxima corrida")
                with conn.cursor() as cur:
                    cartera.aplicar_expediente(cur, acta, info)
                conn.commit()
                if bloqueo_seguido >= args.bloqueos_para_frenar:
                    print(f"{bloqueo_seguido} bloqueos seguidos: se corta la corrida.")
                    break
                time.sleep(args.delay * 4)
                continue
            bloqueo_seguido = 0
            if estado == "ok":
                _completar_denominacion_ws(info, acta)
            with conn.cursor() as cur:
                nuevas = cartera.aplicar_expediente(cur, acta, info)
            conn.commit()
            if estado == "ok":
                ok += 1
                novedades += len(nuevas)
                print(f"  [{i}/{len(pendientes)}] acta {acta}: ok" + (f", {len(nuevas)} novedad(es)" if nuevas else ""))
            elif estado == "no_existe":
                inexistentes += 1
                print(f"  [{i}/{len(pendientes)}] acta {acta}: INPI no la devuelve")
            else:
                errores += 1
                print(f"  [{i}/{len(pendientes)}] acta {acta}: error de consulta")
            time.sleep(args.delay)

        resumen = (f"Revisadas: {revisadas} de {len(pendientes)}. Actualizadas: {ok}. Novedades: {novedades}. "
                   f"Bloqueadas: {bloqueadas}. Sin resultado: {inexistentes}. Con error: {errores}.")
        print(f"::notice::{resumen}")
        print(f"\n{resumen}")
        registrar("revisar_cartera.yml", {
            "revisadas": revisadas, "actualizadas": ok, "novedades": novedades,
            "bloqueos": bloqueadas, "sin_resultado": inexistentes, "errores": errores,
            "pendientes_restantes": max(len(pendientes) - revisadas, 0),
        }, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        linea = f"{type(e).__name__}: {e}".replace("\n", " ")
        print(f"::error::revisar_cartera falló: {linea}")
        traceback.print_exc()
        sys.exit(1)
