"""Marco común de los mails internos (avisos al equipo), desde el 22/11/2026.

Todos los avisos internos (Cartera y vigilancia, formulario recibido, consulta de la web, INPI bloqueando,
vista que no se pudo leer, agenda diaria, oposiciones) usan el mismo diseño: franja azul de Smarties, logo,
etiqueta del tipo de aviso, título, botón al panel y pie. HTML de tablas con estilos en línea, para que se
vea igual en Gmail, Outlook y el celular.
"""

import html

LOGO_URL = "https://panel.registrodemimarca.com.ar/static/logo-smarties-header.png"
AZUL = "#015197"
AZUL_CLARO = "#e8f1fa"
TEXTO = "#101828"
GRIS = "#475467"
GRIS_CLARO = "#98a2b3"
BORDE = "#eaecf0"
FONDO = "#f2f4f7"
FUENTE = "Arial,Helvetica,sans-serif"


def boton(texto: str, url: str, secundario: bool = False) -> str:
    if secundario:
        estilo = f"background:#ffffff;color:{AZUL};border:1px solid {AZUL}"
    else:
        estilo = f"background:{AZUL};color:#ffffff;border:1px solid {AZUL}"
    return (f'<a href="{html.escape(url)}" style="display:inline-block;{estilo};text-decoration:none;'
            f'padding:9px 16px;border-radius:8px;font-weight:700;font-size:14px;font-family:{FUENTE};'
            f'margin:0 6px 6px 0">{html.escape(texto)}</a>')


def seccion(titulo: str) -> str:
    """Fila de título de sección (va dentro del contenido)."""
    return (f'<tr><td style="padding:16px 24px 6px;font-size:12px;font-weight:700;color:{AZUL};'
            f'text-transform:uppercase;letter-spacing:.06em;border-top:1px solid {BORDE};background:#f8fafc">'
            f'{html.escape(titulo)}</td></tr>')


def bloque(contenido_html: str, padding: str = "14px 24px") -> str:
    """Envuelve HTML suelto (párrafos, tablas) en una fila del marco."""
    return (f'<tr><td style="padding:{padding};font-size:14px;line-height:1.5;color:{TEXTO};font-family:{FUENTE}">'
            f'{contenido_html}</td></tr>')


def marco(contenido: str, *, etiqueta: str, titulo: str, subtitulo: str = "", botones=(), pie: str = "",
          panel_url: str = "", ancho: int = 640, color_etiqueta: str = AZUL) -> str:
    """Mail completo. contenido: filas <tr>…</tr> (usar bloque() para HTML suelto).
    botones: [(texto, url)]; el primero va lleno y los demás con borde."""
    e = html.escape
    btns = "".join(boton(t, u, secundario=i > 0) for i, (t, u) in enumerate(botones or []))
    link_panel = (f' · <a href="{e(panel_url)}" style="color:{GRIS_CLARO}">abrir el panel</a>' if panel_url else "")
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{FONDO};font-family:{FUENTE}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{FONDO}"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="{ancho}" cellpadding="0" cellspacing="0" style="max-width:{ancho}px;width:100%;background:#ffffff;border:1px solid {BORDE};border-radius:12px;overflow:hidden">
  <tr><td style="height:5px;line-height:5px;font-size:0;background:{AZUL}">&nbsp;</td></tr>
  <tr><td style="padding:18px 24px 14px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="vertical-align:middle"><img src="{LOGO_URL}" width="90" height="40" alt="Smarties" style="display:block;border:0;height:40px;width:90px"></td>
      <td align="right" style="vertical-align:middle"><span style="display:inline-block;background:{AZUL_CLARO};color:{color_etiqueta};font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:5px 10px;border-radius:999px">{e(etiqueta)}</span></td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:4px 24px 18px">
    <div style="font-size:22px;line-height:1.25;font-weight:700;color:{TEXTO}">{e(titulo)}</div>
    {f'<div style="font-size:14px;line-height:1.5;color:{GRIS};margin-top:6px">{subtitulo}</div>' if subtitulo else ''}
    {f'<div style="margin-top:14px">{btns}</div>' if btns else ''}
  </td></tr>
  {contenido}
  <tr><td style="padding:16px 24px;border-top:1px solid {BORDE};background:#f8fafc;color:{GRIS_CLARO};font-size:12px;line-height:1.5">
    {pie + '<br>' if pie else ''}Aviso interno automático del panel de Smarties{link_panel}
  </td></tr>
</table></td></tr></table></body></html>"""
