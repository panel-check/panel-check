"""
Formularios para clientes (Persona física / Persona jurídica).

Reemplazan a los Google Forms: el cliente abre un link público del panel
(/formulario/persona-fisica o /formulario/persona-juridica), lo completa sin
usuario ni clave y, al enviarlo:
  1. se guarda la respuesta completa (y los archivos adjuntos) en la base,
  2. se crea el cliente en la sección Clientes con todos sus datos — o, si ya
     había un cliente activo con ese CUIT, se le actualizan los datos,
  3. se avisa por mail al equipo con las respuestas y los archivos adjuntos.

Este módulo no depende de FastAPI: lo usan tanto el panel (formularios_api.py)
como el script de respaldo del aviso por mail (scripts/notificar_formularios.py).
"""

import base64
import html
import json
import re

import requests

import archivos_seguros

# ── Definición de los formularios ─────────────────────────────────────────
# Única fuente de verdad: la página pública se arma con esto (lo pide por
# /api/publico/formulario/{tipo}), el backend valida con esto y el mail y la
# ficha del cliente muestran las respuestas en este mismo orden.
#
# tipos de campo: texto, email, parrafo, opcion (botones), lista (desplegable),
#                 archivo, nota (texto fijo sin respuesta)
# "si": {"campo": x, "valor": y} → el campo solo se muestra (y solo es
#        obligatorio) cuando el campo x tiene el valor y.
# "cliente": a qué columna de la tabla clientes va la respuesta.

PROVINCIAS = [
    "Ciudad Autónoma de Buenos Aires", "Buenos Aires", "Catamarca", "Chaco", "Chubut", "Córdoba",
    "Corrientes", "Entre Ríos", "Formosa", "Jujuy", "La Pampa", "La Rioja", "Mendoza", "Misiones",
    "Neuquén", "Río Negro", "Salta", "San Juan", "San Luis", "Santa Cruz", "Santa Fe",
    "Santiago del Estero", "Tierra del Fuego", "Tucumán",
]

