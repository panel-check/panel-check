"""
Cuerpos (HTML) de los mails internos del panel, en un solo lugar.

Los usan los scripts que mandan los avisos (scripts/notificar_*.py,
scripts/monitor_bloqueo.py) y la pestaña "Mails" del panel, que muestra una
vista previa de cada uno con datos de ejemplo. Así lo que se ve en el panel es
exactamente lo que sale por mail.
"""

import html

import mails_marco as mm
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
        <tr><td style="padding:14px 24px;border-top:1px solid #eaecf0">
          <div style="font-size:12px;font-weight:600;color:{color};text-transform:uppercase;letter-spacing:.04em">{tipo}</div>
          <div style="font-size:16px;font-weight:600;color:#101828;margin:2px 0">{e(nombre)}</div>
          <div style="color:#475467;font-size:13px">Acta {e(r['acta'])} · Clase {e(r.get('clase'))} · Publicada {e(fecha)}</div>
          <div style="color:#475467;font-size:13px">Titular: {e(r.get('titular'))} · {e(r.get('email'))}</div>
          {oponente}
          {estado}
          <a href="{html.escape(link)}" style="display:inline-block;margin-top:10px;padding:8px 14px;background:#015197;color:#ffffff;text-decoration:none;border-radius:8px;font-size:14px;font-weight:600">Ver lead</a>
        </td></tr>""")
    n = len(filas)
    titulo = f"{n} lead{'s' if n != 1 else ''} con oposición o vista nueva"
    return titulo, mm.marco(
        "".join(tarjetas), etiqueta="Oposiciones y vistas", titulo=titulo,
        subtitulo="Solicitantes sin apoderado que recibieron una oposición de tercero o una vista de INPI desde el último aviso.",
        panel_url=panel_url)


def _e(v):
    return html.escape(str(v)) if v not in (None, "") else "—"


def _fecha(f):
    return f.strftime("%d/%m/%Y") if hasattr(f, "strftime") else (f or "—")


def _fila_item(cabeza: str, titulo: str, detalle: str = "", color: str = "#475467") -> str:
    """Fila de un aviso: etiqueta chica de color, título y detalle."""
    return (f'<tr><td style="padding:12px 24px;border-top:1px solid #eaecf0">'
            + (f'<div style="font-size:11px;font-weight:700;color:{color};text-transform:uppercase;letter-spacing:.05em">{cabeza}</div>' if cabeza else "")
            + f'<div style="font-size:15px;font-weight:700;color:#101828;margin-top:2px">{titulo}</div>'
            + (f'<div style="color:#475467;font-size:13px;line-height:1.5;margin-top:2px">{detalle}</div>' if detalle else "")
            + "</td></tr>")


def armar_html_cartera(alertas, plazos, novedades, panel_url):
    filas = []
    if alertas:
        filas.append(mm.seccion("Vigilancia: alertas nuevas"))
        for a in alertas:
            if a["tipo"] == "otro_agente":
                filas.append(_fila_item(
                    "Cliente con otro agente",
                    f"{_e(a['cliente_nombre'])} presentó «{_e(a['denominacion_nueva'])}»",
                    f"Acta {_e(a['acta_nueva'])} · clase {_e(a['clase_nueva'])} · {_e(a['agente_nuevo'])} · {_e(a['fuente_nueva'])}",
                    "#6d28d9"))
            else:
                color = "#b42318" if a["nivel"] == "alta" else "#b54708"
                contrato = "" if a["vigilancia_contratada"] else " · <em>sin vigilancia contratada</em>"
                filas.append(_fila_item(
                    f"Parecido {_e(a['nivel'])} · {a['puntaje']}%",
                    f"«{_e(a['denominacion_nueva'])}» se parece a «{_e(a['denominacion_cliente'])}»",
                    f"Cliente: {_e(a['cliente_nombre'])}{contrato}<br>"
                    f"Nueva: acta {_e(a['acta_nueva'])}, clase {_e(a['clase_nueva'])}, {_e(a['agente_nuevo'])} · {_e(a['fuente_nueva'])}<br>"
                    f"De la cartera: acta {_e(a['acta_cliente'])}, clase {_e(a['clase_cliente'])} · {_e(a['motivos'])}",
                    color))
    if plazos:
        filas.append(mm.seccion("Plazos que se acercan"))
        for p, u, _ in plazos:
            color = "#b42318" if p["dias"] <= 7 else "#b54708"
            dias = "hoy" if p["dias"] == 0 else ("mañana" if p["dias"] == 1 else f"en {p['dias']} días")
            filas.append(_fila_item(
                f"{_fecha(p['fecha'])} · {dias}", _e(p["titulo"]),
                f"{_e(p.get('cliente'))}{' · ' + _e(p['marca']) if p.get('marca') else ''}"
                f"{' · acta ' + _e(p['acta']) if p.get('acta') else ''}<br>{_e(p.get('detalle'))}",
                color))
    if novedades:
        filas.append(mm.seccion("Novedades en los expedientes"))
        # Agrupadas por marca: una tarjeta por acta con sus novedades adentro.
        grupos = {}
        for n in novedades:
            grupos.setdefault((n.get("cliente_nombre"), n.get("denominacion"), n["acta"]), []).append(n)
        for (cliente, marca, acta), items in grupos.items():
            lineas = []
            for n in items:
                if n.get("icono"):  # ya clasificada (notificaciones_inpi.filtrar_para_mail)
                    extra = " · ".join(_e(x) for x in (n.get("explicacion"), n.get("detalle")) if x)
                    lineas.append(
                        f'<tr><td style="padding:6px 0 0;vertical-align:top;width:24px;font-size:15px">{n["icono"]}</td>'
                        f'<td style="padding:6px 0 0;font-size:14px;color:#101828"><strong>{_e(n["tipo"])}</strong>'
                        + (f'<div style="font-size:13px;color:#475467;line-height:1.45">{extra}</div>' if extra else "")
                        + "</td></tr>")
                else:
                    lineas.append(f'<tr><td style="padding:6px 0 0;width:24px">•</td>'
                                  f'<td style="padding:6px 0 0;font-size:13px;color:#475467">{_e(n["texto"])}</td></tr>')
            filas.append(
                f'<tr><td style="padding:12px 24px;border-top:1px solid #eaecf0">'
                f'<div style="font-size:15px;font-weight:700;color:#101828">{_e(marca or "acta " + acta)}'
                f' <span style="font-weight:400;color:#98a2b3;font-size:13px">acta {_e(acta)}</span></div>'
                f'<div style="font-size:13px;color:#475467">{_e(cliente)}</div>'
                f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:4px">{"".join(lineas)}</table>'
                f"</td></tr>")

    partes = []
    if alertas:
        partes.append(f"{len(alertas)} alerta{'s' if len(alertas) != 1 else ''}")
    if plazos:
        partes.append(f"{len(plazos)} plazo{'s' if len(plazos) != 1 else ''}")
    if novedades:
        partes.append(f"{len(novedades)} novedad{'es' if len(novedades) != 1 else ''}")
    titulo = "Cartera y vigilancia: " + " · ".join(partes)
    return titulo, mm.marco(
        "".join(filas), etiqueta="Cartera y vigilancia", titulo=titulo,
        subtitulo="Lo nuevo en las marcas de los clientes desde el último aviso.",
        botones=[("Abrir Clientes y vigilancia", f"{panel_url}/clientes?vista=vigilancia")],
        pie="Fechas orientativas: confirmar siempre en el expediente.", panel_url=panel_url)


def armar_mail_bloqueo(proceso, motivo, ok, bloqueadas, link=""):
    """Aviso de bloqueo masivo del WAF de INPI -> (asunto, html)."""
    e = html.escape
    cuerpo = mm.bloque(
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;background:#fef3f2;border:1px solid #fecdca;border-radius:8px">'
        f'<tr><td style="padding:12px 14px;font-size:14px;line-height:1.6;color:#101828">'
        f'Proceso: <b>{e(str(proceso))}</b><br>Detalle: {e(str(motivo))}<br>'
        f'Consultas OK en esta corrida: <b>{ok}</b> · bloqueadas: <b style="color:#b42318">{bloqueadas}</b></td></tr></table>'
        f'<p style="margin:14px 0 0">Las marcas afectadas quedaron como «no verificadas» y los reintentos las vuelven a '
        f'probar solas. Si este aviso se repite en varias corridas seguidas, es un bloqueo '
        f'sostenido: conviene pausar los workflows un día y bajar el ritmo de consultas.</p>'
    )
    asunto = f"⚠️ INPI está bloqueando consultas ({proceso})"
    return asunto, mm.marco(cuerpo, etiqueta="Sistema", titulo="INPI está bloqueando las consultas del sistema",
                            botones=[("Ver la corrida en GitHub", link)] if link else (), color_etiqueta="#b42318")


def armar_mail_vistas_sin_entender(items, link=""):
    """Aviso de vistas que el lector de PDF no supo interpretar -> (asunto, html).
    items: [{acta, marca, tipo, problemas: [str], texto: str}]"""
    filas = ""
    for it in items:
        problemas = "; ".join(it.get("problemas") or [])
        texto = (it.get("texto") or "").strip()
        filas += (
            f"<tr><td style=\"padding:8px;border-top:1px solid #eaecf0;vertical-align:top\">"
            f"<b>{html.escape(str(it.get('marca') or '—'))}</b><br>acta {html.escape(str(it['acta']))}</td>"
            f"<td style=\"padding:8px;border-top:1px solid #eaecf0;vertical-align:top\">{html.escape(problemas)}"
            f"<br><span style=\"color:#667085\">tipo leído: {html.escape(str(it.get('tipo') or '—'))}</span>"
            + (f"<div style=\"margin-top:6px;padding:6px;background:#f9fafb;color:#475467;font-size:12px\">"
               f"{html.escape(texto[:700])}{'…' if len(texto) > 700 else ''}</div>" if texto else "")
            + "</td></tr>"
        )
    n = len(items)
    cuerpo = mm.bloque(
        f"<p style=\"margin:0 0 10px\">El lector de PDF no encontró el tipo, el artículo o el plazo. La vista quedó guardada con lo que "
        f"se pudo leer (y el texto completo, para poder corregir el lector); conviene mirarla a mano en la Grilla "
        f"y avisar para ajustar la lectura.</p>"
        f"<table cellpadding=\"0\" cellspacing=\"0\" style=\"width:100%;border-collapse:collapse;font-size:14px\">"
        f"<tr><th align=\"left\" style=\"padding:8px\">Marca</th><th align=\"left\" style=\"padding:8px\">Qué no se entendió</th></tr>"
        f"{filas}</table>"
    )
    titulo = f"{n} vista{'s' if n != 1 else ''} que el sistema no supo interpretar del todo"
    return f"🔎 {n} vista{'s' if n != 1 else ''} que el sistema no supo leer", mm.marco(
        cuerpo, etiqueta="Vistas", titulo=titulo, botones=[("Ver la corrida en GitHub", link)] if link else (),
        color_etiqueta="#b54708")


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
            {"cliente_nombre": "Estudio Aurora", "denominacion": "AURORA", "acta": "4600123", "icono": "🏅",
             "tipo": "Concedida", "explicacion": "El INPI concedió la marca.", "detalle": ""},
            {"cliente_nombre": "Estudio Aurora", "denominacion": "AURORA", "acta": "4600123", "icono": "✍️",
             "tipo": "Se contestó la vista", "explicacion": "", "detalle": "En el expediente el 20/11/2026"},
        ]
        return armar_html_cartera(alertas, plazos, novedades, panel_url)
    if clave == "bloqueo":
        return armar_mail_bloqueo("Escanear actas nuevas", "7 consultas seguidas bloqueadas", 0, 7,
                                  "https://github.com/panel-check/panel-check/actions")
    if clave == "vista_sin_entender":
        return armar_mail_vistas_sin_entender([
            {"acta": "4800811", "marca": "ANIMA", "tipo": "desconocido",
             "problemas": ["no se reconoce el tipo de vista", "no se encontró el plazo para contestar"],
             "texto": "Dese vista al interesado de lo establecido en el artículo 3, inciso a), de la Ley 22.362 …"},
        ], "https://github.com/panel-check/panel-check/actions")
    if clave == "agenda":
        import calendario_core
        return calendario_core.ejemplo_agenda(panel_url)
    raise KeyError(clave)
