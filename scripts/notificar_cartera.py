"""
Mail diario (Resend) de la cartera de clientes y la vigilancia marcaria.
Junta en un solo mail lo que todavía no se avisó:

  1. Alertas de vigilancia nuevas de nivel ALTO o MEDIO (marcas parecidas a
     una de un cliente) y avisos de "cliente con otro agente". Las de nivel
     bajo quedan solo en el panel.
  2. Plazos que se acercan: renovación (90/60/30/7 días), DJ de uso de medio
     término (90/60/30/7), cierre del plazo de oposición a una marca parecida
     detectada por la vigilancia (14/7/3), y oposición o vista recibida.
     Cada plazo se avisa una vez por umbral (tabla cartera_avisos_plazo).
  3. Novedades de los expedientes (concesión, oposición, publicación, cambio
     de titular, movimientos nuevos).

Si no hay nada nuevo no manda nada. Al enviar, marca todo como avisado.

Variables de entorno:
    DATABASE_URL     (obligatoria)
    RESEND_API_KEY   (obligatoria, salvo con --dry-run / --solo-marcar)
    RESEND_FROM      remitente verificado en Resend
    NOTIFICAR_A      destinatarios separados por coma (default: marcas@komunikacion.com.ar)
    PANEL_URL        base del panel (default: https://panel.registrodemimarca.com.ar)

Uso:
    python3 notificar_cartera.py              # envía y marca como avisado
    python3 notificar_cartera.py --dry-run    # arma el HTML en /tmp sin enviar ni marcar
    python3 notificar_cartera.py --solo-marcar
"""

import argparse
import html
import os
import sys
import traceback

import psycopg2
import psycopg2.extras
import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import cartera  # noqa: E402

from registro import registrar  # noqa: E402

DEFAULT_FROM = "Avisos Panel <avisos@quieroregistrarmimarca.com.ar>"
DEFAULT_TO = "marcas@komunikacion.com.ar"
DEFAULT_PANEL = "https://panel.registrodemimarca.com.ar"

# Umbrales (días que faltan) por tipo de plazo; se avisa cuando entra en el más chico que le corresponde.
UMBRALES = {
    "renovacion": (90, 60, 30, 7),
    "ddjj": (90, 60, 30, 7),
    "oposicion_vigilancia": (14, 7, 3),
}
TIPOS_NOVEDAD_MAIL = ("estado", "oposicion", "publicacion", "titular", "movimiento", "alta_auto")

SQL_ALERTAS = """
    SELECT a.*, c.nombre AS cliente_nombre, COALESCE(c.vigilancia_contratada, false) AS vigilancia_contratada,
           cm.denominacion AS denominacion_cliente, cm.clase AS clase_cliente
    FROM vigilancia_alertas a
    LEFT JOIN clientes c ON c.id = a.cliente_id
    LEFT JOIN cartera_marcas cm ON cm.acta = a.acta_cliente
    WHERE a.notificada_en IS NULL AND a.estado = 'nueva'
      AND (a.tipo = 'otro_agente' OR a.nivel IN ('alta', 'media'))
      AND COALESCE(c.activo, true)
    ORDER BY CASE a.nivel WHEN 'alta' THEN 0 ELSE 1 END, a.puntaje DESC, a.creada_en
"""

SQL_PUBLICACION_NUEVA = """
    SELECT CASE WHEN m.boletin IS NULL THEN m.fecha_publicacion
                ELSE COALESCE(m.fecha_publicacion, b.fecha, b.procesado_en::date) END
    FROM marcas m LEFT JOIN boletines b ON b.numero = m.boletin WHERE m.acta = %s
"""

SQL_NOVEDADES = """
    SELECT n.id, n.acta, n.tipo, n.texto, n.detectado_en, c.nombre AS cliente_nombre, cm.denominacion
    FROM cartera_novedades n
    LEFT JOIN clientes c ON c.id = n.cliente_id
    LEFT JOIN cartera_marcas cm ON cm.acta = n.acta
    WHERE n.notificado_en IS NULL AND n.tipo = ANY(%s) AND COALESCE(c.activo, true)
    ORDER BY c.nombre, n.acta, n.detectado_en
"""


def alertas_pendientes(cur):
    cur.execute(SQL_ALERTAS)
    return cur.fetchall()


