"""
Verificador de marca de la web (smartiesconsultora.com.ar).

El formulario del home de la web (un bloque HTML dentro de WordPress) le manda
el nombre de una marca al panel; el panel la busca en INPI y devuelve si hay una
marca idéntica, parecidas o nada. Cada consulta queda guardada (sección
«Consultas web» del panel, menú Más) con los datos de contacto de quien la hizo.

Este módulo no depende de FastAPI ni de la base: lo usa verificador_api.py y se
prueba aparte (scripts/test_verificador.py).

Qué significa cada veredicto (lo que ve la persona en la web):
  disponible      no apareció ninguna marca idéntica ni parecida
  con_similares   hay marcas parecidas (se escriben o suenan parecido) → hay que revisarlo
  no_disponible   hay una marca idéntica (mismo nombre, o igual sin espacios)
  error           INPI no respondió a tiempo: la consulta queda guardada para revisarla a mano

Es una búsqueda PRELIMINAR sobre la denominación: no mira la clase ni la actividad
(la persona no la elige) y no reemplaza al estudio de disponibilidad del estudio.
"""

import html
import os
import re
import secrets
import threading
import time
from collections import deque
from urllib.parse import urlsplit

import similitud

# ── Parámetros ────────────────────────────────────────────────────────────
VALIDEZ_DIAS = 90            # cuánto tiempo vale el link de un resultado
CACHE_HORAS = 24             # una misma marca no se vuelve a pedir a INPI en este tiempo
UMBRAL_SIMILAR = 80          # puntaje (similitud.puntaje_nombres) desde el cual una marca es «parecida»
UMBRAL_IDENTICA = 98         # 100 = mismo nombre, 98 = igual sin espacios
MUESTRAS_PUBLICAS = 5        # marcas que se le muestran a la persona
MUESTRAS_INTERNAS = 20       # marcas que se guardan para el equipo
LIMITE_INPI = 20             # filas que devuelve INPI por búsqueda (tope de GrillaMarcasAvanzada)
PEDIDOS_INPI_POR_MINUTO = 20  # tope global: protege la cuenta de INPI de un uso masivo
TIMEOUT_INPI = 20            # segundos por pedido a INPI
PAUSA_ENTRE_PEDIDOS = 1.0    # segundos entre una búsqueda y la siguiente a INPI

ORIGENES_POR_DEFECTO = "https://smartiesconsultora.com.ar,https://www.smartiesconsultora.com.ar"
RUTAS_CRUZADAS = ("/api/publico/verificador/",)  # las únicas que puede llamar la web desde otro dominio

VEREDICTOS = {"disponible", "con_similares", "no_disponible", "error"}
VEREDICTO_LEGIBLE = {
    "disponible": "Sin coincidencias",
    "con_similares": "Con marcas similares",
    "no_disponible": "Marca ocupada",
    "error": "INPI no respondió (revisar a mano)",
    "pendiente": "En proceso",
}

EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
CODIGO_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")


class ErrorInpi(Exception):
    """INPI no respondió bien (o no se quiso molestar a INPI): el veredicto es «error»."""


# ── Orígenes permitidos (CORS y CSRF) ─────────────────────────────────────
def origenes_permitidos() -> set:
    crudo = os.environ.get("VERIFICADOR_ORIGENES") or ORIGENES_POR_DEFECTO
    return {o.strip().rstrip("/").lower() for o in crudo.split(",") if o.strip()}


def origen_de(valor: str) -> str:
    """'https://sitio.com/pagina?x=1' → 'https://sitio.com' (vacío si no es una URL)."""
    p = urlsplit(valor or "")
    return f"{p.scheme}://{p.netloc}".lower() if p.scheme and p.netloc else ""


def origen_permitido(valor: str) -> bool:
    return origen_de(valor) in origenes_permitidos()


def es_ruta_cruzada(ruta: str) -> bool:
    return (ruta or "").startswith(RUTAS_CRUZADAS)


# ── Validación de lo que manda la web ────────────────────────────────────
def _limpio(valor, maximo: int) -> str:
    texto = re.sub(r"[\x00-\x1f\x7f]+", " ", str(valor or ""))
    return re.sub(r"\s+", " ", texto).strip()[:maximo]


def validar_lead(marca, actividad, nombre, email, telefono="", web="") -> dict:
    """Devuelve los datos limpios o levanta ValueError con un mensaje para la persona."""
    d = {
        "marca": _limpio(marca, 80),
        "actividad": _limpio(actividad, 300),
        "nombre": _limpio(nombre, 120),
        "email": _limpio(email, 200).lower(),
        "telefono": _limpio(telefono, 40),
        "web": _limpio(web, 200),
    }
    if len(d["marca"]) < 2:
        raise ValueError("Escribí el nombre de tu marca.")
    if not similitud.normalizar(d["marca"]):
        raise ValueError("Escribí el nombre de tu marca con letras o números.")
    if not d["actividad"]:
        raise ValueError("Contanos a qué se dedica.")
    if not d["nombre"]:
        raise ValueError("¿Cómo te llamás?")
    if not EMAIL_RE.match(d["email"]):
        raise ValueError("Revisá el e-mail.")
    return d


