"""
Configuración de los mails del sistema (pestaña "Mails" del panel).

Qué guarda (tabla mails_config, una fila por tipo de mail):
  - cuenta       "interna" o "prospectos": de qué cuenta de Resend sale. La API
                 key NUNCA se guarda en la base: cada cuenta apunta a una
                 variable de entorno (CUENTAS[...]["env"]) que vive en los
                 secrets de GitHub y en las variables de Railway.
  - remitente    "Nombre <mail@dominio>" o "mail@dominio" (dominio verificado
                 en esa cuenta de Resend).
  - responder_a  Reply-To (opcional).
  - destinatarios  solo para los avisos internos.
  - asunto / cuerpo  solo para los mails con plantilla editable (prospectos).

Los avisos internos arman su cuerpo con datos en vivo (ver mails_plantillas.py):
acá se configuran cuenta, remitente, reply-to y destinatarios, y se ve una vista
previa con datos de ejemplo.

Precedencia de cada valor: lo guardado en el panel > variable de entorno vieja
(RESEND_FROM, NOTIFICAR_A) > valor por defecto del catálogo. Si la base no
responde, los scripts siguen con env/defaults: un aviso nunca se cae por esto.

Este módulo lo usan el panel (mails_api.py) y los scripts de aviso, que lo
importan agregando panel/ al path.
"""

import html
import os
import re
import sys

import psycopg2
import psycopg2.extras
import requests

DEFAULT_PANEL = "https://panel.registrodemimarca.com.ar"
DEFAULT_FROM_INTERNO = "Avisos Panel <avisos@quieroregistrarmimarca.com.ar>"
DEFAULT_TO_INTERNO = "marcas@komunikacion.com.ar"

# Cuentas de Resend. "env" es el NOMBRE de la variable con la API key (se lee
# solo de esta lista fija, nunca de un nombre que venga del panel).
CUENTAS = {
    "interna": {
        "nombre": "Interna (avisos al equipo)",
        "env": "RESEND_API_KEY",
    },
    "prospectos": {
        "nombre": "Prospectos (contacto con leads)",
        "env": "RESEND_API_KEY_PROSPECTOS",
    },
}

# Variables que se pueden usar en la plantilla de prospectos: {{variable}}.
VARIABLES_PROSPECTO = {
    "titular": "Nombre del titular de la solicitud",
    "marca": "Denominación de la marca",
    "acta": "Número de acta",
    "clase": "Clase de la solicitud",
}

EJEMPLO_PROSPECTO = {
    "titular": "María Gómez",
    "marca": "Luna Nueva",
    "acta": "4797123",
    "clase": "25",
}

# Línea de baja: va SIEMPRE al final de los mails a prospectos y no se edita.
# Encabezado fijo de los mails a prospectos: franja azul con dos líneas
# doradas y el título en blanco, a todo el ancho del mail. Es HTML (no una
# imagen) para que se vea nítido y no dependa de que se carguen imágenes.
ENCABEZADO_TITULO = "BOLETÍN DE MARCAS Y PATENTES"
COLOR_AZUL = "#133465"
COLOR_DORADO = "#d9b05b"

PIE_BAJA = "Si no querés recibir más mensajes nuestros, respondé BAJA y no te escribimos más."

ASUNTO_PROSPECTO = "Tu solicitud de marca {{marca}} (acta {{acta}})"
CUERPO_PROSPECTO = """Hola {{titular}},

Vimos que presentaste la solicitud de la marca «{{marca}}» (acta {{acta}}, clase {{clase}}) en el INPI sin un agente de la propiedad industrial.

Somos Smarties Consultora y trabajamos con agentes matriculados. Podemos acompañarte en lo que sigue del trámite: vistas del INPI, oposiciones de terceros y la concesión del título.

Si te interesa, respondé este mail y lo vemos sin compromiso.

Saludos,
Smarties Consultora"""

