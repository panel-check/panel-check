"""
Formularios para clientes (Persona física / Persona jurídica).

Reemplazan a los Google Forms: el cliente abre un link público del panel
(/formulario/persona-fisica o /formulario/persona-juridica), lo completa sin
usuario ni clave y, al enviarlo:
  1. se guarda la respuesta completa (y los archivos adjuntos) en la base,
  2. se crea el cliente en la sección Clientes con todos sus datos — o, si ya
     había un cliente activo con ese CUIT, se le actualizan los datos,
  3. se avisa por mail al equipo con las respuestas y los archivos adjuntos.

Corrección: desde el panel se pide un link personal para una respuesta
(/formulario/<tipo>?corregir=<token>). El cliente lo abre con el formulario
ya cargado con lo que mandó, corrige y guarda: se reemplaza la respuesta, se
actualiza el cliente, queda el historial de cambios (formularios_ediciones) y
se vuelve a avisar al equipo. El link sirve una sola vez y vence a los
EDICION_DIAS días.

Este módulo no depende de FastAPI: lo usan tanto el panel (formularios_api.py)
como el script de respaldo del aviso por mail (scripts/notificar_formularios.py).
"""

import base64
import html
import json
import re
import secrets

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
#        obligatorio) cuando el campo x tiene el valor y. También puede ser una
#        lista de condiciones: el campo se muestra solo si se cumplen todas.
# "cliente": a qué columna de la tabla clientes va la respuesta.

MAX_TITULARES = 4
OPCIONES_TITULARES = [str(i) for i in range(1, MAX_TITULARES + 1)]
# Datos que se piden de cada cotitular (titular 2, 3…): los mismos del titular 1 que van al poder y al INPI,
# sin correo, teléfono ni domicilio comercial (el contacto es el titular 1). id del campo: t<N>_<clave>.
CAMPOS_COTITULAR = ("nombre", "sexo", "nacionalidad", "dni", "cuit", "domicilio", "localidad", "provincia",
                    "codigo_postal", "estado_civil", "conyuge_nombre", "conyuge_dni")


def _campos_cotitular(n: int) -> list:
    """Campos del titular n (2..MAX_TITULARES): se muestran si la marca tiene n titulares o más."""
    si = {"campo": "cantidad_titulares", "valores": OPCIONES_TITULARES[n - 1:]}
    casado = [si, {"campo": f"t{n}_estado_civil", "valor": "Casado/a"}]
    t = f"t{n}_"
    return [
        {"id": f"{t}nota", "tipo": "nota", "etiqueta": f"Datos del titular {n}", "si": si,
         "ayuda": f"Completá los datos de la persona número {n} que va a ser titular de la marca. "
                  "Tienen que coincidir con su DNI (y, si la marca ya fue solicitada, con lo ingresado en el INPI)."},
        {"id": f"{t}nombre", "tipo": "texto", "etiqueta": f"Titular {n}: Apellido y Nombre", "obligatorio": True, "si": si},
        {"id": f"{t}sexo", "tipo": "opcion", "etiqueta": f"Titular {n}: Sexo", "obligatorio": True, "si": si,
         "opciones": ["Masculino", "Femenino", "Otro"]},
        {"id": f"{t}nacionalidad", "tipo": "texto", "etiqueta": f"Titular {n}: Nacionalidad", "obligatorio": True, "si": si},
        {"id": f"{t}dni", "tipo": "texto", "etiqueta": f"Titular {n}: DNI", "obligatorio": True, "formato": "dni", "si": si},
        {"id": f"{t}cuit", "tipo": "texto", "etiqueta": f"Titular {n}: CUIL/CUIT", "obligatorio": True, "formato": "cuit", "si": si},
        {"id": f"{t}domicilio", "tipo": "texto", "etiqueta": f"Titular {n}: Domicilio particular", "obligatorio": True, "si": si},
        {"id": f"{t}localidad", "tipo": "texto", "etiqueta": f"Titular {n}: Localidad", "obligatorio": True, "si": si},
        {"id": f"{t}provincia", "tipo": "lista", "etiqueta": f"Titular {n}: Provincia", "obligatorio": True, "si": si,
         "opciones": PROVINCIAS},
        {"id": f"{t}codigo_postal", "tipo": "texto", "etiqueta": f"Titular {n}: Código Postal", "obligatorio": True, "si": si},
        {"id": f"{t}estado_civil", "tipo": "opcion", "etiqueta": f"Titular {n}: Estado Civil", "obligatorio": True, "si": si,
         "opciones": ["Casado/a", "Soltero/a", "Viudo/a", "Divorciado/a"]},
        {"id": f"{t}conyuge_nombre", "tipo": "texto", "etiqueta": f"Titular {n}: Nombre y apellido del cónyuge",
         "obligatorio": False, "si": casado},
        {"id": f"{t}conyuge_dni", "tipo": "texto", "etiqueta": f"Titular {n}: DNI del cónyuge", "obligatorio": False,
         "formato": "dni", "si": casado},
    ]


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