def validar_adicional(pregunta, nombre, email) -> dict:
    d = {"pregunta": str(pregunta or "").strip()[:2000], "nombre": _limpio(nombre, 120), "email": _limpio(email, 200).lower()}
    if len(d["pregunta"]) < 5:
        raise ValueError("Escribí tu consulta.")
    if not d["nombre"] or not EMAIL_RE.match(d["email"]):
        raise ValueError("Necesitamos tu nombre y un e-mail válido.")
    return d


def nuevo_codigo() -> str:
    """Código del link del resultado: largo y al azar, no se puede adivinar."""
    return secrets.token_urlsafe(9)


def clave_cache(marca: str) -> str:
    """Dos escrituras de la misma marca («BALI STONE» / «balistone») comparten caché."""
    return similitud.normalizar(marca).replace(" ", "")[:80]


# ── Búsqueda en INPI ──────────────────────────────────────────────────────
_ventana = deque()
_ventana_lock = threading.Lock()
_simultaneas = threading.BoundedSemaphore(3)


def _reservar_pedido():
    """Tope global de búsquedas por minuto (y 3 a la vez). Si se pasa, no se molesta a INPI."""
    ahora = time.time()
    with _ventana_lock:
        while _ventana and ahora - _ventana[0] > 60:
            _ventana.popleft()
        if len(_ventana) >= PEDIDOS_INPI_POR_MINUTO:
            raise ErrorInpi("demasiadas búsquedas en el último minuto")
        _ventana.append(ahora)


def variantes_de_busqueda(marca: str) -> list:
    """«BALI STONE» también se busca como «BALISTONE»: en INPI puede estar cargada pegada."""
    base = _limpio(marca, 80)
    sin_espacios = re.sub(r"\s+", "", base)
    out = [base]
    if sin_espacios and sin_espacios != base and len(sin_espacios) >= 3:
        out.append(sin_espacios)
    return out


def _buscar_una(s, base_url: str, denominacion: str, timeout: int) -> list:
    """Una búsqueda «contiene» en la API JSON de la búsqueda avanzada de INPI (la misma que
    usa inpi_lead.buscar_marca_por_denominacion). Levanta ErrorInpi si la respuesta no es la
    esperada: una lista vacía solo cuenta como «sin resultados» si INPI contestó bien."""
    import requests  # import local: los tests no necesitan la librería

    payload = {
        "Tipo_Resolucion": "", "Clase": "", "TipoBusquedaDenominacion": "1",  # 1 = CONTIENE
        "Denominacion": denominacion, "Titular": "", "TipoBusquedaTitular": "0",
        "Fecha_IngresoDesde": "", "Fecha_IngresoHasta": "", "Fecha_ResolucionDesde": "", "Fecha_ResolucionHasta": "",
        "vigentes": False, "limit": LIMITE_INPI, "offset": 0,
    }
    cabeceras = {"Referer": f"{base_url}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE", "X-Requested-With": "XMLHttpRequest"}
    ultimo = None
    for intento in range(2):
        try:
            r = s.post(f"{base_url}/MarcasConsultas/GrillaMarcasAvanzada", json=payload, headers=cabeceras, timeout=timeout)
            if r.status_code != 200:
                raise ErrorInpi(f"INPI respondió {r.status_code}")
            try:
                data = r.json()
            except ValueError:
                raise ErrorInpi("INPI no devolvió datos legibles (¿bloqueo?)")
            if not isinstance(data, dict) or "rows" not in data or not isinstance(data["rows"], list):
                raise ErrorInpi("INPI devolvió una respuesta inesperada")
            return data["rows"]
        except (requests.RequestException, ErrorInpi) as e:
            ultimo = e if isinstance(e, ErrorInpi) else ErrorInpi(f"error de conexión con INPI ({type(e).__name__})")
            if intento == 0:
                time.sleep(2)
    raise ultimo