ASUNTO_MARCA_PUBLICADA = "¡Felicitaciones! Su marca {{marca}} ya fue publicada"
CUERPO_MARCA_PUBLICADA = """Hola, {{titular}}:

¡Felicitaciones! Les escribimos porque su marca {{marca}} (Acta N.º {{acta}}, clase {{clase}}) fue publicada en el Boletín de Marcas del INPI. Es un paso importante: significa que la solicitud ya superó el Estudio Fondo IA.

A partir de ahora corren 30 días para que terceros puedan oponerse. Si nadie lo hace, el trámite sigue su curso hacia la concesión.

Queríamos contarles algo que mucha gente todavía no sabe y que puede afectar a su marca a futuro.

Desde diciembre de 2025 (Resolución INPI 583/2025), el INPI ya no frena por su cuenta las marcas parecidas a otras anteriores. Solo rechaza de oficio las idénticas para los mismos productos o servicios. Si mañana alguien pide una marca similar a la de ustedes, el INPI puede concedérsela, salvo que ustedes se opongan a tiempo. Y para oponerse, primero hay que enterarse.

Ahí es donde podemos darles una mano. Cada semana revisamos todas las marcas nuevas que se publican y las comparamos con las que cuidamos. Si aparece algo idéntico o que pueda generar confusión, les avisamos enseguida para que decidan con tiempo qué hacer.

También les recordamos las fechas que suelen pasarse por alto: la Declaración Jurada de Uso a los 5 años de la concesión y la renovación del registro cada 10 años. Y si cambia algo en la normativa que los afecte, se los contamos.

Somos [NOMBRE DEL ESTUDIO], un equipo de abogados y agentes de la propiedad industrial matriculados ante el INPI. Nos gusta trabajar de cerca con cada cliente, sin vueltas ni letra chica.

Si quieren, armamos una charla corta para contarles cómo funciona y cuánto cuesta, sin ningún compromiso. Pueden responder este mail o escribirnos por WhatsApp.

¡Mucho éxito con {{marca}}!

Un saludo,
FIRMA

P.D.: Pueden seguir el estado del trámite en el portal del INPI: https://portaltramites.inpi.gob.ar/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADEA"""

CATALOGO = {
    "oposiciones": {
        "grupo": "interno",
        "nombre": "Oposiciones y vistas",
        "descripcion": "Resumen diario con los leads que recibieron una oposición de tercero o una vista de INPI.",
        "cuenta": "interna",
        "remitente": DEFAULT_FROM_INTERNO,
        "responder_a": "",
        "destinatarios": DEFAULT_TO_INTERNO,
        "env_remitente": "RESEND_FROM",
        "env_destinatarios": "NOTIFICAR_A",
    },
    "cartera": {
        "grupo": "interno",
        "nombre": "Cartera y vigilancia",
        "descripcion": "Alertas de vigilancia, plazos que se acercan y novedades de los expedientes de los clientes.",
        "cuenta": "interna",
        "remitente": DEFAULT_FROM_INTERNO,
        "responder_a": "",
        "destinatarios": DEFAULT_TO_INTERNO,
        "env_remitente": "RESEND_FROM",
        "env_destinatarios": "NOTIFICAR_A",
    },
    "formularios": {
        "grupo": "interno",
        "nombre": "Formulario de cliente recibido",
        "descripcion": "Un mail por cada formulario que completa un cliente, con sus respuestas y archivos.",
        "cuenta": "interna",
        "remitente": DEFAULT_FROM_INTERNO,
        "responder_a": "",
        # Este mail responde solo al email que dejó el cliente en el formulario.
        "responder_a_automatico": "el email del cliente que completó el formulario",
        "destinatarios": DEFAULT_TO_INTERNO,
        "env_remitente": "RESEND_FROM",
        "env_destinatarios": "NOTIFICAR_A",
    },
    "bloqueo": {
        "grupo": "interno",
        "nombre": "INPI está bloqueando",
        "descripcion": "Alarma cuando INPI bloquea en forma masiva las consultas de los procesos automáticos.",
        "cuenta": "interna",
        "remitente": DEFAULT_FROM_INTERNO,
        "responder_a": "",
        "destinatarios": DEFAULT_TO_INTERNO,
        "env_remitente": "RESEND_FROM",
        "env_destinatarios": "NOTIFICAR_A",
    },
    "prospecto_primer_contacto": {
        "grupo": "prospectos",
        "nombre": "Primer contacto a un lead",
        "descripcion": "Mail para presentarse a un solicitante que presentó su marca sin agente. Todavía no se envía nada: se deja configurado.",
        "cuenta": "prospectos",
        "remitente": "Smarties <smarties@registrodemimarca.com.ar>",
        "responder_a": "info@smartiesconsultora.com.ar",
        "destinatarios": "",
        "asunto": ASUNTO_PROSPECTO,
        "cuerpo": CUERPO_PROSPECTO,
        "editable": True,
    },
    "prospecto_marca_publicada": {
        "grupo": "prospectos",
        "nombre": "Marca publicada (vigilancia)",
        "descripcion": "Felicita al titular cuando su marca sale publicada en el Boletín, explica el cambio de la Resolución INPI 583/2025 y ofrece el servicio de vigilancia. Todavía no se envía nada: se deja configurado.",
        "cuenta": "prospectos",
        "remitente": "Smarties <smarties@registrodemimarca.com.ar>",
        "responder_a": "info@smartiesconsultora.com.ar",
        "destinatarios": "",
        "asunto": ASUNTO_MARCA_PUBLICADA,
        "cuerpo": CUERPO_MARCA_PUBLICADA,
        "editable": True,
    },
}

