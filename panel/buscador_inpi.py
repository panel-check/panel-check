"""
Buscador INPI dentro del panel (pantalla /buscador-inpi): replica el buscador de marcas del portal
de INPI (portaltramites.inpi.gob.ar/marcasconsultas/busqueda) consultando SIEMPRE a INPI en vivo.

Búsquedas (las mismas grillas que usa el portal, por POST JSON):
  - puntual      → /MarcasConsultas/GrillaMarcasPuntual      {Acta, Resolucion}
  - avanzada     → /MarcasConsultas/GrillaMarcasAvanzada     {Tipo_Resolucion, Clase, Denominacion +
                   TipoBusquedaDenominacion (0 empieza con / 1 contiene), Titular + TipoBusquedaTitular,
                   fechas de ingreso y de resolución (dd/mm/aaaa), vigentes, limit, offset}
  - renovaciones → /MarcasConsultas/GrillaMarcasRenovaciones {FechaPresentacionDesde/Hasta, limit, offset}
  - cuit         → webservice SOAP ConsultaCuitOTitular (el portal no busca por CUIT; ver inpi_lead)

INPI distingue tildes («CATASTROFE» no encuentra «CATÁSTROFE»), pero acepta los comodines de SQL
(«_» = una letra cualquiera, «%» = cualquier cosa; probado el 06/11/2026). Si una búsqueda por
denominación o titular no trae nada, se reintenta con «_» en lugar de cada vocal y después se filtra
acá sin tildes, para quedarse solo con lo que de verdad coincide (`sin_tildes` en la respuesta).

Detalle de un acta (`detalle_acta`): UN pedido al expediente (datos, titulares, gestión, resolución,
protección/limitación, publicaciones, oposiciones, vistas, logo) y otro a la Grilla Digital (los
archivos). `archivo_grilla` baja un PDF de la grilla para verlo desde el panel, solo si ese documento
figura en la grilla de esa acta.

Sin FastAPI: las rutas están en buscador_inpi_api.py. No guarda nada en la base.
"""

import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

import inpi_lead

BASE = inpi_lead.BASE
URL_BUSQUEDA = f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE"
ART = timezone(timedelta(hours=-3))

# Mismo listado que el selector «TIPO DE RESOLUCION» del portal (CboTipo).
TIPOS_RESOLUCION = {
    "": "Todas", "A": "Abandonada", "CA": "Caduca", "C": "Concedida", "N": "Denegada",
    "D": "Desistida", "E": "En trámite", "NU": "Nula",
}
LIMITE_MAX = 200          # el portal pagina de a 5 a 200
TOPE_SIN_TILDES = 200     # filas que se piden a INPI en el reintento sin tildes

# Pocas consultas a la vez: el portal deja de responder si se le pide mucho seguido.
_semaforo = threading.BoundedSemaphore(2)


class BuscadorError(Exception):
    """Error para mostrarle a la persona (datos mal cargados o INPI que no responde)."""


# ── Texto ──────────────────────────────────────────────────────────────────

def comparable(texto) -> str:
    """Mayúsculas, sin tildes y solo letras, números y espacios simples («Cine  Catástrofe!» → «CINE CATASTROFE»)."""
    sin = "".join(c for c in unicodedata.normalize("NFKD", str(texto or "")) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", sin.upper()).split())


def patron_sin_tildes(texto: str) -> str:
    """Cada vocal (con o sin tilde) pasa a «_», el comodín de una letra de INPI: «CATASTROFE» → «C_T_STR_F_»."""
    return re.sub(r"[AEIOUÁÉÍÓÚÜÀÈÌÒÙÂÊÎÔÛÄËÏÖ]", "_", (texto or "").upper())


def _coincide(valor, buscado: str, empieza: bool) -> bool:
    v, b = comparable(valor), comparable(buscado)
    if not b:
        return True
    return v.startswith(b) if empieza else b in v


def _fecha_dotnet(valor):
    """«/Date(1790701465000)/» → «2026-09-29» (hora Argentina). Fechas vacías (año 1) → None."""
    m = re.search(r"Date\((-?\d+)", str(valor or ""))
    if not m:
        return None
    ms = int(m.group(1))
    if ms < 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(ART).date().isoformat()


def _fecha_dotnet_hora(valor):
    m = re.search(r"Date\((-?\d+)", str(valor or ""))
    if not m or int(m.group(1)) < 0:
        return None
    return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).astimezone(ART).strftime("%Y-%m-%d %H:%M")