# Persona física: qué servicio pide (decide qué preguntas de la marca se muestran).
SERVICIO_NUEVA = "Registro de una nueva marca"
SERVICIO_OPOSICION = "Responder a las oposiciones recibidas"
SERVICIO_VISTAS = "Respuesta de Vistas"
SI_NUEVA = {"campo": "servicio", "valor": SERVICIO_NUEVA}
SI_OPOSICION = {"campo": "servicio", "valor": SERVICIO_OPOSICION}
# Oposiciones y vistas son sobre una marca ya presentada: comparten nombre, descripción y web/redes.
SI_OPOSICION_O_VISTAS = {"campo": "servicio", "valores": [SERVICIO_OPOSICION, SERVICIO_VISTAS]}

FORMULARIOS = {
    "persona-fisica": {
        "tipo_persona": "fisica",
        "titulo": "Formulario de Marcas – Persona Física",
        "descripcion": "Completá este formulario (como persona física). "
                       "Algunos de los datos solicitados al completar el formulario generarán un PDF automático que se te enviará por email.",
        "otro": {"slug": "persona-juridica", "texto": "¿La marca va a nombre de una empresa? Completá el formulario de Persona Jurídica"},
        "campos": [
            {"id": "nota_datos_inpi", "tipo": "nota", "etiqueta": "Datos personales",
             "ayuda": "Si tu marca ya fue solicitada en el INPI (por ejemplo, para responder una oposición o una vista), "
                      "todos los datos personales tienen que coincidir con los ingresados a la hora de hacer la solicitud "
                      "de la marca en el INPI."},
            {"id": "email", "tipo": "email", "etiqueta": "Correo electrónico", "obligatorio": True, "cliente": "email"},
            # Si la marca tiene más de un titular, se piden los datos de cada uno (t2_…, t4_…) y el poder los
            # incluye a todos, con un lugar de firma para cada uno (ver poderes.propuesta).
            {"id": "cantidad_titulares", "tipo": "opcion", "etiqueta": "¿Cuántos titulares tiene la marca?", "obligatorio": True,
             "opciones": OPCIONES_TITULARES,
             "ayuda": "Si la marca es (o va a ser) de más de una persona, más abajo te pedimos los datos de cada una. "
                      "El poder lo tienen que firmar todos los titulares. Primero completá los datos del titular 1."},
            {"id": "nombre", "tipo": "texto", "etiqueta": "Apellido y Nombre del titular", "obligatorio": True, "cliente": "nombre"},
            # Para redactar el poder: Masculino → «El», Femenino → «La», Otro → «El/La» (ver poderes.propuesta_simple)
            {"id": "sexo", "tipo": "opcion", "etiqueta": "Sexo", "obligatorio": True, "cliente": "sexo",
             "opciones": ["Masculino", "Femenino", "Otro"]},
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
            *[c for n in range(2, MAX_TITULARES + 1) for c in _campos_cotitular(n)],
            {"id": "servicio", "tipo": "opcion", "etiqueta": "¿Qué servicio solicitás?", "obligatorio": True,
             "opciones": [SERVICIO_NUEVA, SERVICIO_OPOSICION, SERVICIO_VISTAS]},
            # ── Opción 1: registro de una nueva marca ──
            {"id": "empresa_nombre", "tipo": "texto", "etiqueta": "Nombre de la empresa", "obligatorio": True, "si": SI_NUEVA},
            {"id": "rubro", "tipo": "texto", "etiqueta": "Rubro", "obligatorio": True, "si": SI_NUEVA},
            {"id": "actividad_descripcion", "tipo": "parrafo", "etiqueta": "Descripción de la actividad", "obligatorio": True,
             "ayuda": "Es importante poder dar una descripción lo más cercana a la realidad para poder definir en qué clase debería ser registrada la marca.",
             "si": SI_NUEVA},
            {"id": "web_redes", "tipo": "texto", "etiqueta": "Web o Redes", "obligatorio": True, "si": SI_NUEVA},
            {"id": "nueva_tiene_logo", "tipo": "opcion", "etiqueta": "¿Tiene logo?", "obligatorio": True, "opciones": ["SI", "NO"],
             "si": SI_NUEVA},
            {"id": "nueva_logo", "tipo": "archivo", "etiqueta": "Adjunte su logo", "obligatorio": True,
             "ayuda": "Imagen (JPG, PNG, WEBP, GIF) o PDF. Máximo 10 MB.", "mimes": MIME_LOGO,
             "si": [SI_NUEVA, {"campo": "nueva_tiene_logo", "valor": "SI"}]},
            # ── Opción 2: responder a las oposiciones recibidas y Opción 3: respuesta de vistas ──
            {"id": "nota_coinciden_inpi", "tipo": "nota", "etiqueta": "Importante",
             "ayuda": "Revisá que todos los datos personales que completaste arriba (nombre, DNI, CUIL/CUIT y domicilio) "
                      "coincidan con los ingresados a la hora de hacer la solicitud de la marca en el INPI.",
             "si": SI_OPOSICION_O_VISTAS},
            {"id": "marca_nombre", "tipo": "texto", "etiqueta": "Nombre de la marca", "obligatorio": True, "si": SI_OPOSICION_O_VISTAS},
            {"id": "marca_descripcion", "tipo": "parrafo",
             "etiqueta": "Describa de manera sencilla pero a la vez completa los servicios y/o productos que brinda su marca",
             "obligatorio": False, "si": SI_OPOSICION_O_VISTAS},
            {"id": "marca_web_redes", "tipo": "texto", "etiqueta": "Web o Redes", "obligatorio": False,
             "ayuda": "La página web o las redes de la marca (por ejemplo, @tumarca en Instagram). Si no tiene, dejalo vacío.",
             "si": SI_OPOSICION_O_VISTAS},
            # el logo se pide solo para responder oposiciones
            {"id": "tiene_logo", "tipo": "opcion", "etiqueta": "¿Tiene logo?", "obligatorio": True, "opciones": ["SI", "NO"],
             "si": SI_OPOSICION},
            {"id": "logo", "tipo": "archivo", "etiqueta": "Adjunte su logo en el tamaño 4x4", "obligatorio": True,
             "ayuda": "Imagen (JPG, PNG, WEBP, GIF) o PDF. Máximo 10 MB.", "mimes": MIME_LOGO,
             "si": [SI_OPOSICION, {"campo": "tiene_logo", "valor": "SI"}]},
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
    "tipo_persona", "dni", "sexo", "nacionalidad", "domicilio", "localidad", "provincia", "codigo_postal",
    "domicilio_comercial", "estado_civil", "conyuge_nombre", "conyuge_dni", "firmante_nombre", "firmante_cargo",
    "cotitulares",  # JSON con los demás titulares (titular 2, 3…) del formulario de persona física
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
    cur.execute("ALTER TABLE formularios_respuestas ADD COLUMN IF NOT EXISTS poder_enviado_en TIMESTAMPTZ")
    cur.execute("ALTER TABLE formularios_respuestas ADD COLUMN IF NOT EXISTS poder_error TEXT")
    # Link para corregir (ver aplicar_edicion)
    for col, tipo_col in [("edicion_token", "TEXT"), ("edicion_creado_en", "TIMESTAMPTZ"), ("edicion_vence_en", "TIMESTAMPTZ"),
                          ("edicion_creado_por", "TEXT"), ("editado_en", "TIMESTAMPTZ"),
                          ("ediciones", "INTEGER NOT NULL DEFAULT 0")]:
        cur.execute(f"ALTER TABLE formularios_respuestas ADD COLUMN IF NOT EXISTS {col} {tipo_col}")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_form_resp_token ON formularios_respuestas(edicion_token) "
                "WHERE edicion_token IS NOT NULL")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS formularios_ediciones (
            id            SERIAL PRIMARY KEY,
            respuesta_id  INTEGER NOT NULL REFERENCES formularios_respuestas(id) ON DELETE CASCADE,
            datos_antes   JSONB NOT NULL,
            datos_despues JSONB NOT NULL,
            cambios       JSONB NOT NULL DEFAULT '[]'::jsonb,
            editado_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
            ip            TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_form_ed_resp ON formularios_ediciones(respuesta_id, editado_en DESC)")
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
    if not cond:
        return True
    conds = cond if isinstance(cond, list) else [cond]
    # cada condición pide un valor («valor») o uno entre varios («valores»)
    return all((valores.get(c["campo"]) or "") in (c["valores"] if "valores" in c else [c["valor"]]) for c in conds)


def nombre_marca(datos: dict) -> str:
    """Nombre de la marca de una respuesta: según el servicio es «marca_nombre»
    (responder oposiciones o vistas) o «empresa_nombre» (registro de una nueva marca)."""
    return datos.get("marca_nombre") or datos.get("empresa_nombre") or ""


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


def validar(slug: str, valores: dict, archivos: dict, existentes=frozenset()) -> tuple:
    """valores: {id: str}; archivos: {id: (nombre, mime_declarado, bytes)}.
    existentes: campos de archivo que ya tienen un archivo guardado (al corregir:
    si no suben uno nuevo, queda el anterior y el campo no falta).
    Devuelve (datos_limpios, archivos_validos). Lanza ErrorFormulario."""
    form = FORMULARIOS[slug]
    datos, validos, faltan = {}, [], []
    for c in form["campos"]:
        if c["tipo"] == "nota" or not campo_visible(c, valores):
            continue
        if c["tipo"] == "archivo":
            a = archivos.get(c["id"])
            if not a or not a[2]:
                if c.get("obligatorio") and c["id"] not in existentes:
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
    campos_cliente = datos_cliente(slug, datos)
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


def datos_cliente(slug: str, datos: dict) -> dict:
    """Columnas de `clientes` que salen de una respuesta."""
    form = FORMULARIOS[slug]
    campos = {c["cliente"]: datos[c["id"]] for c in form["campos"] if c.get("cliente") and datos.get(c["id"])}
    campos["tipo_persona"] = form["tipo_persona"]
    if form["tipo_persona"] == "fisica":
        cot = cotitulares(datos)  # lo último que mandó manda: con un solo titular, se borran los anteriores
        campos["cotitulares"] = json.dumps(cot, ensure_ascii=False) if cot else None
    if campos.get("firmante_nombre"):
        campos.setdefault("contacto", campos["firmante_nombre"])
    return campos


# ── Corrección de una respuesta (link personal) ───────────────────────────
EDICION_DIAS = 30
# Si cambia alguno de estos, el poder que ya se mandó quedó mal: se vuelve a mandar.
CAMPOS_PODER = ("nombre", "sexo", "dni", "cuit", "domicilio", "localidad", "provincia", "email")


def nuevo_token() -> str:
    return secrets.token_urlsafe(24)


def diferencias(slug: str, antes: dict, despues: dict, archivos_antes: dict = None, archivos_despues: dict = None) -> list:
    """[{etiqueta, antes, despues}] en el orden del formulario. archivos_*: {campo: nombre}."""
    archivos_antes, archivos_despues = archivos_antes or {}, archivos_despues or {}
    cambios = []
    for c in FORMULARIOS.get(slug, {}).get("campos", []):
        if c["tipo"] == "nota":
            continue
        if c["tipo"] == "archivo":
            a, d = archivos_antes.get(c["id"], ""), archivos_despues.get(c["id"], "")
        else:
            a, d = (antes or {}).get(c["id"], ""), (despues or {}).get(c["id"], "")
        if a != d:
            cambios.append({"etiqueta": c["etiqueta"], "antes": a or "—", "despues": d or "—"})
    return cambios


def cambia_poder(antes: dict, despues: dict) -> bool:
    claves = set(CAMPOS_PODER) | {"cantidad_titulares"} | {f"t{n}_{k}" for n in range(2, MAX_TITULARES + 1)
                                                          for k in CAMPOS_COTITULAR}
    return any((antes or {}).get(k, "") != (despues or {}).get(k, "") for k in claves)


def cantidad_titulares(datos: dict) -> int:
    try:
        return max(1, min(MAX_TITULARES, int((datos or {}).get("cantidad_titulares") or 1)))
    except ValueError:
        return 1


def cotitulares(datos: dict) -> list:
    """Titulares 2, 3… de una respuesta, como dicts con las mismas claves que la ficha del cliente."""
    out = []
    for n in range(2, cantidad_titulares(datos) + 1):
        t = {k: (datos.get(f"t{n}_{k}") or "") for k in CAMPOS_COTITULAR}
        if t["nombre"]:
            out.append(t)
    return out


def agregar_aviso_titulares(cuerpo: str, n: int) -> str:
    """Con más de un titular, el mail del poder avisa que lo tienen que firmar todos (antes del saludo)."""
    if n <= 1:
        return cuerpo
    aviso = (f"Importante: como la marca tiene {n} titulares, el poder lo tienen que firmar todos, cada uno en su lugar "
             "(firma, aclaración y DNI). Pueden firmar la misma hoja o cada uno una copia impresa; en ese caso, mandanos todas.")
    m = re.search(r"\n\s*\n(?=Saludos)", cuerpo)
    return cuerpo[:m.start()] + "\n\n" + aviso + cuerpo[m.start():] if m else cuerpo.rstrip() + "\n\n" + aviso


def respuesta_por_token(cur, token: str, bloquear: bool = False):
    """La respuesta con ese link para corregir, si sigue vigente (o None). `cur`: RealDictCursor."""
    if not token or len(token) > 100:
        return None
    cur.execute("SELECT * FROM formularios_respuestas WHERE edicion_token = %s AND edicion_vence_en > now()"
                + (" FOR UPDATE" if bloquear else ""), (token,))
    return cur.fetchone()


def archivos_por_campo(cur, respuesta_id: int) -> dict:
    """{campo: nombre} de los archivos guardados de una respuesta (sin el contenido)."""
    cur.execute("SELECT campo, nombre FROM formularios_archivos WHERE respuesta_id = %s ORDER BY id", (respuesta_id,))
    return {f["campo"]: f["nombre"] for f in cur.fetchall()}


def aplicar_edicion(cur, resp: dict, datos: dict, archivos: list, ip: str) -> dict:
    """Guarda la corrección de una respuesta (resp: fila bloqueada con respuesta_por_token).
    Reemplaza las respuestas, los archivos que subieron de nuevo (los demás quedan; los de una opción que
    ya no corresponde se borran), actualiza el cliente, deja el historial y anula el link (sirve una vez).
    La respuesta vuelve a «nueva» y queda pendiente de aviso por mail. Devuelve {"cambios", "reenviar_poder"}."""
    slug, rid = resp["formulario"], resp["id"]
    antes = resp["datos"] if isinstance(resp["datos"], dict) else json.loads(resp["datos"] or "{}")
    arch_antes = archivos_por_campo(cur, rid)
    visibles = {c["id"] for c in FORMULARIOS[slug]["campos"] if c["tipo"] == "archivo" and campo_visible(c, datos)}
    reemplazados = {a["campo"] for a in archivos}
    sacar = [c for c in arch_antes if c in reemplazados or c not in visibles]
    if sacar:
        cur.execute("DELETE FROM formularios_archivos WHERE respuesta_id = %s AND campo = ANY(%s)", (rid, sacar))
    for a in archivos:
        cur.execute(
            "INSERT INTO formularios_archivos (respuesta_id, campo, nombre, mime, tamano, contenido) VALUES (%s, %s, %s, %s, %s, %s)",
            (rid, a["campo"], a["nombre"], a["mime"], len(a["contenido"]), a["contenido"]),
        )
    arch_despues = {c: n for c, n in arch_antes.items() if c not in sacar}
    arch_despues.update({a["campo"]: a["nombre"] for a in archivos})
    cambios = diferencias(slug, antes, datos, arch_antes, arch_despues)
    cur.execute("INSERT INTO formularios_ediciones (respuesta_id, datos_antes, datos_despues, cambios, ip) VALUES (%s, %s, %s, %s, %s)",
                (rid, json.dumps(antes, ensure_ascii=False), json.dumps(datos, ensure_ascii=False),
                 json.dumps(cambios, ensure_ascii=False), ip))
    cur.execute(
        "UPDATE formularios_respuestas SET datos = %s, estado = 'nueva', revisado_por = NULL, revisado_en = NULL, "
        "editado_en = now(), ediciones = ediciones + 1, edicion_token = NULL, aviso_enviado_en = NULL, aviso_error = NULL "
        "WHERE id = %s", (json.dumps(datos, ensure_ascii=False), rid))
    if resp.get("cliente_id"):
        # Es una corrección pedida por nosotros: se pisan todos los datos del cliente, también el nombre.
        campos = datos_cliente(slug, datos)
        sets = [f"{k} = %s" for k in campos] + ["modificado_en = now()", "modificado_por = %s"]
        cur.execute(f"UPDATE clientes SET {', '.join(sets)} WHERE id = %s",
                    list(campos.values()) + ["formulario web (corrección)", resp["cliente_id"]])
    return {"cambios": cambios,
            "reenviar_poder": FORMULARIOS[slug]["tipo_persona"] == "fisica" and cambia_poder(antes, datos)}


CLAVE_MAIL_PODER = "poder_persona_fisica"
PODER_MAX_24H = 3  # poderes por mail en 24 hs: evita que usen el formulario para spamear a un tercero


def datos_mail_poder(datos: dict) -> dict:
    """Variables de la plantilla del mail del poder, a partir de lo que completó la persona."""
    return {"titular": datos.get("nombre") or ""}


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


def armar_mail(resp: dict, cliente: dict, archivos: list, panel_url: str, cambios=None) -> tuple:
    """resp: fila de formularios_respuestas; cliente: fila de clientes (o {});
    archivos: filas de formularios_archivos (con contenido).
    cambios: si la respuesta se corrigió con el link, los cambios de la última corrección."""
    e = html.escape
    tipo = TIPO_LEGIBLE.get(resp["tipo_persona"], resp["tipo_persona"])
    datos = resp["datos"] if isinstance(resp["datos"], dict) else json.loads(resp["datos"] or "{}")
    nombre = datos.get("nombre") or (cliente or {}).get("nombre") or "(sin nombre)"
    marca = nombre_marca(datos)
    corregido = cambios is not None
    asunto = (f"✏️ Formulario corregido: {nombre}" if corregido else f"📝 Formulario recibido: {nombre}") \
        + (f" — marca «{marca}»" if marca else "") + f" ({tipo})"
    link = f"{panel_url}/clientes?cliente={resp['cliente_id']}" if resp.get("cliente_id") else f"{panel_url}/clientes?vista=formularios"
    if corregido:
        estado_cli = ("Corrigió los datos con el link que le mandamos" + (" (se actualizó el cliente)." if resp.get("cliente_id") else ".")
                      + (" <strong>No cambió nada.</strong>" if not cambios else ""))
    else:
        estado_cli = ("Se creó como <strong>cliente nuevo</strong>." if resp.get("cliente_nuevo")
                      else "Ya era cliente (mismo CUIT): se <strong>actualizaron sus datos</strong>.")
    bloque_cambios = ""
    if cambios:
        filas_c = "".join(
            f'<tr><td style="padding:6px 10px;color:#6b7280;font-size:13px;vertical-align:top;width:34%">{e(c["etiqueta"])}</td>'
            f'<td style="padding:6px 10px;font-size:13px;color:#9b1c1c;text-decoration:line-through;vertical-align:top">{e(str(c["antes"]))}</td>'
            f'<td style="padding:6px 10px;font-size:14px;color:#14532d;font-weight:bold;vertical-align:top">{e(str(c["despues"]))}</td></tr>'
            for c in cambios)
        bloque_cambios = (f'<tr><td style="padding:12px 20px 0"><div style="font-size:13px;font-weight:bold;color:#1f2430">Qué cambió</div>'
                          f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#fffbeb;border:1px solid #fde68a;'
                          f'border-radius:8px;margin-top:6px">{filas_c}</table>'
                          f'<div style="font-size:13px;font-weight:bold;color:#1f2430;margin-top:14px">Todas las respuestas, como quedaron</div></td></tr>')
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
  {bloque_cambios}
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
        cambios = None
        if resp.get("editado_en"):
            cur.execute("SELECT cambios FROM formularios_ediciones WHERE respuesta_id = %s ORDER BY editado_en DESC, id DESC LIMIT 1",
                        (respuesta_id,))
            ult = cur.fetchone()
            cambios = (ult["cambios"] if ult else None) or []
        asunto, cuerpo, adjuntos = armar_mail(resp, cliente, archivos, panel_url, cambios)
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