_RE_EMAIL = re.compile(r"^[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+$")
_RE_REMITENTE = re.compile(r"^(?:[^<>\r\n]{1,100}\s)?<([^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+)>$")
# Links en el cuerpo de las plantillas (se buscan sobre el texto ya escapado).
_RE_URL = re.compile(r'https?://[^\s<>"]+[^\s<>".,;:)]')
_RE_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


# ── Tablas ────────────────────────────────────────────────────────────────
def crear_tablas(cur):
    """Idempotente."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mails_config (
            clave          TEXT PRIMARY KEY,
            cuenta         TEXT,
            remitente      TEXT,
            responder_a    TEXT,
            destinatarios  TEXT,
            asunto         TEXT,
            cuerpo         TEXT,
            actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
            actualizado_por TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mails_bajas (
            email      TEXT PRIMARY KEY,
            motivo     TEXT,
            creada_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
            creada_por TEXT
        )
        """
    )


# ── Validaciones ──────────────────────────────────────────────────────────
def email_de_remitente(remitente: str) -> str | None:
    """'Nombre <a@b.com>' o 'a@b.com' -> 'a@b.com' (None si no es válido)."""
    r = (remitente or "").strip()
    if _RE_EMAIL.match(r):
        return r
    m = _RE_REMITENTE.match(r)
    return m.group(1) if m else None


def lista_de_emails(texto: str) -> list:
    return [d.strip() for d in (texto or "").split(",") if d.strip()]


def variables_usadas(texto: str) -> set:
    return {m.group(1) for m in _RE_VARIABLE.finditer(texto or "")}


