"""
Revisión de seguridad de los archivos que suben los clientes en los
formularios públicos (logo, constancia de CUIT, estatuto).

Nunca se confía en el nombre ni en el tipo que manda el navegador. Cada
archivo pasa por acá antes de guardarse y, si algo no cierra, se rechaza con
un mensaje claro para el cliente:

  • Imágenes (JPG, PNG, WEBP, GIF): se abren y se VUELVEN A GENERAR desde
    cero con Pillow. Lo que se guarda es una imagen nueva hecha solo con los
    píxeles: cualquier cosa escondida dentro del archivo original (código,
    otro archivo pegado al final, metadatos) se pierde. Si no se puede abrir
    como imagen de verdad, se rechaza. GIF se convierte a PNG.
  • PDF: se abre con pypdf y se rechaza si trae contenido activo — JavaScript,
    acciones automáticas al abrir, comandos para ejecutar programas
    (/Launch), archivos incrustados, formularios XFA o contenido multimedia.
    Un PDF común (constancia de AFIP, un estatuto escaneado) no tiene nada de
    eso. Si no se puede leer o está protegido con contraseña, se rechaza.
  • Word: solo .docx. Se rechaza si trae macros (vbaProject), objetos
    incrustados, o vínculos a plantillas/recursos externos (una técnica
    conocida para bajar código al abrirlo). El .doc viejo no se acepta
    porque puede traer macros escondidas.

Los archivos nunca se ejecutan en el servidor: se guardan en la base y el
panel los entrega como descarga (o los muestra en el navegador solo si son
PDF o imagen), con cabeceras que impiden que el navegador los interprete
como otra cosa.
"""

import io
import re
import zipfile

MAX_PIXELES = 40_000_000          # ~ 6300 x 6300: más que suficiente para un logo o un escaneo
MAX_DESCOMPRIMIDO_DOCX = 60 * 1024 * 1024
MAX_ENTRADAS_DOCX = 2000

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ArchivoRechazado(Exception):
    """Mensaje mostrable al cliente."""


# ── Imágenes ──────────────────────────────────────────────────────────────
def limpiar_imagen(contenido: bytes, mime: str) -> tuple:
    import warnings

    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELES
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(contenido)) as im:
                im.verify()  # chequeo de estructura
            with Image.open(io.BytesIO(contenido)) as im:
                if im.width * im.height > MAX_PIXELES:
                    raise ArchivoRechazado("la imagen es demasiado grande (en píxeles).")
                im.load()  # decodifica la imagen completa: si está rota o trucada, falla acá
                formato = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}.get(mime, "PNG")
                if formato == "JPEG":
                    limpia = im.convert("RGB") if im.mode not in ("RGB", "L") else im.copy()
                elif im.mode in ("RGBA", "LA", "RGB", "L"):
                    limpia = im.copy()
                else:  # paletas, CMYK, etc.: a RGBA (respeta transparencia)
                    limpia = im.convert("RGBA")
    except ArchivoRechazado:
        raise
    except Exception:  # noqa: BLE001 — cualquier cosa que no sea una imagen sana
        raise ArchivoRechazado("el archivo no es una imagen válida o está dañado.")

    salida = io.BytesIO()
    # Imagen nueva: solo píxeles, sin metadatos (EXIF, comentarios, perfiles, datos extra).
    if formato == "JPEG":
        limpia.save(salida, "JPEG", quality=95, subsampling=0, optimize=True)
        return salida.getvalue(), "image/jpeg"
    if formato == "WEBP":
        limpia.save(salida, "WEBP", lossless=True)
        return salida.getvalue(), "image/webp"
    limpia.save(salida, "PNG", optimize=True)
    return salida.getvalue(), "image/png"


# ── PDF ───────────────────────────────────────────────────────────────────
CLAVES_PELIGROSAS_PDF = {"/JavaScript", "/JS", "/Launch", "/EmbeddedFile", "/EmbeddedFiles",
                         "/RichMedia", "/XFA", "/SubmitForm", "/ImportData", "/GoToE", "/Sound", "/Movie"}
ACCIONES_PELIGROSAS_PDF = {"/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToE", "/GoToR",
                           "/RichMediaExecute", "/Rendition", "/Sound", "/Movie"}


