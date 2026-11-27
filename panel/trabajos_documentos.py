"""Documentos firmados de un trabajo de Seguimientos (27/11/2026).

El poder, la toma de intervención o el desiste se generan desde la ficha del trabajo, se imprimen, se firman y se
les saca una foto. Acá se sube esa foto (JPG, PNG, WEBP…) o un PDF, en una o varias páginas, y el panel arma un
solo PDF liviano, en el orden elegido, listo para subir al Portal de Trámites del INPI.

Las fotos se enderezan según el celular (EXIF), se achican (lado mayor ≤ 2000 px, que alcanza para leer y
pesa poco) y, si se pide «como escaneo», se pasan a blanco y negro con más contraste. Cada página queda del
ancho de una hoja A4. Los PDF que se suben se suman tal cual, con todas sus páginas.
"""

import io
import re

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter

TIPOS = {
    "poder": "Poder firmado",
    "toma": "Toma de intervención firmada",
    "desiste": "Desiste firmado",
    "otro": "Documento firmado",
}
MAX_ARCHIVOS = 20
MAX_BYTES = 40 * 1024 * 1024        # tope del pedido entero
LADO_MAX = 2000                      # px del lado mayor de cada foto
A4_ANCHO_PT = 595.0                  # ancho de una hoja A4 en puntos

ESQUEMA = [
    """CREATE TABLE IF NOT EXISTS trabajos_documentos (
        id          BIGSERIAL PRIMARY KEY,
        trabajo_id  INTEGER NOT NULL REFERENCES trabajos(id) ON DELETE CASCADE,
        tipo        TEXT NOT NULL,
        nombre      TEXT NOT NULL,
        pdf         BYTEA NOT NULL,
        paginas     INTEGER NOT NULL,
        bytes       INTEGER NOT NULL,
        subido_por  TEXT,
        subido_en   TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_trabajos_documentos ON trabajos_documentos(trabajo_id, subido_en DESC)",
    # 27/11/2026: subir un firmado ya no deja nota en la bitácora; se borran las que quedaron de las primeras subidas.
    "DELETE FROM trabajos_bitacora WHERE texto LIKE 'Se subió «%»%: listo en PDF para el INPI.'",
]


class ArchivoInvalido(ValueError):
    """Un archivo que no se pudo leer: el mensaje se muestra tal cual en la ficha."""


def _es_heic(datos: bytes) -> bool:
    return datos[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc", b"ftypheim", b"ftypheis")


def _pagina_de_foto(datos: bytes, nombre: str, escaneo: bool) -> bytes:
    """Una foto → un PDF de una página, del ancho de una A4."""
    if _es_heic(datos):
        raise ArchivoInvalido(f"«{nombre}» es HEIC (foto de iPhone). Elegila desde la galería del navegador "
                              "(se convierte sola a JPG) o mandátela por WhatsApp y subí la que te llega.")
    try:
        im = Image.open(io.BytesIO(datos))
        im.load()
    except (UnidentifiedImageError, OSError):
        raise ArchivoInvalido(f"«{nombre}» no es una imagen ni un PDF que se pueda leer.")
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        fondo = Image.new("RGB", im.size, "white")
        im = im.convert("RGBA")
        fondo.paste(im, mask=im.split()[-1])
        im = fondo
    im.thumbnail((LADO_MAX, LADO_MAX))
    if escaneo:
        im = ImageOps.autocontrast(im.convert("L"), cutoff=2)
    else:
        im = im.convert("RGB")
    salida = io.BytesIO()
    # resolution = px por pulgada: con esto la página queda del ancho de una A4 (595 pt) y el alto proporcional.
    im.save(salida, "PDF", resolution=im.width * 72.0 / A4_ANCHO_PT, quality=78)
    return salida.getvalue()


def a_pdf(archivos: list, escaneo: bool = True) -> tuple:
    """archivos: [(nombre, bytes)] en el orden de las páginas → (pdf, cantidad de páginas)."""
    if not archivos:
        raise ArchivoInvalido("Elegí al menos una foto o un PDF.")
    if len(archivos) > MAX_ARCHIVOS:
        raise ArchivoInvalido(f"Son muchos archivos: el máximo es {MAX_ARCHIVOS} por vez.")
    w = PdfWriter()
    for nombre, datos in archivos:
        if not datos:
            raise ArchivoInvalido(f"«{nombre}» está vacío.")
        if datos[:5] == b"%PDF-":
            try:
                lector = PdfReader(io.BytesIO(datos))
                for p in lector.pages:
                    w.add_page(p)
            except Exception:
                raise ArchivoInvalido(f"«{nombre}» es un PDF dañado o con clave.")
        else:
            for p in PdfReader(io.BytesIO(_pagina_de_foto(datos, nombre, escaneo))).pages:
                w.add_page(p)
    w.compress_identical_objects()
    salida = io.BytesIO()
    w.write(salida)
    return salida.getvalue(), len(w.pages)


def nombre_archivo(acta, marca, tipo) -> str:
    """«4790681-MAS - Poder firmado.pdf» (acta y marca, como los PDF del INPI)."""
    base = "-".join(x for x in [str(acta or "").strip(), (marca or "").strip().upper()] if x) or "Trabajo"
    base = re.sub(r'[\\/:*?"<>|]+', " ", base).strip()
    return f"{base} - {TIPOS.get(tipo, TIPOS['otro'])}.pdf"