def validar_config(clave: str, valores: dict) -> dict:
    """Devuelve los valores limpios o levanta ValueError con un mensaje claro."""
    if clave not in CATALOGO:
        raise ValueError("Tipo de mail inexistente")
    cat = CATALOGO[clave]
    out = {}

    cuenta = (valores.get("cuenta") or cat["cuenta"]).strip()
    if cuenta not in CUENTAS:
        raise ValueError("Cuenta de Resend inexistente")
    out["cuenta"] = cuenta

    remitente = (valores.get("remitente") or "").strip()
    if not remitente or not email_de_remitente(remitente):
        raise ValueError('El remitente tiene que ser un mail válido, por ejemplo "Avisos <avisos@dominio.com>" o "avisos@dominio.com"')
    out["remitente"] = remitente

    responder_a = (valores.get("responder_a") or "").strip()
    if responder_a and not _RE_EMAIL.match(responder_a):
        raise ValueError("El Reply-To tiene que ser un mail válido")
    out["responder_a"] = responder_a

    if cat["grupo"] == "interno":
        destinos = lista_de_emails(valores.get("destinatarios"))
        if not destinos:
            raise ValueError("Falta al menos un destinatario")
        malos = [d for d in destinos if not _RE_EMAIL.match(d)]
        if malos:
            raise ValueError(f"Destinatario inválido: {malos[0]}")
        out["destinatarios"] = ", ".join(destinos)
    else:
        out["destinatarios"] = ""

    if cat.get("editable"):
        asunto = (valores.get("asunto") or "").strip()
        cuerpo = (valores.get("cuerpo") or "").strip()
        if not asunto:
            raise ValueError("Falta el asunto")
        if not cuerpo:
            raise ValueError("Falta el cuerpo del mail")
        if len(asunto) > 200 or len(cuerpo) > 8000:
            raise ValueError("El asunto (200) o el cuerpo (8000 caracteres) es demasiado largo")
        desconocidas = (variables_usadas(asunto) | variables_usadas(cuerpo)) - set(VARIABLES_PROSPECTO)
        if desconocidas:
            raise ValueError("Variable inexistente: {{" + sorted(desconocidas)[0] + "}}. "
                             "Disponibles: " + ", ".join("{{" + v + "}}" for v in VARIABLES_PROSPECTO))
        out["asunto"], out["cuerpo"] = asunto, cuerpo
    return out


def dominios_verificados(cuenta: str):
    """Set de dominios verificados en esa cuenta de Resend, o None si no se
    puede saber (sin clave en este servidor, clave solo de envío, sin red)."""
    api_key = os.environ.get(CUENTAS[cuenta]["env"])
    if not api_key:
        return None
    try:
        r = requests.get("https://api.resend.com/domains", headers={"Authorization": f"Bearer {api_key}"}, timeout=8)
        if r.status_code != 200:
            return None
        return {d["name"].lower() for d in r.json().get("data", []) if d.get("status") == "verified"}
    except Exception:  # noqa: BLE001 — es un chequeo de cortesía, no bloquea
        return None


def validar_dominio(cuenta: str, remitente: str):
    """Si se puede consultar a Resend, el dominio del remitente tiene que estar
    verificado en esa cuenta. Levanta ValueError si no lo está."""
    dominios = dominios_verificados(cuenta)
    if dominios is None:
        return
    dominio = email_de_remitente(remitente).rsplit("@", 1)[1].lower()
    if dominio not in dominios:
        raise ValueError(f"El dominio {dominio} no está verificado en la cuenta «{CUENTAS[cuenta]['nombre']}» de Resend. "
                         f"Dominios verificados: {', '.join(sorted(dominios)) or 'ninguno'}.")


# ── Lectura de la configuración ───────────────────────────────────────────
def _dsn(dsn=None):
    return dsn or os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")


def _fila_guardada(clave: str, dsn=None) -> dict:
    d = _dsn(dsn)
    if not d:
        return {}
    try:
        conn = psycopg2.connect(d, connect_timeout=10)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM mails_config WHERE clave = %s", (clave,))
                return cur.fetchone() or {}
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — tabla todavía inexistente o base caída: se usan los valores por defecto
        print(f"[mails] no se pudo leer la configuración de «{clave}» (se usan los valores por defecto): {e}", file=sys.stderr)
        return {}


