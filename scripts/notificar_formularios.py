"""
Respaldo del aviso por mail de los formularios para clientes.

El panel manda el mail ni bien llega un formulario (si tiene RESEND_API_KEY
configurada en Railway). Si no la tiene, o si Resend falló en ese momento,
la respuesta queda "pendiente de aviso" y este script la manda: corre cada
2 horas dentro del workflow reintentar_sin_email.yml.

Solo toma respuestas con más de 5 minutos (para no pisarse con el envío
inmediato del panel). Cada respuesta se bloquea al enviarla (FOR UPDATE SKIP
LOCKED), así que nunca sale el mismo mail dos veces.

Variables de entorno:
    DATABASE_URL     (o DATABASE_PUBLIC_URL)
    RESEND_API_KEY   obligatoria (salvo --dry-run)
    RESEND_FROM      remitente verificado en Resend
    NOTIFICAR_A      destinatarios separados por coma (default marcas@komunikacion.com.ar)
    PANEL_URL        base del panel
"""

import argparse
import os
import sys

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import cartera  # noqa: E402
import formularios_core as fc  # noqa: E402

from registro import registrar  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="solo cuenta las pendientes, no envía")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")
    api_key = os.environ.get("RESEND_API_KEY")
    if not (api_key or args.dry_run):
        sys.exit("Falta la variable de entorno RESEND_API_KEY")
    remitente = os.environ.get("RESEND_FROM") or fc.DEFAULT_FROM
    destinatarios = [d.strip() for d in (os.environ.get("NOTIFICAR_A") or fc.DEFAULT_TO).split(",") if d.strip()]
    panel_url = (os.environ.get("PANEL_URL") or fc.DEFAULT_PANEL).rstrip("/")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cartera.crear_tablas(cur)
            fc.crear_tablas(cur)
            cur.execute("SELECT id FROM formularios_respuestas WHERE aviso_enviado_en IS NULL "
                        "AND recibido_en < now() - interval '5 minutes' ORDER BY id LIMIT 100")
            pendientes = [r[0] for r in cur.fetchall()]
        conn.commit()
        print(f"Formularios con aviso pendiente: {len(pendientes)}")
        if args.dry_run:
            return
        enviados, errores = 0, 0
        for rid in pendientes:
            try:
                if fc.avisar_respuesta(conn, rid, api_key, remitente, destinatarios, panel_url):
                    enviados += 1
                    print(f"  respuesta {rid}: mail enviado")
            except Exception as e:  # noqa: BLE001 — sigue con las demás
                errores += 1
                conn.rollback()
                print(f"  respuesta {rid}: no se pudo enviar ({e})")
        print(f"Enviados: {enviados}. Con error: {errores}.")
        if pendientes:
            registrar("reintentar_sin_email.yml", {"formularios_avisados": enviados})
        if errores:
            sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
