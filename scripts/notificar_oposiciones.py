"""
Manda por mail (Resend) un resumen con los leads que recibieron una
oposición de tercero o una vista de INPI y que todavía no se avisaron.

Corre después de revisar_oposiciones.py, en el mismo workflow diario. Cada
lead se avisa una sola vez: al enviarse el mail se marca
notificado_oposicion_en = now(). Si no hay novedades, no se manda nada.

Quedan afuera los que ya sumaron apoderado/gestor después de la oposición
(representacion_posterior_oposicion = true): ya no son un lead frío.

Variables de entorno:
    DATABASE_URL     (obligatoria)
    RESEND_API_KEY   (obligatoria, salvo con --dry-run). Si en el panel (pestaña Mails) se
                     eligió otra cuenta de Resend, la variable es la de esa cuenta.
    RESEND_FROM / NOTIFICAR_A   valores de respaldo: lo que se configura en la pestaña Mails
                     del panel (remitente, Reply-To, destinatarios) tiene prioridad.
                     Por defecto: "Avisos Panel <avisos@quieroregistrarmimarca.com.ar>" y marcas@komunikacion.com.ar
    PANEL_URL        base del panel (default: https://panel.registrodemimarca.com.ar)

Uso:
    python3 notificar_oposiciones.py              # envía y marca como notificados
    python3 notificar_oposiciones.py --dry-run    # arma el HTML en /tmp sin enviar ni marcar
    python3 notificar_oposiciones.py --solo-marcar  # marca los pendientes sin enviar
"""

import argparse
import html
import os
import sys
from urllib.parse import quote

import psycopg2
import psycopg2.extras
import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))

DEFAULT_FROM = "Avisos Panel <avisos@quieroregistrarmimarca.com.ar>"
DEFAULT_TO = "marcas@komunikacion.com.ar"
DEFAULT_PANEL = "https://panel.registrodemimarca.com.ar"

SQL_PENDIENTES = """
    SELECT acta, clase, denominacion, denominacion_inpi, titular, cuit, email,
           fecha_publicacion, detalle_oposicion, oponente_nombre,
           marca_oponente_denominacion
    FROM marcas
    WHERE es_lead = true
      AND tuvo_oposicion = true
      AND representacion_posterior_oposicion IS NOT TRUE
      AND notificado_oposicion_en IS NULL
    ORDER BY fecha_publicacion DESC NULLS LAST, acta
"""


import mails_core  # noqa: E402
from mails_plantillas import armar_html_oposiciones as armar_html  # noqa: E402


def enviar_resend(api_key, remitente, destinatarios, asunto, cuerpo, responder_a=None):
    payload = {"from": remitente, "to": destinatarios, "subject": asunto, "html": cuerpo}
    if responder_a:
        payload["reply_to"] = responder_a
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if r.status_code >= 300:
        sys.exit(f"Resend rechazó el envío ({r.status_code}): {r.text[:500]}")
    return r.json().get("id")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="no envía ni marca; guarda el HTML en /tmp")
    ap.add_argument("--solo-marcar", action="store_true", help="marca los pendientes como notificados sin enviar")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")
    # Cuenta de Resend, remitente y destinatarios: se configuran en el panel (pestaña Mails).
    cfg_mail = mails_core.preparar("oposiciones", dsn)
    api_key, remitente, destinatarios = cfg_mail["api_key"], cfg_mail["remitente"], cfg_mail["destinatarios"]
    if not (api_key or args.dry_run or args.solo_marcar):
        sys.exit(f"Falta la variable de entorno {cfg_mail['env_key']}")
    panel_url = (os.environ.get("PANEL_URL") or DEFAULT_PANEL).rstrip("/")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("ALTER TABLE marcas ADD COLUMN IF NOT EXISTS notificado_oposicion_en TIMESTAMPTZ")
            conn.commit()
            cur.execute(SQL_PENDIENTES)
            filas = cur.fetchall()

        # Sin datos personales en el log (el repo es público): solo actas y conteo.
        print(f"Leads con oposición/vista sin avisar: {len(filas)}")
        if not filas:
            return

        actas = [f["acta"] for f in filas]

        if not args.solo_marcar:
            asunto, cuerpo = armar_html(filas, panel_url)
            if args.dry_run:
                ruta = "/tmp/notificacion_oposiciones.html"
                with open(ruta, "w") as f:
                    f.write(cuerpo)
                print(f"[dry-run] Asunto: {asunto}. HTML guardado en {ruta}. No se envió ni se marcó nada.")
                return
            mail_id = enviar_resend(api_key, remitente, destinatarios, asunto, cuerpo, cfg_mail["responder_a"])
            print(f"Mail enviado (id {mail_id}) a {len(destinatarios)} destinatario(s).")

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE marcas SET notificado_oposicion_en = now() WHERE acta = ANY(%s)",
                (actas,),
            )
        conn.commit()
        print(f"Marcadas como notificadas: {len(actas)} actas ({', '.join(map(str, actas))}).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