def buscar_inpi(marca: str, timeout: int = TIMEOUT_INPI) -> list:
    """Filas de INPI para esa marca: [{acta, denominacion, clase, estado, numero_resolucion}].
    Levanta ErrorInpi si no se pudo consultar."""
    import requests
    import inpi_lead

    _reservar_pedido()
    if not _simultaneas.acquire(timeout=5):
        raise ErrorInpi("hay muchas búsquedas en curso")
    try:
        base = inpi_lead.BASE
        s = requests.Session()
        s.headers.update(inpi_lead.HEADERS)
        try:
            s.get(f"{base}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE", timeout=timeout)
        except requests.RequestException as e:
            raise ErrorInpi(f"error de conexión con INPI ({type(e).__name__})")

        filas, vistas = [], set()
        for i, variante in enumerate(variantes_de_busqueda(marca)):
            if i:
                time.sleep(PAUSA_ENTRE_PEDIDOS)
            try:
                rows = _buscar_una(s, base, variante, timeout)
            except ErrorInpi:
                if i == 0:
                    raise  # sin la búsqueda principal no hay veredicto
                break      # la variante es un extra: con lo que hay alcanza
            for row in rows:
                acta = str(row.get("Acta", "")).strip()
                if not acta or acta in vistas:
                    continue
                vistas.add(acta)
                filas.append({
                    "acta": acta,
                    "denominacion": str(row.get("Denominacion", "")).strip(),
                    "clase": row.get("Clase"),
                    "estado": str(row.get("Estado") or "").strip(),
                    "numero_resolucion": str(row.get("Numero_Resolucion") or "").strip(),
                })
        return filas
    finally:
        _simultaneas.release()


# ── Veredicto ─────────────────────────────────────────────────────────────
def _clase_entera(valor):
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def evaluar(marca: str, filas: list, umbral: int = UMBRAL_SIMILAR, genericas=frozenset()) -> dict:
    """Compara la marca contra lo que devolvió INPI. `genericas` son las palabras que no distinguen
    ("SA", "GROUP"...: las mismas que usa la vigilancia), así «Pan Casero SA» cuenta como «Pan Casero».
    Devuelve {verdict, exactCount, similarCount, samples (para la web), muestras (para el equipo),
    posible_mas (INPI llenó la página: puede haber más marcas que no se vieron)}."""
    candidatas = []
    for f in filas or []:
        denom = f.get("denominacion") or ""
        puntaje, motivos = similitud.puntaje_nombres(marca, denom, genericas)
        if puntaje < umbral:
            continue
        candidatas.append({
            "acta": f.get("acta"), "denominacion": denom, "clase": f.get("clase"), "estado": f.get("estado") or "",
            "puntaje": puntaje, "motivos": motivos,
            "tipo": "identica" if puntaje >= UMBRAL_IDENTICA else "similar",
        })
    candidatas.sort(key=lambda c: (-c["puntaje"], c["denominacion"]))
    exactas = sum(1 for c in candidatas if c["tipo"] == "identica")
    similares = len(candidatas) - exactas

    if exactas:
        veredicto = "no_disponible"
    elif similares:
        veredicto = "con_similares"
    else:
        veredicto = "disponible"

    publicas = []
    for c in candidatas:
        clase = _clase_entera(c["clase"])
        if clase is None:
            continue
        publicas.append({"denominacion": c["denominacion"], "clase": clase})
        if len(publicas) >= MUESTRAS_PUBLICAS:
            break

    return {
        "verdict": veredicto,
        "exactCount": exactas,
        "similarCount": similares,
        "samples": publicas,
        "muestras": candidatas[:MUESTRAS_INTERNAS],
        "posible_mas": len(filas or []) >= LIMITE_INPI,
    }


# ── Mails de aviso al equipo ─────────────────────────────────────────────
def asunto_aviso(marca: str, veredicto: str) -> str:
    return f"Consulta web: {marca} ({VEREDICTO_LEGIBLE.get(veredicto, veredicto)})"


def asunto_adicional(marca: str) -> str:
    return f"Consulta web adicional: {marca or 'sin marca'}"


def _fila(etiqueta: str, valor: str) -> str:
    if not valor:
        return ""
    return (f'<tr><td style="padding:4px 14px 4px 0;color:#666;vertical-align:top">{html.escape(etiqueta)}</td>'
            f'<td style="padding:4px 0">{valor}</td></tr>')