def combinar(clave: str, guardada: dict) -> dict:
    """Valores efectivos: guardado > env viejo > defecto del catálogo."""
    cat = CATALOGO[clave]
    g = guardada or {}
    cuenta = g.get("cuenta") if g.get("cuenta") in CUENTAS else cat["cuenta"]
    remitente = g.get("remitente") or (os.environ.get(cat.get("env_remitente", "")) if cat.get("env_remitente") else None) or cat["remitente"]
    destinatarios = g.get("destinatarios") or (os.environ.get(cat.get("env_destinatarios", "")) if cat.get("env_destinatarios") else None) or cat["destinatarios"]
    responder_a = g["responder_a"] if g.get("responder_a") is not None else cat["responder_a"]
    cfg = {
        "clave": clave,
        "cuenta": cuenta,
        "remitente": remitente,
        "responder_a": responder_a or "",
        "destinatarios": destinatarios,
        "destinatarios_lista": lista_de_emails(destinatarios),
        "actualizado_en": g.get("actualizado_en"),
        "actualizado_por": g.get("actualizado_por"),
        "personalizado": bool(g),
    }
    if cat.get("editable"):
        cfg["asunto"] = g.get("asunto") or cat["asunto"]
        cfg["cuerpo"] = g.get("cuerpo") or cat["cuerpo"]
    return cfg


def leer_config(clave: str, dsn=None) -> dict:
    return combinar(clave, _fila_guardada(clave, dsn))


def preparar(clave: str, dsn=None) -> dict:
    """Todo lo que necesita un script para mandar un aviso:
    {api_key, env_key, remitente, responder_a, destinatarios, cuenta}.
    api_key es None si la variable de esa cuenta no está definida."""
    cfg = leer_config(clave, dsn)
    env_key = CUENTAS[cfg["cuenta"]]["env"]
    return {
        "cuenta": cfg["cuenta"],
        "env_key": env_key,
        "api_key": os.environ.get(env_key),
        "remitente": cfg["remitente"],
        "responder_a": cfg["responder_a"] or None,
        "destinatarios": cfg["destinatarios_lista"],
    }


# ── Plantilla de prospectos ───────────────────────────────────────────────
def _sustituir(texto: str, datos: dict, escapar: bool) -> str:
    def reemplazo(m):
        v = str(datos.get(m.group(1), ""))
        return html.escape(v) if escapar else v
    return _RE_VARIABLE.sub(reemplazo, texto)


def render_prospecto(asunto: str, cuerpo: str, datos: dict) -> tuple:
    """(asunto, html, texto) con las variables reemplazadas, el encabezado fijo
    arriba y la línea de baja obligatoria al final. El cuerpo se escribe como texto: los saltos de línea
    se respetan y todo el contenido se escapa (no se interpreta HTML)."""
    asunto_final = _sustituir(asunto, datos, escapar=False).replace("\r", " ").replace("\n", " ").strip()
    texto = _sustituir(cuerpo, datos, escapar=False).strip() + "\n\n--\n" + PIE_BAJA
    parrafos = []
    for bloque in _sustituir(html.escape(cuerpo, quote=False), datos, escapar=True).strip().split("\n\n"):
        bloque = _RE_URL.sub(lambda m: f'<a href="{m.group(0)}" style="color:#1d4ed8">{m.group(0)}</a>', bloque)
        parrafos.append('<p style="margin:0 0 14px">' + bloque.replace("\r", "").replace("\n", "<br>") + "</p>")
    linea = (f'<tr><td height="3" style="height:3px;background:{COLOR_DORADO};font-size:0;line-height:0;'
             f'mso-line-height-rule:exactly">&nbsp;</td></tr>')
    encabezado = (
        f'<tr><td style="background:{COLOR_AZUL};padding:22px 22px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        + linea
        + '<tr><td align="center" style="padding:16px 4px;color:#ffffff;'
          "font-family:Montserrat,'Helvetica Neue',Arial,Helvetica,sans-serif;font-size:22px;line-height:1.25;"
          f'font-weight:700;letter-spacing:1px;text-align:center">{html.escape(ENCABEZADO_TITULO)}</td></tr>'
        + linea
        + "</table></td></tr>"
    )
    cuerpo_html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@700&display=swap" rel="stylesheet">'
        '</head><body style="margin:0;padding:0;background:#eef1f5">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eef1f5">'
        '<tr><td align="center" style="padding:24px 0">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;max-width:600px;background:#ffffff;border-collapse:collapse">'
        + encabezado
        + '<tr><td style="padding:28px 28px 24px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;color:#1f2430;word-break:break-word;overflow-wrap:anywhere">'
        + "".join(parrafos)
        + f'<p style="margin:22px 0 0;padding-top:12px;border-top:1px solid #e2e5ea;font-size:12px;color:#6b7280">{html.escape(PIE_BAJA)}</p>'
        + "</td></tr></table></td></tr></table></body></html>"
    )
    return asunto_final, cuerpo_html, texto