def _recorrer(obj, vistos, hallazgos, profundidad=0):
    """Recorre todos los objetos alcanzables del PDF (ya descomprimidos por
    pypdf, así que no se escapa nada dentro de streams de objetos)."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

    if profundidad > 200 or len(hallazgos) > 0:
        return
    if isinstance(obj, IndirectObject):
        clave = (obj.idnum, obj.generation)
        if clave in vistos:
            return
        vistos.add(clave)
        if len(vistos) > 200_000:
            hallazgos.append("demasiados objetos")
            return
        try:
            obj = obj.get_object()
        except Exception:  # noqa: BLE001
            return
    if isinstance(obj, DictionaryObject):
        for k, v in obj.items():
            if k in CLAVES_PELIGROSAS_PDF:
                hallazgos.append(k)
                return
            if k == "/S" and isinstance(v, NameObject) and v in ACCIONES_PELIGROSAS_PDF:
                hallazgos.append(str(v))
                return
            if k in ("/OpenAction", "/AA"):
                # Una acción automática que solo "va a la página 1" es normal;
                # cualquier otra cosa se revisa con el resto de las reglas.
                pass
            _recorrer(v, vistos, hallazgos, profundidad + 1)
    elif isinstance(obj, ArrayObject):
        for v in obj:
            _recorrer(v, vistos, hallazgos, profundidad + 1)


def revisar_pdf(contenido: bytes):
    from pypdf import PdfReader

    try:
        lector = PdfReader(io.BytesIO(contenido), strict=False)
        if lector.is_encrypted:
            if not lector.decrypt(""):
                raise ArchivoRechazado("el PDF está protegido con contraseña. Mandalo sin contraseña.")
        _ = len(lector.pages)
        hallazgos, vistos = [], set()
        _recorrer(lector.trailer.get("/Root"), vistos, hallazgos)
    except ArchivoRechazado:
        raise
    except Exception:  # noqa: BLE001
        raise ArchivoRechazado("no se pudo leer el PDF (está dañado o no es un PDF real).")
    if hallazgos:
        raise ArchivoRechazado("el PDF trae contenido activo (scripts o acciones automáticas) y por seguridad no lo "
                               "aceptamos. Probá guardarlo de nuevo como PDF («Imprimir → Guardar como PDF») o mandá una foto/escaneo.")
    # Por las dudas, también se mira el archivo crudo (por si pypdf no llegó a algún objeto suelto).
    crudo = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), contenido)
    # (no se busca "/JS" suelto: 3 letras aparecen por azar dentro de imágenes comprimidas; ni
    # "/EmbeddedFile" suelto: Adobe Scan guarda ahí metadatos propios que el documento no usa.
    # Un adjunto real, enganchado al documento, ya lo detecta el recorrido de arriba.)
    if re.search(rb"/(JavaScript|Launch|RichMedia|XFA)\b", crudo):
        raise ArchivoRechazado("el PDF trae contenido activo (scripts o acciones automáticas) y por seguridad no lo "
                               "aceptamos. Probá guardarlo de nuevo como PDF («Imprimir → Guardar como PDF») o mandá una foto/escaneo.")


# ── Word (.docx) ──────────────────────────────────────────────────────────
def revisar_docx(contenido: bytes):
    try:
        z = zipfile.ZipFile(io.BytesIO(contenido))
        entradas = z.infolist()
    except Exception:  # noqa: BLE001
        raise ArchivoRechazado("el archivo de Word está dañado.")
    if len(entradas) > MAX_ENTRADAS_DOCX or sum(e.file_size for e in entradas) > MAX_DESCOMPRIMIDO_DOCX:
        raise ArchivoRechazado("el archivo de Word es demasiado grande.")
    nombres = [e.filename for e in entradas]
    if "word/document.xml" not in nombres or "[Content_Types].xml" not in nombres:
        raise ArchivoRechazado("el archivo no es un documento de Word válido.")
    for n in nombres:
        nl = n.lower()
        if "vbaproject" in nl or nl.endswith(".bin") or "/embeddings/" in nl or "activex" in nl:
            raise ArchivoRechazado("el documento de Word trae macros u objetos incrustados y por seguridad no lo aceptamos. "
                                   "Mandalo como PDF.")
    tipos = z.read("[Content_Types].xml").decode("utf-8", "ignore").lower()
    if "macroenabled" in tipos or "vbaproject" in tipos:
        raise ArchivoRechazado("el documento de Word trae macros y por seguridad no lo aceptamos. Mandalo como PDF.")
    for n in nombres:
        if n.endswith(".rels"):
            rels = z.read(n).decode("utf-8", "ignore")
            for m in re.finditer(r"<Relationship\b[^>]*>", rels):
                rel = m.group(0)
                externo = re.search(r'TargetMode\s*=\s*"External"', rel, re.I)
                es_link = re.search(r'/hyperlink"', rel, re.I)
                if externo and not es_link:  # un hipervínculo común está bien; una plantilla/objeto remoto, no
                    raise ArchivoRechazado("el documento de Word carga contenido de internet al abrirse y por seguridad "
                                           "no lo aceptamos. Mandalo como PDF.")


def revisar(contenido: bytes, mime: str) -> tuple:
    """Devuelve (contenido_a_guardar, mime_final). Lanza ArchivoRechazado."""
    if mime.startswith("image/"):
        return limpiar_imagen(contenido, mime)
    if mime == "application/pdf":
        revisar_pdf(contenido)
        return contenido, mime
    if mime == DOCX:
        revisar_docx(contenido)
        return contenido, mime
    raise ArchivoRechazado("el tipo de archivo no está permitido.")
