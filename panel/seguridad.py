"""
Constantes de seguridad del panel (sin dependencias, para poder importarlas desde
cualquier lado y probarlas por separado).

CSP (Content-Security-Policy): le dice al navegador de dónde puede cargar cosas
la página. Se arma a partir de lo que el panel realmente usa:
  - scripts y estilos propios (el panel usa JavaScript y estilos escritos dentro
    de las páginas, por eso 'unsafe-inline'),
  - Cloudflare Turnstile (captcha del formulario público),
  - Google Fonts (solo el formulario público),
  - imágenes propias y data:/blob: (descargas y vistas previas),
  - el formulario «Ver ficha» manda un POST a portaltramites.inpi.gob.ar.
Todo lo demás (scripts de otros sitios, pedidos a otros dominios, iframes
ajenos, <base> trucado) queda bloqueado: si algún día se colara código en una
página, no podría cargar nada de afuera ni mandar datos a otro dominio.
"""

import os


KAN_URL_DEFECTO = "https://fulfilling-achievement-production-7586.up.railway.app"


def kan_url() -> str:
    """Dirección de Kan (tablero de tareas), sin barra final."""
    return (os.environ.get("KAN_URL") or KAN_URL_DEFECTO).rstrip("/")


CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' data: https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    f"frame-src 'self' blob: https://challenges.cloudflare.com {kan_url()}",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self' https://portaltramites.inpi.gob.ar",
    "frame-ancestors 'self'",
])

# Páginas HTML de /static que se pueden abrir sin iniciar sesión: la pantalla de
# ingreso y el formulario público para clientes. Las demás (Ayuda, Crons, Mails,
# Clientes, etc.) solo se sirven con sesión, igual que sus rutas (/ayuda, /crons...).
HTML_PUBLICOS = {"/static/login.html", "/static/formulario.html"}


def es_html_privado(ruta: str) -> bool:
    """True si la ruta es una página HTML de /static que exige sesión."""
    return ruta.startswith("/static/") and ruta.lower().endswith(".html") and ruta not in HTML_PUBLICOS