# El .doc viejo no se acepta: puede traer macros escondidas (ver archivos_seguros.py).
MIME_DOCS = ["application/pdf", "image/jpeg", "image/png", "image/webp",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]
MIME_LOGO = ["image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf"]
EXT_POR_MIME = {
    "application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
MAX_ARCHIVO = 10 * 1024 * 1024  # 10 MB, igual que en Google Forms

FORMULARIOS = {
    "persona-fisica": {
        "tipo_persona": "fisica",
        "titulo": "Formulario para Registro de Marca – Persona Física",
        "descripcion": "Completá este formulario si querés registrar la marca a tu nombre (como persona física).",
        "otro": {"slug": "persona-juridica", "texto": "¿La marca va a nombre de una empresa? Completá el formulario de Persona Jurídica"},
        "campos": [
            {"id": "email", "tipo": "email", "etiqueta": "Correo electrónico", "obligatorio": True, "cliente": "email"},
            {"id": "nombre", "tipo": "texto", "etiqueta": "Apellido y Nombre del titular", "obligatorio": True, "cliente": "nombre"},
            {"id": "nacionalidad", "tipo": "texto", "etiqueta": "Nacionalidad", "obligatorio": True, "cliente": "nacionalidad"},
            {"id": "dni", "tipo": "texto", "etiqueta": "DNI", "obligatorio": True, "cliente": "dni", "formato": "dni"},
            {"id": "cuit", "tipo": "texto", "etiqueta": "CUIL/CUIT", "obligatorio": True, "cliente": "cuit", "formato": "cuit"},
            {"id": "telefono", "tipo": "texto", "etiqueta": "Teléfono / WhatsApp", "obligatorio": False, "cliente": "telefono"},
            {"id": "domicilio", "tipo": "texto", "etiqueta": "Domicilio particular", "obligatorio": True, "cliente": "domicilio"},
            {"id": "localidad", "tipo": "texto", "etiqueta": "Localidad", "obligatorio": True, "cliente": "localidad"},
            {"id": "provincia", "tipo": "lista", "etiqueta": "Provincia", "obligatorio": True, "cliente": "provincia", "opciones": PROVINCIAS},
            {"id": "codigo_postal", "tipo": "texto", "etiqueta": "Código Postal", "obligatorio": True, "cliente": "codigo_postal"},
            {"id": "estado_civil", "tipo": "opcion", "etiqueta": "Estado Civil", "obligatorio": True, "cliente": "estado_civil",
             "opciones": ["Casado/a", "Soltero/a", "Viudo/a", "Divorciado/a"]},
            {"id": "domicilio_comercial", "tipo": "texto", "etiqueta": "Domicilio Comercial", "obligatorio": False, "cliente": "domicilio_comercial"},
            {"id": "nota_conyuge", "tipo": "nota", "etiqueta": "En caso de ser casado/a",
             "ayuda": "Los derechos intelectuales son bienes propios del autor o inventor, pero el producido de ellos "
                      "durante la vigencia de la sociedad conyugal es ganancial.",
             "si": {"campo": "estado_civil", "valor": "Casado/a"}},
            {"id": "conyuge_nombre", "tipo": "texto", "etiqueta": "Nombre y apellido del cónyuge", "obligatorio": False,
             "cliente": "conyuge_nombre", "si": {"campo": "estado_civil", "valor": "Casado/a"}},
            {"id": "conyuge_dni", "tipo": "texto", "etiqueta": "DNI del cónyuge", "obligatorio": False,
             "cliente": "conyuge_dni", "formato": "dni", "si": {"campo": "estado_civil", "valor": "Casado/a"}},
            {"id": "marca_nombre", "tipo": "texto", "etiqueta": "Nombre de la marca a registrar", "obligatorio": True},
            {"id": "marca_descripcion", "tipo": "parrafo", "etiqueta": "Describa de manera sencilla los servicios y/o productos que brinda su marca",
             "obligatorio": False},
            {"id": "tiene_logo", "tipo": "opcion", "etiqueta": "¿Tiene logo?", "obligatorio": True, "opciones": ["SI", "NO"]},
            {"id": "logo", "tipo": "archivo", "etiqueta": "Adjunte su logo en el tamaño 4x4", "obligatorio": True,
             "ayuda": "Imagen (JPG, PNG, WEBP, GIF) o PDF. Máximo 10 MB.", "mimes": MIME_LOGO,
             "si": {"campo": "tiene_logo", "valor": "SI"}},
        ],
    },
    "persona-juridica": {
        "tipo_persona": "juridica",
        "titulo": "Formulario para Registro de Marca – Persona Jurídica",
        "descripcion": "Completá este formulario si querés registrar la marca a nombre de una empresa.",
        "otro": {"slug": "persona-fisica", "texto": "¿La marca va a tu nombre? Completá el formulario de Persona Física"},
        "campos": [
            {"id": "email", "tipo": "email", "etiqueta": "Correo electrónico", "obligatorio": True, "cliente": "email"},
            {"id": "nombre", "tipo": "texto", "etiqueta": "Razón social", "obligatorio": True, "cliente": "nombre"},
            {"id": "cuit", "tipo": "texto", "etiqueta": "CUIT", "obligatorio": True, "cliente": "cuit", "formato": "cuit"},
            {"id": "telefono", "tipo": "texto", "etiqueta": "Teléfono / WhatsApp", "obligatorio": False, "cliente": "telefono"},
            {"id": "firmante_nombre", "tipo": "texto", "etiqueta": "Nombre y apellido de quien firma por la empresa", "obligatorio": True,
             "cliente": "firmante_nombre", "ayuda": "La persona que va a firmar el poder (representante legal o apoderado)."},
            {"id": "firmante_cargo", "tipo": "texto", "etiqueta": "Cargo de quien firma", "obligatorio": True,
             "cliente": "firmante_cargo", "ayuda": "Por ejemplo: Socio gerente, Presidente, Apoderado."},
            {"id": "constancia_cuit", "tipo": "archivo", "etiqueta": "Constancia de CUIT", "obligatorio": True,
             "ayuda": "PDF, imagen (JPG, PNG) o Word (.docx). Máximo 10 MB.", "mimes": MIME_DOCS},
            {"id": "domicilio", "tipo": "texto", "etiqueta": "Domicilio completo", "obligatorio": True, "cliente": "domicilio",
             "ayuda": "Calle, número, piso/depto y localidad."},
            {"id": "provincia", "tipo": "lista", "etiqueta": "Provincia", "obligatorio": True, "cliente": "provincia", "opciones": PROVINCIAS},
            {"id": "estatuto", "tipo": "archivo", "etiqueta": "Estatuto / contrato social / poder", "obligatorio": True,
             "ayuda": "PDF, imagen (JPG, PNG) o Word (.docx). Máximo 10 MB.", "mimes": MIME_DOCS},
        ],
    },
}

TIPO_LEGIBLE = {"fisica": "Persona física", "juridica": "Persona jurídica"}

# Columnas que se agregan a `clientes` para guardar lo que llega del formulario.
COLUMNAS_CLIENTE_NUEVAS = [
    "tipo_persona", "dni", "nacionalidad", "domicilio", "localidad", "provincia", "codigo_postal",
    "domicilio_comercial", "estado_civil", "conyuge_nombre", "conyuge_dni", "firmante_nombre", "firmante_cargo",
]


def crear_tablas(cur):
    """Idempotente. Llamar después de cartera.crear_tablas (usa `clientes`)."""
    for col in COLUMNAS_CLIENTE_NUEVAS:
        cur.execute(f"ALTER TABLE clientes ADD COLUMN IF NOT EXISTS {col} TEXT")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS formularios_respuestas (
            id                SERIAL PRIMARY KEY,
            formulario        TEXT NOT NULL,
            tipo_persona      TEXT NOT NULL,
            datos             JSONB NOT NULL DEFAULT '{}'::jsonb,
            cliente_id        INTEGER REFERENCES clientes(id) ON DELETE SET NULL,
            cliente_nuevo     BOOLEAN NOT NULL DEFAULT false,
            estado            TEXT NOT NULL DEFAULT 'nueva',
            recibido_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
            ip                TEXT,
            revisado_por      TEXT,
            revisado_en       TIMESTAMPTZ,
            aviso_enviado_en  TIMESTAMPTZ,
            aviso_error       TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_form_resp_cliente ON formularios_respuestas(cliente_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_form_resp_recibido ON formularios_respuestas(recibido_en DESC)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS formularios_archivos (
            id            SERIAL PRIMARY KEY,
            respuesta_id  INTEGER NOT NULL REFERENCES formularios_respuestas(id) ON DELETE CASCADE,
            campo         TEXT NOT NULL,
            nombre        TEXT NOT NULL,
            mime          TEXT NOT NULL,
            tamano        INTEGER NOT NULL,
            contenido     BYTEA NOT NULL,
            subido_en     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_form_arch_resp ON formularios_archivos(respuesta_id)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS poderes_generados (
            id            SERIAL PRIMARY KEY,
            cliente_id    INTEGER REFERENCES clientes(id) ON DELETE CASCADE,
            datos         JSONB NOT NULL,
            apoderado     TEXT NOT NULL,
            generado_por  TEXT,
            generado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_poderes_cliente ON poderes_generados(cliente_id, generado_en DESC)")


# ── Validación ────────────────────────────────────────────────────────────
class ErrorFormulario(Exception):
    """Error mostrable al cliente (en castellano, sin detalles internos)."""


def solo_digitos(v) -> str:
    return re.sub(r"\D", "", v or "")


def campo_visible(campo: dict, valores: dict) -> bool:
    cond = campo.get("si")
    return not cond or (valores.get(cond["campo"]) or "") == cond["valor"]


def detectar_mime(contenido: bytes, declarado: str) -> str:
    """Tipo real del archivo según sus primeros bytes (no se confía en lo que
    manda el navegador). Devuelve '' si no es ninguno de los permitidos."""
    c = contenido[:16]
    if c.startswith(b"%PDF"):
        return "application/pdf"
    if c.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if c.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if c[:4] == b"RIFF" and contenido[8:12] == b"WEBP":
        return "image/webp"
    if c.startswith(b"GIF87a") or c.startswith(b"GIF89a"):
        return "image/gif"
    if c.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/msword"
    if c.startswith(b"PK\x03\x04") and declarado.endswith("wordprocessingml.document"):
        return declarado
    return ""


def nombre_archivo_seguro(nombre: str, mime: str) -> str:
    base = re.sub(r"[^\w.\- ()áéíóúñÁÉÍÓÚÑ]", "_", (nombre or "archivo").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip() or "archivo"
    base = base[:120]
    ext = EXT_POR_MIME.get(mime, "")
    if ext and not base.lower().endswith(ext) and not (ext == ".jpg" and base.lower().endswith(".jpeg")):
        base = re.sub(r"\.(gif|png|jpe?g|webp|pdf|docx?)$", "", base, flags=re.I) + ext  # p.ej. un GIF se guarda como PNG
    return base


def validar(slug: str, valores: dict, archivos: dict) -> tuple:
    """valores: {id: str}; archivos: {id: (nombre, mime_declarado, bytes)}.
    Devuelve (datos_limpios, archivos_validos). Lanza ErrorFormulario."""
    form = FORMULARIOS[slug]
    datos, validos, faltan = {}, [], []
    for c in form["campos"]:
        if c["tipo"] == "nota" or not campo_visible(c, valores):
            continue
        if c["tipo"] == "archivo":
            a = archivos.get(c["id"])
            if not a or not a[2]:
                if c.get("obligatorio"):
                    faltan.append(c["etiqueta"])
                continue
            nombre, declarado, contenido = a
            if len(contenido) > MAX_ARCHIVO:
                raise ErrorFormulario(f"«{c['etiqueta']}»: el archivo supera los 10 MB.")
            mime = detectar_mime(contenido, declarado or "")
            if mime not in c.get("mimes", MIME_DOCS):
                raise ErrorFormulario(f"«{c['etiqueta']}»: el tipo de archivo no está permitido. {c.get('ayuda', '')}".strip())
            try:  # revisión de seguridad: imágenes regeneradas, PDF/Word sin contenido activo
                contenido, mime = archivos_seguros.revisar(contenido, mime)
            except archivos_seguros.ArchivoRechazado as e:
                raise ErrorFormulario(f"«{c['etiqueta']}»: {e}")
            validos.append({"campo": c["id"], "nombre": nombre_archivo_seguro(nombre, mime), "mime": mime,
                            "contenido": contenido})
            continue
        v = (valores.get(c["id"]) or "").strip()
        if len(v) > (4000 if c["tipo"] == "parrafo" else 300):
            raise ErrorFormulario(f"«{c['etiqueta']}»: la respuesta es demasiado larga.")
        if not v:
            if c.get("obligatorio"):
                faltan.append(c["etiqueta"])
            continue
        if c["tipo"] == "email" and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ErrorFormulario(f"«{c['etiqueta']}»: el correo no parece válido.")
        if c["tipo"] in ("opcion", "lista") and v not in c["opciones"]:
            raise ErrorFormulario(f"«{c['etiqueta']}»: elegí una de las opciones.")
        if c.get("formato") == "cuit":
            v = solo_digitos(v)
            if len(v) != 11:
                raise ErrorFormulario(f"«{c['etiqueta']}»: tiene que tener 11 números (sin guiones también vale).")
        if c.get("formato") == "dni":
            v = solo_digitos(v)
            if not 6 <= len(v) <= 9:
                raise ErrorFormulario(f"«{c['etiqueta']}»: revisá el número.")
        datos[c["id"]] = v
    if faltan:
        raise ErrorFormulario("Faltan completar: " + ", ".join(faltan) + ".")
    return datos, validos


# ── Guardado: respuesta + cliente ─────────────────────────────────────────
def guardar_respuesta(cur, slug: str, datos: dict, archivos: list, ip: str) -> dict:
    """Guarda la respuesta y crea/actualiza el cliente. `cur` tiene que ser
    RealDictCursor. Devuelve {"respuesta_id", "cliente_id", "cliente_nuevo"}."""
    form = FORMULARIOS[slug]
    tipo = form["tipo_persona"]
    campos_cliente = {c["cliente"]: datos[c["id"]] for c in form["campos"]
                      if c.get("cliente") and datos.get(c["id"])}
    campos_cliente["tipo_persona"] = tipo
    if campos_cliente.get("firmante_nombre"):
        campos_cliente.setdefault("contacto", campos_cliente["firmante_nombre"])
    autor = "formulario web"

    cur.execute("SELECT * FROM clientes WHERE cuit = %s AND activo ORDER BY id LIMIT 1", (datos.get("cuit"),))
    existente = cur.fetchone()
    if existente:
        # Ya era cliente: se actualizan sus datos con lo que mandó (es lo más
        # reciente), salvo el nombre, que puede estar cargado distinto a mano.
        cambios = {k: v for k, v in campos_cliente.items() if k != "nombre" or not existente.get("nombre")}
        sets = [f"{k} = %s" for k in cambios] + ["modificado_en = now()", "modificado_por = %s"]
        cur.execute(f"UPDATE clientes SET {', '.join(sets)} WHERE id = %s", list(cambios.values()) + [autor, existente["id"]])
        cliente_id, nuevo = existente["id"], False
    else:
        cols = list(campos_cliente.keys()) + ["origen", "alta_por", "modificado_por"]
        vals = list(campos_cliente.values()) + ["formulario", autor, autor]
        cur.execute(f"INSERT INTO clientes ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id", vals)
        cliente_id, nuevo = cur.fetchone()["id"], True

    cur.execute(
        "INSERT INTO formularios_respuestas (formulario, tipo_persona, datos, cliente_id, cliente_nuevo, ip) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (slug, tipo, json.dumps(datos, ensure_ascii=False), cliente_id, nuevo, ip),
    )
    rid = cur.fetchone()["id"]
    for a in archivos:
        cur.execute(
            "INSERT INTO formularios_archivos (respuesta_id, campo, nombre, mime, tamano, contenido) VALUES (%s, %s, %s, %s, %s, %s)",
            (rid, a["campo"], a["nombre"], a["mime"], len(a["contenido"]), a["contenido"]),
        )
    return {"respuesta_id": rid, "cliente_id": cliente_id, "cliente_nuevo": nuevo}


def respuestas_legibles(slug: str, datos: dict, archivos: list = ()) -> list:
    """[(etiqueta, valor)] en el orden del formulario, para el mail y la ficha."""
    por_campo = {}
    for a in archivos:
        por_campo.setdefault(a["campo"], []).append(a["nombre"])
    filas = []
    for c in FORMULARIOS.get(slug, {}).get("campos", []):
        if c["tipo"] == "nota":
            continue
        if c["tipo"] == "archivo":
            if c["id"] in por_campo:
                filas.append((c["etiqueta"], "📎 " + ", ".join(por_campo[c["id"]])))
        elif datos.get(c["id"]):
            filas.append((c["etiqueta"], datos[c["id"]]))
    return filas


# ── Aviso por mail ────────────────────────────────────────────────────────
DEFAULT_FROM = "Avisos Panel <avisos@quieroregistrarmimarca.com.ar>"
DEFAULT_TO = "marcas@komunikacion.com.ar"
DEFAULT_PANEL = "https://panel.registrodemimarca.com.ar"
MAX_ADJUNTOS_MAIL = 20 * 1024 * 1024  # por encima de esto, los archivos van solo como link al panel


def armar_mail(resp: dict, cliente: dict, archivos: list, panel_url: str) -> tuple:
    """resp: fila de formularios_respuestas; cliente: fila de clientes (o {});
    archivos: filas de formularios_archivos (con contenido)."""
    e = html.escape
    tipo = TIPO_LEGIBLE.get(resp["tipo_persona"], resp["tipo_persona"])
    datos = resp["datos"] if isinstance(resp["datos"], dict) else json.loads(resp["datos"] or "{}")
    nombre = datos.get("nombre") or (cliente or {}).get("nombre") or "(sin nombre)"
    marca = datos.get("marca_nombre")
    asunto = f"📝 Formulario recibido: {nombre}" + (f" — marca «{marca}»" if marca else "") + f" ({tipo})"
    link = f"{panel_url}/clientes?cliente={resp['cliente_id']}" if resp.get("cliente_id") else f"{panel_url}/clientes?vista=formularios"
    estado_cli = ("Se creó como <strong>cliente nuevo</strong>." if resp.get("cliente_nuevo")
                  else "Ya era cliente (mismo CUIT): se <strong>actualizaron sus datos</strong>.")
    filas = "".join(
        f'<tr><td style="padding:6px 10px;color:#6b7280;font-size:13px;vertical-align:top;width:40%">{e(k)}</td>'
        f'<td style="padding:6px 10px;font-size:14px;color:#1f2430">{e(str(v)).replace(chr(10), "<br>")}</td></tr>'
        for k, v in respuestas_legibles(resp["formulario"], datos, archivos)
    )
    total = sum(a["tamano"] for a in archivos)
    nota_adj = ""
    if archivos:
        nota_adj = ("Los archivos van adjuntos a este mail." if total <= MAX_ADJUNTOS_MAIL
                    else "Los archivos son muy pesados para adjuntarlos: descargalos desde la ficha del cliente.")
    cuerpo = f"""<!doctype html><html><body style="margin:0;background:#f5f6f8;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f6f8;padding:20px 0"><tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="background:#fff;border:1px solid #e2e5ea;border-radius:10px;max-width:620px">
  <tr><td style="padding:18px 20px;border-bottom:1px solid #e2e5ea">
    <div style="font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.04em">Formulario {e(tipo)}</div>
    <div style="font-size:20px;font-weight:bold;color:#1f2430;margin-top:4px">{e(nombre)}</div>
    <div style="font-size:14px;color:#374151;margin-top:6px">{estado_cli}</div>
  </td></tr>
  <tr><td style="padding:10px 10px"><table width="100%" cellpadding="0" cellspacing="0">{filas}</table></td></tr>
  <tr><td style="padding:6px 20px 20px">
    <a href="{e(link)}" style="display:inline-block;background:#015197;color:#fff;text-decoration:none;padding:10px 16px;border-radius:8px;font-weight:bold;font-size:14px">Abrir ficha del cliente</a>
    {f'<div style="font-size:12px;color:#6b7280;margin-top:10px">{nota_adj}</div>' if nota_adj else ''}
  </td></tr>
</table></td></tr></table></body></html>"""
    adjuntos = []
    if archivos and total <= MAX_ADJUNTOS_MAIL:
        adjuntos = [{"filename": a["nombre"], "content": base64.b64encode(bytes(a["contenido"])).decode()} for a in archivos]
    return asunto, cuerpo, adjuntos


def enviar_resend(api_key, remitente, destinatarios, asunto, cuerpo, adjuntos=None, responder_a=None):
    payload = {"from": remitente, "to": destinatarios, "subject": asunto, "html": cuerpo}
    if adjuntos:
        payload["attachments"] = adjuntos
    if responder_a:
        payload["reply_to"] = responder_a
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload, timeout=60,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Resend rechazó el envío ({r.status_code}): {r.text[:300]}")
    return r.json().get("id")


def avisar_respuesta(conn, respuesta_id: int, api_key: str, remitente: str, destinatarios: list, panel_url: str) -> bool:
    """Manda el aviso de UNA respuesta y lo marca como enviado. Usa una
    conexión psycopg2 (RealDictCursor). Devuelve True si se envió.
    Si ya estaba avisada (otro proceso se adelantó), no hace nada."""
    import psycopg2.extras  # import local: este módulo se usa también sin psycopg2 en tests
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM formularios_respuestas WHERE id = %s AND aviso_enviado_en IS NULL FOR UPDATE SKIP LOCKED",
                    (respuesta_id,))
        resp = cur.fetchone()
        if not resp:
            conn.rollback()
            return False
        cliente = {}
        if resp["cliente_id"]:
            cur.execute("SELECT * FROM clientes WHERE id = %s", (resp["cliente_id"],))
            cliente = cur.fetchone() or {}
        cur.execute("SELECT campo, nombre, mime, tamano, contenido FROM formularios_archivos WHERE respuesta_id = %s ORDER BY id",
                    (respuesta_id,))
        archivos = cur.fetchall()
        asunto, cuerpo, adjuntos = armar_mail(resp, cliente, archivos, panel_url)
        datos = resp["datos"] if isinstance(resp["datos"], dict) else {}
        try:
            enviar_resend(api_key, remitente, destinatarios, asunto, cuerpo, adjuntos, datos.get("email"))
        except Exception as ex:  # noqa: BLE001 — se guarda el error y se reintenta después
            cur.execute("UPDATE formularios_respuestas SET aviso_error = %s WHERE id = %s", (str(ex)[:500], respuesta_id))
            conn.commit()
            raise
        cur.execute("UPDATE formularios_respuestas SET aviso_enviado_en = now(), aviso_error = NULL WHERE id = %s", (respuesta_id,))
    conn.commit()
    return True