def plazos_a_avisar(cur, hoy):
    """[(plazo, umbral, clave_aviso_acta)] de los plazos que entraron en un
    umbral y todavía no se avisaron."""
    cur.execute(
        "SELECT cm.*, c.nombre AS cliente_nombre FROM cartera_marcas cm "
        "JOIN clientes c ON c.id = cm.cliente_id WHERE c.activo"
    )
    plazos = []
    for m in cur.fetchall():
        plazos += [(p, m["acta"]) for p in cartera.plazos_marca_cartera(m, hoy)]
    # cierre del plazo de oposición a marcas parecidas (alertas abiertas de parecido, nivel alta/media)
    cur.execute(
        """
        SELECT a.*, c.nombre AS cliente_nombre, cm.denominacion AS denominacion_cliente, cm.clase AS clase_cliente
        FROM vigilancia_alertas a LEFT JOIN clientes c ON c.id = a.cliente_id
        LEFT JOIN cartera_marcas cm ON cm.acta = a.acta_cliente
        WHERE a.tipo = 'similitud' AND a.estado IN ('nueva', 'monitorear', 'oponer') AND a.nivel IN ('alta', 'media')
          AND COALESCE(c.activo, true)
        """
    )
    for a in cur.fetchall():
        cur.execute(SQL_PUBLICACION_NUEVA, (a["acta_nueva"],))
        fila = cur.fetchone()
        a["publicacion_nueva"] = list(fila.values())[0] if fila else None
        p = cartera.plazo_oposicion_alerta(a, hoy)
        if p:
            plazos.append((p, f"{a['acta_cliente']}>{a['acta_nueva']}"))

    out = []
    for p, acta_aviso in plazos:
        umbrales = UMBRALES.get(p["tipo"])
        if not umbrales or p["dias"] < 0:
            continue
        aplicables = [u for u in umbrales if p["dias"] <= u]
        if not aplicables:
            continue
        u = min(aplicables)
        cur.execute(
            "SELECT 1 FROM cartera_avisos_plazo WHERE acta = %s AND tipo = %s AND fecha = %s AND umbral <= %s",
            (acta_aviso, p["tipo"], p["fecha"], u),
        )
        if cur.fetchone():
            continue  # ya se avisó este umbral u otro más chico
        out.append((p, u, acta_aviso))
    # una oposición o vista recibida se avisa por la novedad del expediente, no por acá
    out.sort(key=lambda x: x[0]["fecha"])
    return out


def _e(v):
    return html.escape(str(v)) if v not in (None, "") else "—"


def _fecha(f):
    return f.strftime("%d/%m/%Y") if hasattr(f, "strftime") else (f or "—")