def armar_mail_aviso(v: dict, panel_url: str) -> tuple:
    """(html, texto) del aviso de una consulta nueva. `v` es la fila de verificaciones_marca."""
    esc = html.escape
    veredicto = VEREDICTO_LEGIBLE.get(v.get("veredicto"), v.get("veredicto") or "")
    email = v.get("email") or ""
    telefono = v.get("telefono") or ""
    web = v.get("web") or ""
    muestras = v.get("muestras") or []
    lista = "".join(
        f'<li>{esc(str(m.get("denominacion") or ""))} — clase {esc(str(m.get("clase") or "?"))}'
        f'{" · " + esc(str(m["estado"])) if m.get("estado") else ""}'
        f'{" · acta " + esc(str(m["acta"])) if m.get("acta") else ""}</li>'
        for m in muestras[:10]
    )
    filas = "".join([
        _fila("Marca", f"<strong>{esc(v.get('marca') or '')}</strong>"),
        _fila("Resultado", esc(veredicto)),
        _fila("Actividad", esc(v.get("actividad") or "")),
        _fila("Nombre", esc(v.get("nombre") or "")),
        _fila("E-mail", f'<a href="mailto:{esc(email)}">{esc(email)}</a>' if email else ""),
        _fila("Teléfono", esc(telefono)),
        _fila("Web o redes", esc(web)),
    ])
    cuerpo = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222">'
        '<p>Alguien consultó una marca en la web de Smarties.</p>'
        f'<table style="border-collapse:collapse">{filas}</table>'
        + (f'<p style="margin:14px 0 4px"><strong>Marcas encontradas en INPI</strong></p><ul style="margin:0">{lista}</ul>' if lista else "")
        + f'<p style="margin-top:18px"><a href="{esc(panel_url)}/consultas-web">Ver en el panel (Más → Consultas web)</a></p>'
        '</div>'
    )
    texto = "\n".join(x for x in [
        "Alguien consultó una marca en la web de Smarties.",
        f"Marca: {v.get('marca') or ''}", f"Resultado: {veredicto}", f"Actividad: {v.get('actividad') or ''}",
        f"Nombre: {v.get('nombre') or ''}", f"E-mail: {email}", f"Teléfono: {telefono}" if telefono else "",
        f"Web o redes: {web}" if web else "", f"Panel: {panel_url}/consultas-web",
    ] if x)
    return cuerpo, texto


def armar_mail_adicional(c: dict, marca: str, codigo: str, panel_url: str) -> tuple:
    esc = html.escape
    email = c.get("email") or ""
    cuerpo = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222">'
        f'<p>{esc(c.get("nombre") or "")} dejó una consulta adicional sobre la marca <strong>{esc(marca or "")}</strong>'
        f'{" (verificación " + esc(codigo) + ")" if codigo else ""}.</p>'
        f'<blockquote style="margin:10px 0;padding:8px 14px;border-left:3px solid #ccc;white-space:pre-wrap">{esc(c.get("pregunta") or "")}</blockquote>'
        f'<p>E-mail: <a href="mailto:{esc(email)}">{esc(email)}</a></p>'
        f'<p><a href="{esc(panel_url)}/consultas-web">Ver en el panel (Más → Consultas web)</a></p></div>'
    )
    texto = (f"{c.get('nombre') or ''} dejó una consulta adicional sobre la marca {marca or ''}"
             f"{' (verificación ' + codigo + ')' if codigo else ''}.\n\n{c.get('pregunta') or ''}\n\n"
             f"E-mail: {email}\nPanel: {panel_url}/consultas-web")
    return cuerpo, texto


# ── Tablas ────────────────────────────────────────────────────────────────
def crear_tablas(cur):
    """Idempotente."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS verificaciones_marca (
            id               SERIAL PRIMARY KEY,
            codigo           TEXT NOT NULL UNIQUE,
            creada_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
            marca            TEXT NOT NULL,
            actividad        TEXT,
            nombre           TEXT,
            email            TEXT,
            telefono         TEXT,
            web              TEXT,
            veredicto        TEXT NOT NULL DEFAULT 'pendiente',
            exactos          INTEGER NOT NULL DEFAULT 0,
            similares        INTEGER NOT NULL DEFAULT 0,
            muestras         JSONB NOT NULL DEFAULT '[]'::jsonb,
            posible_mas      BOOLEAN NOT NULL DEFAULT false,
            error            TEXT,
            desde_cache      BOOLEAN NOT NULL DEFAULT false,
            consultada_en    TIMESTAMPTZ,
            origen           TEXT,
            ip               TEXT,
            estado           TEXT NOT NULL DEFAULT 'nueva',
            revisado_por     TEXT,
            revisado_en      TIMESTAMPTZ,
            aviso_enviado_en TIMESTAMPTZ,
            aviso_error      TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_verif_marca_creada ON verificaciones_marca(creada_en DESC)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS verificaciones_marca_consultas (
            id               SERIAL PRIMARY KEY,
            verificacion_id  INTEGER REFERENCES verificaciones_marca(id) ON DELETE CASCADE,
            creada_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
            marca            TEXT,
            nombre           TEXT,
            email            TEXT,
            pregunta         TEXT NOT NULL,
            ip               TEXT,
            aviso_enviado_en TIMESTAMPTZ,
            aviso_error      TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_verif_consultas_verif ON verificaciones_marca_consultas(verificacion_id)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS verificaciones_marca_cache (
            clave          TEXT PRIMARY KEY,
            filas          JSONB NOT NULL,
            consultada_en  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