def datos_de_marca(cur, acta: str):
    """Variables de la plantilla de prospectos con los datos REALES de una
    marca de la base ({titular, marca, acta, clase}), o None si no existe.
    Se usa para la vista previa y el envío de prueba con una marca elegida."""
    cur.execute("SELECT acta, clase, titular, denominacion, denominacion_inpi FROM marcas WHERE acta = %s",
                ((acta or "").strip(),))
    f = cur.fetchone()
    if not f:
        return None
    return {
        "titular": f["titular"] or "",
        "marca": f["denominacion_inpi"] or f["denominacion"] or "",
        "acta": f["acta"],
        "clase": "" if f["clase"] is None else str(f["clase"]),
    }


def enviar_test(cuenta: str, remitente: str, responder_a: str, para: list, asunto: str, html_cuerpo: str, texto: str) -> str:
    """Manda un mail de PRUEBA a las direcciones que se eligieron a mano en el
    panel (nunca al email de la marca). Devuelve el id de Resend."""
    env_key = CUENTAS[cuenta]["env"]
    api_key = os.environ.get(env_key)
    if not api_key:
        raise ValueError(f"La variable {env_key} no está cargada en el servidor del panel (Railway): sin esa clave no se puede mandar la prueba.")
    payload = {"from": remitente, "to": para, "subject": "[TEST] " + asunto, "html": html_cuerpo, "text": texto}
    if responder_a:
        payload["reply_to"] = responder_a
    r = requests.post("https://api.resend.com/emails",
                      headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                      json=payload, timeout=30)
    if r.status_code >= 300:
        raise ValueError(f"Resend rechazó el envío ({r.status_code}): {r.text[:300]}")
    return r.json().get("id") or ""


def esta_de_baja(cur, email: str) -> bool:
    cur.execute("SELECT 1 FROM mails_bajas WHERE email = %s", ((email or "").strip().lower(),))
    return cur.fetchone() is not None


# ── Vista previa ──────────────────────────────────────────────────────────
def vista_previa(clave: str, cfg: dict, panel_url: str = DEFAULT_PANEL, datos: dict = None) -> dict:
    """{asunto, html} del mail (para la pestaña Mails). En las plantillas de
    prospectos, `datos` son los de una marca real elegida; si no hay, se usan
    los de ejemplo."""
    import mails_plantillas as mp

    if CATALOGO[clave].get("editable"):
        asunto, cuerpo, _ = render_prospecto(cfg["asunto"], cfg["cuerpo"], datos or EJEMPLO_PROSPECTO)
        return {"asunto": asunto, "html": cuerpo}
    if clave == "formularios":
        import formularios_core as fc
        slug = "persona-fisica"
        datos = {}
        for c in fc.FORMULARIOS[slug]["campos"]:
            if c["tipo"] in ("nota", "archivo"):
                continue
            if c["tipo"] in ("opcion", "lista"):
                datos[c["id"]] = c["opciones"][0]
            elif c["tipo"] == "email":
                datos[c["id"]] = "cliente@ejemplo.com"
            else:
                datos[c["id"]] = "Texto de ejemplo"
        datos["nombre"] = "María Gómez"
        datos["marca_nombre"] = "Luna Nueva"
        resp = {"formulario": slug, "tipo_persona": "fisica", "datos": datos, "cliente_id": None, "cliente_nuevo": True}
        asunto, cuerpo, _ = fc.armar_mail(resp, {}, [], panel_url)
        return {"asunto": asunto, "html": cuerpo}
    asunto, cuerpo = mp.ejemplo(clave, panel_url)
    return {"asunto": asunto, "html": cuerpo}