def armar_html(alertas, plazos, novedades, panel_url):
    secciones = []
    if alertas:
        filas = []
        for a in alertas:
            if a["tipo"] == "otro_agente":
                filas.append(f"""<tr><td style="padding:12px 16px;border-bottom:1px solid #eaecf0">
                  <div style="font-size:12px;font-weight:600;color:#6d28d9;text-transform:uppercase">Cliente con otro agente</div>
                  <div style="font-size:15px;font-weight:600;color:#101828">{_e(a['cliente_nombre'])} presentó «{_e(a['denominacion_nueva'])}»</div>
                  <div style="color:#475467;font-size:13px">Acta {_e(a['acta_nueva'])} · clase {_e(a['clase_nueva'])} · {_e(a['agente_nuevo'])} · {_e(a['fuente_nueva'])}</div></td></tr>""")
            else:
                color = "#b42318" if a["nivel"] == "alta" else "#b54708"
                contrato = "" if a["vigilancia_contratada"] else " · <em>sin vigilancia contratada</em>"
                filas.append(f"""<tr><td style="padding:12px 16px;border-bottom:1px solid #eaecf0">
                  <div style="font-size:12px;font-weight:600;color:{color};text-transform:uppercase">Parecido {_e(a['nivel'])} · {a['puntaje']}%</div>
                  <div style="font-size:15px;font-weight:600;color:#101828">«{_e(a['denominacion_nueva'])}» se parece a «{_e(a['denominacion_cliente'])}»</div>
                  <div style="color:#475467;font-size:13px">Cliente: {_e(a['cliente_nombre'])}{contrato}<br>
                    Nueva: acta {_e(a['acta_nueva'])}, clase {_e(a['clase_nueva'])}, {_e(a['agente_nuevo'])} · {_e(a['fuente_nueva'])}<br>
                    De la cartera: acta {_e(a['acta_cliente'])}, clase {_e(a['clase_cliente'])} · {_e(a['motivos'])}</div></td></tr>""")
        secciones.append(("Vigilancia: alertas nuevas", filas))
    if plazos:
        filas = []
        for p, u, _ in plazos:
            color = "#b42318" if p["dias"] <= 7 else "#b54708"
            dias = "hoy" if p["dias"] == 0 else ("mañana" if p["dias"] == 1 else f"en {p['dias']} días")
            filas.append(f"""<tr><td style="padding:12px 16px;border-bottom:1px solid #eaecf0">
              <div style="font-size:12px;font-weight:600;color:{color};text-transform:uppercase">{_fecha(p['fecha'])} · {dias}</div>
              <div style="font-size:15px;font-weight:600;color:#101828">{_e(p['titulo'])}</div>
              <div style="color:#475467;font-size:13px">{_e(p.get('cliente'))}{' · ' + _e(p['marca']) if p.get('marca') else ''}{' · acta ' + _e(p['acta']) if p.get('acta') else ''}<br>{_e(p.get('detalle'))}</div></td></tr>""")
        secciones.append(("Plazos que se acercan", filas))
    if novedades:
        filas = []
        ultimo = None
        for n in novedades:
            cab = f"{n['cliente_nombre']} · {n['denominacion'] or 'acta ' + n['acta']}"
            if cab != ultimo:
                filas.append(f"""<tr><td style="padding:10px 16px 0;font-size:14px;font-weight:600;color:#101828">{_e(cab)} <span style="color:#98a2b3;font-weight:400">(acta {_e(n['acta'])})</span></td></tr>""")
                ultimo = cab
            filas.append(f"""<tr><td style="padding:2px 16px 4px 28px;color:#475467;font-size:13px">• {_e(n['texto'])}</td></tr>""")
        secciones.append(("Novedades en los expedientes", filas))

    partes = []
    if alertas:
        partes.append(f"{len(alertas)} alerta{'s' if len(alertas) != 1 else ''}")
    if plazos:
        partes.append(f"{len(plazos)} plazo{'s' if len(plazos) != 1 else ''}")
    if novedades:
        partes.append(f"{len(novedades)} novedad{'es' if len(novedades) != 1 else ''}")
    titulo = "Cartera y vigilancia: " + " · ".join(partes)
    cuerpo_secciones = "".join(
        f"""<tr><td style="padding:14px 16px 6px;font-size:13px;font-weight:700;color:#344054;text-transform:uppercase;letter-spacing:.04em;background:#f9fafb">{html.escape(t)}</td></tr>{''.join(f)}"""
        for t, f in secciones
    )
    link = f"{panel_url}/clientes?vista=vigilancia"
    return titulo, f"""<!doctype html>
<html><body style="margin:0;background:#f2f4f7;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f7;padding:24px 0"><tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;background:#ffffff;border-radius:8px;overflow:hidden">
  <tr><td style="padding:20px 16px;border-bottom:1px solid #eaecf0">
    <div style="font-size:20px;font-weight:700;color:#101828">{html.escape(titulo)}</div>
    <a href="{html.escape(link)}" style="display:inline-block;margin-top:10px;padding:8px 14px;background:#1d4ed8;color:#ffffff;text-decoration:none;border-radius:6px;font-size:14px;font-weight:600">Abrir Clientes y vigilancia</a>
  </td></tr>
  {cuerpo_secciones}
  <tr><td style="padding:14px 16px;color:#98a2b3;font-size:12px">Fechas orientativas: confirmar siempre en el expediente. Aviso automático del panel · <a href="{html.escape(panel_url)}" style="color:#98a2b3">abrir panel</a></td></tr>
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
    ap.add_argument("--solo-marcar", action="store_true", help="marca todo como avisado sin enviar")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
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
        with conn.cursor() as cur:
            cartera.crear_tablas(cur)
        conn.commit()
        hoy = cartera.hoy_ar()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            alertas = alertas_pendientes(cur)
            plazos = plazos_a_avisar(cur, hoy)
            cur.execute(SQL_NOVEDADES, (list(TIPOS_NOVEDAD_MAIL),))
            novedades = cur.fetchall()
        conn.commit()
        print(f"Sin avisar: {len(alertas)} alertas, {len(plazos)} plazos, {len(novedades)} novedades.")
        enviado = 0
        if alertas or plazos or novedades:
            if not args.solo_marcar:
                asunto, cuerpo = armar_html(alertas, plazos, novedades, panel_url)
                if args.dry_run:
                    ruta = "/tmp/notificacion_cartera.html"
                    with open(ruta, "w") as f:
                        f.write(cuerpo)
                    print(f"[dry-run] Asunto: {asunto}. HTML en {ruta}. No se envió ni se marcó nada.")
                    return
                mail_id = enviar_resend(api_key, remitente, destinatarios, asunto, cuerpo)
                enviado = 1
                print(f"Mail enviado (id {mail_id}) a {len(destinatarios)} destinatario(s).")
            with conn.cursor() as cur:
                if alertas:
                    cur.execute("UPDATE vigilancia_alertas SET notificada_en = now() WHERE id = ANY(%s)", ([a["id"] for a in alertas],))
                for p, u, acta_aviso in plazos:
                    cur.execute(
                        "INSERT INTO cartera_avisos_plazo (acta, tipo, fecha, umbral) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (acta_aviso, p["tipo"], p["fecha"], u),
                    )
                if novedades:
                    cur.execute("UPDATE cartera_novedades SET notificado_en = now() WHERE id = ANY(%s)", ([n["id"] for n in novedades],))
            conn.commit()
        registrar("vigilancia.yml", {  # el mail es un paso del workflow vigilancia.yml
            "alertas_avisadas": len(alertas), "plazos_avisados": len(plazos), "novedades_avisadas": len(novedades),
            "mails_enviados": enviado,
        }, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        linea = f"{type(e).__name__}: {e}".replace("\n", " ")
        print(f"::error::notificar_cartera falló: {linea}")
        traceback.print_exc()
        sys.exit(1)