def _fecha_ws(valor):
    """El webservice trae «2026-09-29T14:04:25» (o vacío)."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(valor or ""))
    return m.group(1) if m else None


def fecha_ddmmaaaa(valor: str) -> str:
    """Acepta «2026-09-29» o «29/09/2026» y devuelve lo que pide INPI («29/09/2026»). Vacío → vacío."""
    v = (valor or "").strip()
    if not v:
        return ""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", v):
        return v
    raise BuscadorError(f"La fecha «{v}» no es válida (dd/mm/aaaa).")


_ESTADOS = {"A": "Abandonada", "CA": "Caduca", "C": "Concedida", "N": "Denegada", "D": "Desistida",
            "E": "En trámite", "NU": "Nula"}


def estado_legible(valor):
    v = (str(valor).strip() if valor is not None else "")
    if not v:
        return None
    return _ESTADOS.get(v.upper(), v)


def _cuit_de_titulares(titulares: str):
    """Los titulares vienen como «30711122989    XANTANA SRL 100.00%»: el CUIT, si está, va primero."""
    m = re.match(r"\s*(\d{11})\b", titulares or "")
    return m.group(1) if m else None


def _titulares_limpios(titulares: str) -> str:
    """Saca CUIT y espacios de más: «30711122989    XANTANA SRL 100.00%» → «XANTANA SRL 100.00%»."""
    partes = re.split(r"(?<=%)\s*", titulares or "")
    limpio = [" ".join(re.sub(r"^\s*\d{11}\s+", "", p).split()) for p in partes]
    return " · ".join(p for p in limpio if p)


def fila_marca(row: dict) -> dict:
    """Una fila de GrillaMarcasPuntual / GrillaMarcasAvanzada, normalizada."""
    titulares = str(row.get("Titulares") or "")
    return {
        "acta": str(row.get("Acta") or "").strip(),
        "denominacion": (str(row.get("Denominacion") or "").strip() or None),
        "clase": row.get("Clase"),
        "tipo_marca": (str(row.get("Tipo_Marca") or "").strip() or None),
        "titulares": _titulares_limpios(titulares) or None,
        "cuit": _cuit_de_titulares(titulares),
        "fecha_ingreso": _fecha_dotnet(row.get("Fecha_Ingreso")),
        "numero_resolucion": (str(row.get("Numero_Resolucion") or "").strip() or None),
        "estado": estado_legible(row.get("Estado")),
        "vencimiento": _fecha_dotnet(row.get("Fecha_Vencimiento")),
    }


def fila_ws(row: dict) -> dict:
    titulares = str(row.get("Titulares") or "")
    return {
        "acta": str(row.get("Acta") or "").strip(),
        "denominacion": (str(row.get("Denominacion") or "").strip() or None),
        "clase": int(row["Clase"]) if str(row.get("Clase") or "").strip().isdigit() else row.get("Clase"),
        "tipo_marca": (str(row.get("Tipo_Marca") or "").strip() or None),
        "titulares": _titulares_limpios(titulares) or None,
        "cuit": _cuit_de_titulares(titulares),
        "fecha_ingreso": _fecha_ws(row.get("Fecha_Ingreso")),
        "numero_resolucion": None, "estado": None, "vencimiento": None,
    }


def fila_renovacion(row: dict) -> dict:
    return {
        "acta": str(row.get("Acta") or "").strip(),
        "fecha_presentacion": _fecha_dotnet(row.get("Fecha_Presentacion")),
        "clase": row.get("Nro_Clase"),
        "numero_resolucion": (str(row.get("Nro_Resolucion") or "").strip() or None),
        "renovadora": (str(row.get("Renovadora") or "").strip() or None),
    }


# ── Conexión con INPI ──────────────────────────────────────────────────────

def _sesion(timeout: int = 30) -> requests.Session:
    """Sesión con las cookies de la página de búsqueda (sin ellas la grilla avanzada devuelve 0 filas)."""
    s = inpi_lead._crear_sesion()
    try:
        s.get(URL_BUSQUEDA, timeout=timeout)
    except requests.RequestException:
        pass
    return s


def _pedir_grilla(s, ruta: str, cuerpo: dict, timeout: int = 45) -> dict:
    try:
        r = inpi_lead._get_con_reintentos(lambda: s.post(
            f"{BASE}{ruta}", json=cuerpo, timeout=timeout,
            headers={"Referer": URL_BUSQUEDA, "X-Requested-With": "XMLHttpRequest"},
        ))
    except requests.RequestException as e:
        raise BuscadorError(f"INPI no respondió ({e.__class__.__name__}). Probá de nuevo en un rato.")
    if "Web Page Blocked" in r.text or "Attack ID" in r.text:
        raise BuscadorError("INPI bloqueó la consulta. Esperá unos minutos y probá de nuevo.")
    try:
        datos = r.json()
    except ValueError:
        raise BuscadorError("INPI devolvió una respuesta que no se pudo leer. Probá de nuevo en un rato.")
    return datos if isinstance(datos, dict) else {}


def _limites(limit, offset):
    try:
        limit = int(limit or 50)
        offset = int(offset or 0)
    except (TypeError, ValueError):
        raise BuscadorError("Paginado inválido.")
    return max(1, min(LIMITE_MAX, limit)), max(0, offset)


# ── Búsquedas ──────────────────────────────────────────────────────────────

def buscar_puntual(acta: str = "", resolucion: str = "") -> dict:
    acta = re.sub(r"\D", "", acta or "")
    resolucion = re.sub(r"\D", "", resolucion or "")
    if not acta and not resolucion:
        raise BuscadorError("Poné un número de acta o de resolución.")
    with _semaforo:
        s = _sesion()
        datos = _pedir_grilla(s, "/MarcasConsultas/GrillaMarcasPuntual", {"Acta": acta, "Resolucion": resolucion})
    filas = [fila_marca(r) for r in (datos.get("rows") or []) if isinstance(r, dict)]
    return {"modo": "puntual", "total": len(filas), "filas": filas}


def _cuerpo_avanzada(p: dict, limit: int, offset: int, denominacion: str, titular: str) -> dict:
    return {
        "Tipo_Resolucion": p.get("tipo_resolucion") or "",
        "Clase": str(p.get("clase") or "-1"),
        "TipoBusquedaDenominacion": "0" if p.get("denominacion_empieza") else "1",
        "Denominacion": denominacion,
        "Titular": titular,
        "TipoBusquedaTitular": "0" if p.get("titular_empieza") else "1",
        "Fecha_IngresoDesde": fecha_ddmmaaaa(p.get("ingreso_desde")),
        "Fecha_IngresoHasta": fecha_ddmmaaaa(p.get("ingreso_hasta")),
        "Fecha_ResolucionDesde": fecha_ddmmaaaa(p.get("resolucion_desde")),
        "Fecha_ResolucionHasta": fecha_ddmmaaaa(p.get("resolucion_hasta")),
        "vigentes": bool(p.get("vigentes")),
        "limit": limit, "offset": offset,
    }


def buscar_avanzada(p: dict) -> dict:
    """p: tipo_resolucion, clase, denominacion, denominacion_empieza, titular, titular_empieza,
    ingreso_desde/hasta, resolucion_desde/hasta, vigentes, limit, offset."""
    denominacion = " ".join(str(p.get("denominacion") or "").split())
    titular = " ".join(str(p.get("titular") or "").split())
    if p.get("tipo_resolucion") not in (None, "") and p.get("tipo_resolucion") not in TIPOS_RESOLUCION:
        raise BuscadorError("Tipo de resolución inválido.")
    clase = str(p.get("clase") or "").strip()
    if clase and clase != "-1" and not (clase.isdigit() and 1 <= int(clase) <= 45):
        raise BuscadorError("La clase va de 1 a 45.")
    hay_fecha = any(p.get(k) for k in ("ingreso_desde", "ingreso_hasta", "resolucion_desde", "resolucion_hasta"))
    if len(comparable(denominacion)) < 2 and len(comparable(titular)) < 2 and not hay_fecha:
        raise BuscadorError("Escribí al menos 2 letras de la denominación o del titular, o elegí un rango de fechas.")
    limit, offset = _limites(p.get("limit"), p.get("offset"))
    with _semaforo:
        s = _sesion()
        datos = _pedir_grilla(s, "/MarcasConsultas/GrillaMarcasAvanzada",
                              _cuerpo_avanzada(p, limit, offset, denominacion, titular))
        total = int(datos.get("total") or 0)
        filas = [fila_marca(r) for r in (datos.get("rows") or []) if isinstance(r, dict)]
        sin_tildes = False
        # Sin resultados: puede ser una tilde. Se reintenta con comodines y se filtra acá.
        if not total and offset == 0 and (patron_sin_tildes(denominacion) != denominacion.upper()
                                          or patron_sin_tildes(titular) != titular.upper()):
            datos = _pedir_grilla(s, "/MarcasConsultas/GrillaMarcasAvanzada", _cuerpo_avanzada(
                p, TOPE_SIN_TILDES, 0, patron_sin_tildes(denominacion), patron_sin_tildes(titular)))
            candidatas = [fila_marca(r) for r in (datos.get("rows") or []) if isinstance(r, dict)]
            filas = [f for f in candidatas
                     if _coincide(f["denominacion"], denominacion, bool(p.get("denominacion_empieza")))
                     and _coincide(f["titulares"], titular, bool(p.get("titular_empieza")))]
            total, sin_tildes = len(filas), True
            filas = filas[:limit]
    return {"modo": "avanzada", "total": total, "filas": filas, "sin_tildes": sin_tildes,
            "recortado": sin_tildes and int(datos.get("total") or 0) > TOPE_SIN_TILDES}


def buscar_renovaciones(desde: str, hasta: str, limit=50, offset=0) -> dict:
    desde, hasta = fecha_ddmmaaaa(desde), fecha_ddmmaaaa(hasta)
    if not desde or not hasta:
        raise BuscadorError("Elegí la fecha de presentación desde y hasta.")
    limit, offset = _limites(limit, offset)
    with _semaforo:
        s = _sesion()
        datos = _pedir_grilla(s, "/MarcasConsultas/GrillaMarcasRenovaciones",
                              {"FechaPresentacionDesde": desde, "FechaPresentacionHasta": hasta,
                               "limit": limit, "offset": offset})
    filas = [fila_renovacion(r) for r in (datos.get("rows") or []) if isinstance(r, dict)]
    return {"modo": "renovaciones", "total": int(datos.get("total") or 0), "filas": filas}


def buscar_cuit(cuit: str) -> dict:
    cuit = re.sub(r"\D", "", cuit or "")
    if len(cuit) != 11:
        raise BuscadorError("El CUIT tiene que tener 11 números.")
    with _semaforo:
        filas = [fila_ws(r) for r in inpi_lead.consultar_titular_ws(cuit=cuit) if r.get("Acta")]
    filas.sort(key=lambda f: f["fecha_ingreso"] or "", reverse=True)
    return {"modo": "cuit", "total": len(filas), "filas": filas}


def buscar_rapido(q: str, limit=50) -> dict:
    """Lo que se escribe en la lupa con «INPI»: acta (o resolución), CUIT o denominación (contiene)."""
    texto = " ".join((q or "").split())
    digitos = re.sub(r"\D", "", texto)
    if digitos and re.fullmatch(r"[\d.\-\s/]+", texto):
        if len(digitos) == 11:
            return buscar_cuit(digitos)
        res = buscar_puntual(acta=digitos)
        if not res["total"]:
            res = buscar_puntual(resolucion=digitos)
            res["por_resolucion"] = True
        return res
    return buscar_avanzada({"denominacion": texto, "limit": limit})


# ── Detalle de un acta ─────────────────────────────────────────────────────

def _vista(v: dict) -> dict:
    f = inpi_lead._fecha_valida_opo
    return {"tipo": (v.get("Tipo") or "").strip() or None, "fecha": f(v.get("Fecha_Vista")),
            "notificacion": f(v.get("Fecha_Notificacion")), "vencimiento": f(v.get("Fecha_Vencimiento")),
            "contestacion": f(v.get("Fecha_Contestacion"))}


def _archivo(a: dict) -> dict:
    return {"indice": (a.get("Indice") or "").strip() or None,
            "referencia": (a.get("Referencia") or "").strip() or None,
            "fecha": _fecha_dotnet_hora(a.get("Fecha")),
            "gde": (a.get("NombreGde") or "").strip() or None,
            "id": a.get("id_Documento_encriptado") or None,
            "nombre": (a.get("ruta") or "").rsplit("/", 1)[-1] or None}


def detalle_acta(acta: str, timeout: int = 40) -> dict:
    acta = re.sub(r"\D", "", acta or "")
    if not acta:
        raise BuscadorError("Número de acta inválido.")
    with _semaforo:
        s = inpi_lead._crear_sesion()
        try:
            r = inpi_lead._get_con_reintentos(lambda: s.post(
                f"{BASE}/MarcasConsultas/Resultado", data={"acta": acta}, timeout=timeout,
                headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"}))
        except requests.RequestException as e:
            raise BuscadorError(f"INPI no respondió ({e.__class__.__name__}). Probá de nuevo en un rato.")
        html = r.text
        if "Web Page Blocked" in html or "Attack ID" in html:
            raise BuscadorError("INPI bloqueó la consulta. Esperá unos minutos y probá de nuevo.")
        if "GESTION DEL TRAMITE" not in html and "TITULARIDAD" not in html:
            raise BuscadorError("INPI no tiene un expediente con ese número de acta.")
        try:
            archivos = inpi_lead._buscar_archivos_grilla(s, acta, timeout=timeout)
            error_grilla = None
        except requests.RequestException:
            archivos, error_grilla = [], "No se pudo leer la Grilla Digital (INPI no respondió)."

    datos = inpi_lead.datos_de_expediente(html)
    exp = inpi_lead.parsear_datos_expediente(html)
    opos = inpi_lead.parsear_oposiciones(html)
    vistas, error_vistas = inpi_lead._json_de_js(html, "vistas")
    logo = inpi_lead.extraer_logo(html)
    logo_uri = None
    if logo:
        import base64

        contenido, mime = logo
        logo_uri = f"data:{mime};base64,{base64.b64encode(contenido).decode()}"
    grilla = [_archivo(a) for a in archivos if isinstance(a, dict)]
    grilla.sort(key=lambda a: a["fecha"] or "", reverse=True)
    return {
        "acta": acta,
        "denominacion": exp.get("denominacion"),
        "tipo_marca": exp.get("tipo_marca") or datos.get("tipo"),
        "clase": exp.get("clase") or datos.get("clase"),
        "proteccion": exp.get("proteccion"),
        "limitacion": exp.get("limitacion"),
        "publicaciones": exp.get("publicaciones") or [],
        "titulares": datos.get("titular"),
        "cuit": datos.get("cuit"),
        "fecha_presentacion": datos.get("fecha_presentacion"),
        "agente": datos.get("agente"),
        "matricula_agente": datos.get("matricula_agente"),
        "caracter": datos.get("caracter"),
        "particular": datos.get("particular"),
        "estado_tramite": datos.get("estado_tramite"),
        "fecha_concesion": datos.get("fecha_concesion"),
        "numero_disposicion": datos.get("numero_disposicion"),
        "vencimiento": datos.get("fecha_vencimiento_marca"),
        "oposiciones": opos.get("items") or [],
        "error_oposiciones": bool(opos.get("error_lectura")),
        "vistas": [_vista(v) for v in (vistas or []) if isinstance(v, dict)],
        "error_vistas": bool(error_vistas),
        "grilla": grilla,
        "error_grilla": error_grilla,
        "logo": logo_uri,
    }


def archivo_grilla(acta: str, id_doc: str, timeout: int = 60):
    """(bytes, content_type, nombre) de un archivo de la Grilla Digital del acta. Solo baja documentos
    que figuran en la grilla de ESA acta (no sirve para pedir cualquier id a INPI)."""
    acta = re.sub(r"\D", "", acta or "")
    if not acta or not id_doc:
        raise BuscadorError("Faltan datos del archivo.")
    with _semaforo:
        s = inpi_lead._crear_sesion()
        try:
            archivos = inpi_lead._buscar_archivos_grilla(s, acta, timeout=timeout)
        except requests.RequestException:
            raise BuscadorError("INPI no respondió al leer la Grilla Digital. Probá de nuevo en un rato.")
        elegido = next((a for a in archivos if isinstance(a, dict) and a.get("id_Documento_encriptado") == id_doc), None)
        if not elegido:
            raise BuscadorError("Ese archivo no figura en la Grilla Digital del acta.")
        nombre = (elegido.get("ruta") or "").rsplit("/", 1)[-1] or f"acta-{acta}.pdf"
        try:
            r = inpi_lead._get_con_reintentos(lambda: s.get(
                f"{BASE}/Home/edmsxidd", params={"id": id_doc, "nombre": nombre}, timeout=timeout,
                headers={"Referer": f"{BASE}/Home/GrillaDigital"}))
        except requests.RequestException:
            raise BuscadorError("INPI no respondió al bajar el archivo. Probá de nuevo en un rato.")
    tipo = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if r.status_code != 200 or not r.content or tipo in ("text/html", ""):
        raise BuscadorError("INPI no entregó el archivo. Abrilo desde la Grilla Digital en INPI.")
    return r.content, tipo, nombre
