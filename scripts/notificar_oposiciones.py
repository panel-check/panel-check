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
    RESEND_API_KEY   (obligatoria, salvo con --dry-run)
    RESEND_FROM      remitente; tiene que ser de un dominio VERIFICADO en Resend
                     (default: "Avisos Panel <avisos@quieroregistrarmimarca.com.ar>")
    NOTIFICAR_A      destinatarios separados por coma (default: marcas@komunikacion.com.ar)
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


def clave_titular(r):
    # Mismo criterio que claveTitular() en panel/static/comun.js.
    cuit = (r.get("cuit") or "").strip()
    if cuit:
        return cuit
    return " ".join((r.get("titular") or "").split()).upper()


def tipo_aviso(r):
    d = (r.get("detalle_oposicion") or "").upper()
    return "Oposición" if "OPO" in d else "Vista"


def armar_html(filas, panel_url):
    e = lambda v: html.escape(str(v)) if v not in (None, "") else "—"
    tarjetas = []
    for r in filas:
        nombre = r.get("denominacion_inpi") or r.get("denominacion") or "(sin denominación)"
        clave = clave_titular(r)
        link = f"{panel_url}/titular/{quote(clave, safe='')}" if clave else panel_url
        tipo = tipo_aviso(r)
        fp = r.get("fecha_publicacion")
        fecha = fp.strftime("%d/%m/%Y") if hasattr(fp, "strftime") else fp
        color = "#b42318" if tipo == "Oposición" else "#b54708"
        oponente = ""
        if r.get("oponente_nombre"):
            oponente = f"<div style='color:#475467;font-size:13px'>Opone: {e(r['oponente_nombre'])}"
            if r.get("marca_oponente_denominacion"):
                oponente += f" (marca {e(r['marca_oponente_denominacion'])})"
            oponente += "</div>"
        tarjetas.append(f"""
        <tr><td style="padding:14px 16px;border-bottom:1px solid #eaecf0">
          <div style="font-size:12px;font-weight:600;color:{color};text-transform:uppercase;letter-spacing:.04em">{tipo}</div>
          <div style="font-size:16px;font-weight:600;color:#101828;margin:2px 0">{e(nombre)}</div>
          <div style="color:#475467;font-size:13px">Acta {e(r['acta'])} · Clase {e(r.get('clase'))} · Publicada {e(fecha)}</div>
          <div style="color:#475467;font-size:13px">Titular: {e(r.get('titular'))} · {e(r.get('email'))}</div>
          {oponente}
          <a href="{html.escape(link)}" style="display:inline-block;margin-top:10px;padding:8px 14px;background:#1d4ed8;color:#ffffff;text-decoration:none;border-radius:6px;font-size:14px;font-weight:600">Ver lead</a>
        </td></tr>""")
    n = len(filas)
    titulo = f"{n} lead{'s' if n != 1 else ''} con oposición o vista nueva"
    return titulo, f"""<!doctype html>
<html><body style="margin:0;background:#f2f4f7;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f7;padding:24px 0"><tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:8px;overflow:hidden">
  <tr><td style="padding:20px 16px;border-bottom:1px solid #eaecf0">
    <div style="font-size:20px;font-weight:700;color:#101828">{html.escape(titulo)}</div>
    <div style="color:#475467;font-size:14px;margin-top:4px">Solicitantes sin apoderado que recibieron una oposición de tercero o una vista de INPI desde el último aviso.</div>
  </td></tr>
  {''.join(tarjetas)}
  <tr><td style="padding:14px 16px;color:#98a2b3;font-size:12px">
    Aviso automático del panel · <a href="{html.escape(panel_url)}" style="color:#98a2b3">abrir panel</a>
  </td></tr>
</table></td></tr></table></body></html>"""


def enviar_resend(api_key, remitente, destinatarios, asunto, cuerpo):
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": remitente, "to": destinatarios, "subject": asunto, "html": cuerpo},
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
    api_key = os.environ.get("RESEND_API_KEY")
    if not (api_key or args.dry_run or args.solo_marcar):
        sys.exit("Falta la variable de entorno RESEND_API_KEY")

    remitente = os.environ.get("RESEND_FROM") or DEFAULT_FROM
    destinatarios = [d.strip() for d in (os.environ.get("NOTIFICAR_A") or DEFAULT_TO).split(",") if d.strip()]
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
            mail_id = enviar_resend(api_key, remitente, destinatarios, asunto, cuerpo)
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
