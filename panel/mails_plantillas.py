"""
Cuerpos (HTML) de los mails internos del panel, en un solo lugar.

Los usan los scripts que mandan los avisos (scripts/notificar_*.py,
scripts/monitor_bloqueo.py) y la pestaña "Mails" del panel, que muestra una
vista previa de cada uno con datos de ejemplo. Así lo que se ve en el panel es
exactamente lo que sale por mail.
"""

import html
from datetime import date, timedelta
from urllib.parse import quote

def clave_titular(r):
    # Mismo criterio que claveTitular() en panel/static/comun.js.
    cuit = (r.get("cuit") or "").strip()
    if cuit:
        return cuit
    return " ".join((r.get("titular") or "").split()).upper()


def tipo_aviso(r):
    d = (r.get("detalle_oposicion") or "").upper()
    return "Oposición" if "OPO" in d else "Vista"


def armar_html_oposiciones(filas, panel_url):
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
        estado = ""
        if r.get("estado_oposicion_detalle"):
            estado = f"<div style='color:#b42318;font-size:13px;font-weight:600;margin-top:4px'>{e(r['estado_oposicion_detalle'])}</div>"
        tarjetas.append(f"""
        <tr><td style="padding:14px 16px;border-bottom:1px solid #eaecf0">
          <div style="font-size:12px;font-weight:600;color:{color};text-transform:uppercase;letter-spacing:.04em">{tipo}</div>
          <div style="font-size:16px;font-weight:600;color:#101828;margin:2px 0">{e(nombre)}</div>
          <div style="color:#475467;font-size:13px">Acta {e(r['acta'])} · Clase {e(r.get('clase'))} · Publicada {e(fecha)}</div>
          <div style="color:#475467;font-size:13px">Titular: {e(r.get('titular'))} · {e(r.get('email'))}</div>
          {oponente}
          {estado}
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


def _e(v):
    return html.escape(str(v)) if v not in (None, "") else "—"


def _fecha(f):
    return f.strftime("%d/%m/%Y") if hasattr(f, "strftime") else (f or "—")


def armar_html_cartera(alertas, plazos, novedades, panel_url):
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


def armar_mail_bloqueo(proceso, motivo, ok, bloqueadas, link=""):
    """Aviso de bloqueo masivo del WAF de INPI -> (asunto, html)."""
    cuerpo = (
        f"<p><b>INPI está bloqueando las consultas del sistema.</b></p>"
        f"<p>Proceso: <b>{proceso}</b><br>Detalle: {motivo}<br>"
        f"Consultas OK en esta corrida: {ok} — bloqueadas: {bloqueadas}</p>"
        f"<p>Las marcas afectadas quedaron como “no verificadas” y los reintentos las vuelven a "
        f"probar solas. Si este aviso se repite en varias corridas seguidas, es un bloqueo "
        f"sostenido: conviene pausar los workflows un día y bajar el ritmo de consultas.</p>"
        + (f'<p><a href="{link}">Ver la corrida en GitHub</a></p>' if link else "")
    )
    return f"⚠️ INPI está bloqueando consultas ({proceso})", cuerpo


# ── Ejemplos para la vista previa de la pestaña "Mails" ───────────────────
def ejemplo(clave, panel_url):
    """(asunto, html) con datos inventados, para mostrar en el panel cómo se ve
    cada aviso. No toca la base."""
    hoy = date.today()
    if clave == "oposiciones":
        filas = [
            {"acta": "4797123", "clase": 25, "denominacion": "LUNA NUEVA", "denominacion_inpi": None,
             "titular": "María Gómez", "cuit": "27-12345678-4", "email": "maria@ejemplo.com",
             "fecha_publicacion": hoy - timedelta(days=12), "detalle_oposicion": "OPOSICION",
             "oponente_nombre": "Moda Sur S.A.", "marca_oponente_denominacion": "LUNA"},
            {"acta": "4797456", "clase": 30, "denominacion": "DULCE HOGAR", "denominacion_inpi": None,
             "titular": "Juan Pérez", "cuit": "", "email": None,
             "fecha_publicacion": hoy - timedelta(days=5), "detalle_oposicion": "VISTA",
             "oponente_nombre": None, "marca_oponente_denominacion": None},
        ]
        return armar_html_oposiciones(filas, panel_url)
    if clave == "cartera":
        alertas = [
            {"tipo": "similitud", "nivel": "alta", "puntaje": 92, "cliente_nombre": "Panadería Don Luis",
             "vigilancia_contratada": True, "denominacion_nueva": "DON LUIZ", "denominacion_cliente": "DON LUIS",
             "acta_nueva": "4797800", "clase_nueva": 30, "agente_nuevo": "Sin agente", "fuente_nueva": "boletín 11120",
             "acta_cliente": "3901234", "clase_cliente": 30, "motivos": "nombre casi idéntico, misma clase"},
            {"tipo": "otro_agente", "nivel": "media", "puntaje": 0, "cliente_nombre": "Estudio Aurora",
             "denominacion_nueva": "AURORA HOME", "acta_nueva": "4797901", "clase_nueva": 35,
             "agente_nuevo": "Otro Agente (Mat. 1234)", "fuente_nueva": "escaneo de actas"},
        ]
        plazos = [
            ({"fecha": hoy + timedelta(days=28), "dias": 28, "titulo": "Vence la renovación",
              "cliente": "Panadería Don Luis", "marca": "DON LUIS", "acta": "3901234", "detalle": "Registro vigente hasta esa fecha",
              "tipo": "renovacion"}, 30, "3901234"),
            ({"fecha": hoy + timedelta(days=6), "dias": 6, "titulo": "Cierra el plazo de oposición a «DON LUIZ»",
              "cliente": "Panadería Don Luis", "marca": "DON LUIS", "acta": "4797800", "detalle": "30 días corridos desde la publicación",
              "tipo": "oposicion_vigilancia"}, 7, "3901234>4797800"),
        ]
        novedades = [
            {"cliente_nombre": "Estudio Aurora", "denominacion": "AURORA", "acta": "4600123", "texto": "Marca concedida"},
            {"cliente_nombre": "Estudio Aurora", "denominacion": "AURORA", "acta": "4600123", "texto": "Nuevo movimiento: Pago de tasa"},
        ]
        return armar_html_cartera(alertas, plazos, novedades, panel_url)
    if clave == "bloqueo":
        return armar_mail_bloqueo("Escanear actas nuevas", "7 consultas seguidas bloqueadas", 0, 7,
                                  "https://github.com/panel-check/panel-check/actions")
    raise KeyError(clave)
