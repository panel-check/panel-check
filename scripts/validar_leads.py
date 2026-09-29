"""
Paso 5 — Para las marcas con matrícula "Part." o vacía, entra al expediente
completo en el portal de trámites de INPI, lee CARACTER (GESTION DEL TRAMITE) y,
si está vacío (lead real), abre GRILLA DIGITAL y descarga el Formulario para
sacarle el email.

Reescrito para usar `requests` puro (sin navegador). El WAF de INPI bloquea
las visitas hechas con Chromium/Playwright headless (respuesta "Web Page
Blocked! Attack ID: 20000051"), pero no bloquea peticiones HTTP simples con
`requests` — probablemente por fingerprinting del navegador headless. Además,
el dato de CARACTER no está en la página `Resultado?acta=X` (GET, la que
aparece en el CSV) ni en `Grilla?acta=X`: hay que hacer un POST a
`/MarcasConsultas/Resultado` con `acta` en el body (replicando el form
`frmGD` que usa el propio sitio), que devuelve la página completa con la
sección "GESTION DEL TRAMITE".

Flujo real (confirmado a mano, ver docs/proceso-original.md):
  1. GET  /MarcasConsultas/Grilla                     (cookies de sesión)
  2. POST /MarcasConsultas/Resultado  {acta}           -> HTML con GESTION DEL TRAMITE
     - Si no aparece ningún campo AGENTE/CARACTER en esa sección: lead real.
     - Si aparece CARACTER: <valor> (ej. "Apoderado", "Gestor Ratificado"): no es lead.
  3. Si es lead, para sacar el email:
     POST /Home/GrillaDigital       {fname: "1-{acta}"}
     POST /Home/GrillaDigitales     {acta, limit, offset, direccion:1}  -> JSON de archivos
     buscar el archivo con Indice == "Formulario"
     GET  /Home/edmsxidd?id={id_Documento_encriptado}&nombre={archivo}  -> PDF
     leer el PDF con pdfplumber y buscar "EMAIL:" por regex

Uso:
    python3 validar_leads.py --in 11121_completo.csv --out 11121_leads.csv --limit 20

Nota: si INPI cambia el HTML o los endpoints, ajustar las constantes/regex de
abajo (quedan comentados los puntos exactos revisados el 2026-09-25).
"""

import argparse
import csv
import re
import sys
import time

import requests

BASE = "https://portaltramites.inpi.gob.ar"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
}

# Dentro del bloque "GESTION DEL TRAMITE", el campo CARACTER aparece como:
#   <label class="input">CARACTER:<span class="text-danger"> Apoderado </span></label>
RE_GESTION = re.compile(r"GESTION DEL TRAMITE.*?</h4>\s*</div>\s*<div[^>]*>(.*?)</div>\s*</div>\s*</div>", re.S)
RE_CARACTER = re.compile(r"CARACTER\s*:?\s*</label>|CARACTER\s*:", re.S)
RE_CARACTER_SPAN = re.compile(r"CARACTER\s*:?\s*<span[^>]*>(.*?)</span>", re.S)

# Dentro de TITULARIDAD aparece igual que CARACTER: "CUIT:<span>30715260898</span>".
# Se agregó cuando notamos que también viene para Denominativas (antes solo se
# guardaba para Mixtas/Figurativas, vía el webservice de completar_mixtas.py).
# Sirve como clave real para unificar leads del mismo titular en el panel
# (mejor que comparar texto de titular, que puede variar de tipeo).
# El <span> es opcional: personas jurídicas lo traen envuelto en <span>,
# personas físicas a veces lo traen como texto plano en el mismo <label>
# ("CUIT: 20302361291" sin span) — sin esto se perdía casi la mitad.
RE_CUIT_SPAN = re.compile(r"CUIT\s*:?\s*(?:<span[^>]*>)?\s*([\d.\-]{6,})", re.S)


def crear_sesion() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(f"{BASE}/MarcasConsultas/Grilla", timeout=30)
    except requests.RequestException as e:
        print(f"  aviso: no se pudo pre-cargar sesión ({e})", file=sys.stderr)
    return s


def _get_con_reintentos(fn, intentos: int = 3, espera: int = 3):
    ultimo_error = None
    for i in range(intentos):
        try:
            return fn()
        except requests.RequestException as e:
            ultimo_error = e
            time.sleep(espera)
    raise ultimo_error


def buscar_archivos_grilla(s: requests.Session, acta: str, timeout: int = 30) -> list[dict]:
    """POSTea a Home/GrillaDigital + Home/GrillaDigitales y devuelve la lista
    de archivos del expediente (Indice/Referencia/Fecha/NombreGde, etc.), la
    misma tabla que se ve en la Grilla Digital del portal. Devuelve [] si
    hay bloqueo del WAF o el JSON no viene como se espera."""
    r_gd = _get_con_reintentos(
        lambda: s.post(
            f"{BASE}/Home/GrillaDigital",
            headers={"Referer": f"{BASE}/MarcasConsultas/Resultado"},
            data={"fname": f"1-{acta}"},
            timeout=timeout,
        )
    )
    if "Web Page Blocked" in r_gd.text or "Attack ID" in r_gd.text:
        return []
    r_archivos = _get_con_reintentos(
        lambda: s.post(
            f"{BASE}/Home/GrillaDigitales",
            headers={"Referer": f"{BASE}/Home/GrillaDigital", "X-Requested-With": "XMLHttpRequest"},
            data={"acta": acta, "limit": 50, "offset": 0, "direccion": 1},
            timeout=timeout,
        )
    )
    try:
        return r_archivos.json().get("rows", [])
    except ValueError:
        return []


# Términos "universales" que usa INPI para marcar una oposición de tercero o
# una vista de oficio en la Grilla Digital (columna Indice o Referencia).
# Ejemplo real visto: Indice="Recibo de Ingreso", Referencia="Opo. de Marcas".
# Si aparece un caso real de vista de oficio con otra redacción, agregar el
# término acá.
TERMINOS_OPOSICION = ("OPO", "VISTA", "OPOSICION", "OPOSICIÓN")


def buscar_fila_oposicion(archivos: list[dict], fecha_publicacion: str | None = None) -> dict | None:
    """Igual criterio que detectar_oposicion, pero devuelve la fila cruda de
    Grilla Digital (no un string armado) para poder ubicar después, por la
    misma fecha, el PDF Formulario de esa oposición/vista — ver
    descargar_formulario_oposicion."""
    for a in archivos:
        indice = (a.get("Indice") or "").upper()
        referencia = (a.get("Referencia") or "").upper()
        if not any(t in indice or t in referencia for t in TERMINOS_OPOSICION):
            continue
        if fecha_publicacion:
            fecha_fila = _parsear_fecha_grilla(a.get("Fecha") or "")
            if not fecha_fila or fecha_fila < fecha_publicacion:
                continue
        return a
    return None


def detectar_oposicion(archivos: list[dict], fecha_publicacion: str | None = None) -> tuple[bool, str]:
    """Recorre los archivos de Grilla Digital buscando una fila de oposición
    o vista. Devuelve (tuvo_oposicion, detalle) — detalle queda vacío si no
    se encontró nada.

    fecha_publicacion (ISO YYYY-MM-DD) es la fecha de la publicación que se
    está evaluando (fila "Hoja Publicacion" más reciente, ver
    fecha_publicacion_de_archivos). Solo cuenta una oposición/vista fechada
    en o después de esa publicación: una marca puede tener varias
    publicaciones a lo largo de su vida (ej. una vista de una presentación
    anterior, ya resuelta, antes de que se vuelva a publicar), y una
    vista/oposición VIEJA, anterior a la publicación vigente, ya está
    resuelta — no es una alerta nueva. Confirmado con un caso real (acta
    4700141): "Vista de Marcas" del 16/07/2026, pero la "Hoja Publicacion"
    vigente es del 23/09/2026 — esa vista ya se solucionó, no corresponde
    marcarla como oposición pendiente.
    Si no se puede parsear la fecha de una fila candidata, se la descarta
    (mejor no marcar una oposición que no se puede confirmar que sea
    posterior a la publicación, que arriesgar un falso positivo)."""
    a = buscar_fila_oposicion(archivos, fecha_publicacion)
    if not a:
        return False, ""
    detalle = f"{a.get('Fecha', '')} - {a.get('Indice', '')} - {a.get('Referencia', '')}"
    return True, detalle


# Regexes para parsear el PDF "Formulario" de una SOLICITUD DE OPOSICION
# (confirmado a mano con un caso real: acta 4764327, oposición de
# PICAPIETRA LEANDRO contra la marca BALISTONE). El texto ya viene con
# espacios/saltos de línea colapsados a uno solo (ver
# descargar_formulario_oposicion) antes de aplicar estos regex.
RE_OPONENTE_NOMBRE = re.compile(r"OPONENTE NOMBRE:\s*(.+?)\s*G[ÉE]NERO:")
RE_OPONENTE_DOC = re.compile(r"TIPO DOC:\s*(\S+)\s+NUMERO:\s*(\S+)\s+CUIT:\s*(\d+)")
RE_FUNDAMENTO = re.compile(
    r"FUNDAMENTO:\s*(.+?)\s*NOMBRE DE LA MARCA A LA QUE SE OPONE:", re.S
)


# Para encontrar la(s) marca(s) PROPIA(s) del oponente citada(s) en el
# FUNDAMENTO, y así poder linkear directo al expediente de esa marca en vez
# de sólo mostrar el texto legal. Confirmado con 5 casos reales (29/09/2026):
#   - La mayoría cita la marca propia como "ACTA N° X" (a veces varias, ej.
#     renovaciones o varias clases) — el link más confiable, es la MISMA
#     acta que ya sabemos abrir con abrirActa() en el panel.
#   - El fundamento a veces también repite, al principio, el ACTA de la
#     marca QUE SE ESTÁ OPONIENDO (la nuestra) entre paréntesis — hay que
#     excluirla, o el botón "Ver marca opuesta" terminaría abriendo la
#     misma acta que ya estamos mirando.
#   - Un par de casos no dan ningún ACTA, solo un número de "Registro"
#     (Nro./Reg. Nr.) — ahí no hay link directo, guardamos denominación +
#     número para intentar una búsqueda por nombre (mejor esfuerzo: probado
#     a mano que la búsqueda por denominación en GrillaMarcasAvanzada no
#     siempre encuentra el registro exacto, por diferencias de espaciado
#     entre el texto legal y como está cargada la denominación en INPI).
RE_ACTA_CITADA = re.compile(r"ACTAS?\s+N[°ºo]\.?\s*(\d{5,8})", re.IGNORECASE)
# Enumeraciones tipo "Actas N° 4094053 (clase 35), 4099176 (clase 14) y
# 4099178 (clase 8)" — confirmado a mano (acta 4758381/4758371, TAMARA
# PONS): solo el PRIMER número está pegado a "Actas N°", los siguientes son
# número + "(clase ...)" sueltos separados por coma/"y". RE_ACTA_CITADA
# solo agarra ese primero; este regex agarra el grupo entero para poder
# sacar los demás con RE_NUMERO_EN_GRUPO_ACTAS.
RE_GRUPO_ACTAS = re.compile(
    r"ACTAS?\s+N[°ºo]\.?\s*\d{5,8}\s*\(clase[^)]*\)(?:\s*[,y]\s*\d{5,8}\s*\(clase[^)]*\))*",
    re.IGNORECASE,
)
RE_NUMERO_EN_GRUPO_ACTAS = re.compile(r"(\d{5,8})\s*\(clase", re.IGNORECASE)
RE_MARCA_OPONENTE_COMILLAS = re.compile(r'["“]([^"”]{2,60})["”]\s*Nro\.?\s*([\d.]{4,})')
RE_MARCA_OPONENTE_REG = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9]*(?:\s+[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9]*){0,4})\s+Reg\.?\s*Nr\.?\s*([\d.]{4,})"
)


def parsear_marca_oponente(fundamento: str, acta_propia: str | None = None) -> dict:
    """Busca, dentro del FUNDAMENTO ya extraído, a qué marca propia del
    oponente hace referencia — ver comentario de los regex arriba. Devuelve
    como mucho una de estas dos formas (nunca las dos):
      {"actas_marca_oponente": "4094053,4099176,4099178"}   -> link directo
      {"marca_oponente_denominacion": "...", "marca_oponente_numero_registro": "..."} -> a buscar por nombre
    {} si no encontró nada reconocible."""
    if not fundamento:
        return {}
    actas = []
    for m in RE_ACTA_CITADA.finditer(fundamento):
        acta = m.group(1)
        if acta != acta_propia and acta not in actas:
            actas.append(acta)
    for grupo in RE_GRUPO_ACTAS.finditer(fundamento):
        for m in RE_NUMERO_EN_GRUPO_ACTAS.finditer(grupo.group(0)):
            acta = m.group(1)
            if acta != acta_propia and acta not in actas:
                actas.append(acta)
    if actas:
        return {"actas_marca_oponente": ",".join(actas)}

    m = RE_MARCA_OPONENTE_COMILLAS.search(fundamento) or RE_MARCA_OPONENTE_REG.search(fundamento)
    if m:
        return {
            "marca_oponente_denominacion": m.group(1).strip(),
            "marca_oponente_numero_registro": m.group(2).replace(".", ""),
        }
    return {}


def parsear_formulario_oposicion(texto: str, acta_propia: str | None = None) -> dict:
    """Extrae oponente (nombre/tipo y número de doc/CUIT), el fundamento
    legal, y la marca propia del oponente (ver parsear_marca_oponente) del
    texto ya extraído del PDF Formulario de una oposición de tercero.
    Devuelve solo las claves que efectivamente matchearon — mejor un campo
    faltante que uno mal parseado. No falla si el texto no tiene el formato
    esperado (ej. viene de una VISTA de oficio de INPI en vez de una
    oposición de tercero, que no tiene este mismo formulario)."""
    resultado: dict = {}
    m = RE_OPONENTE_NOMBRE.search(texto)
    if m:
        resultado["oponente_nombre"] = m.group(1).strip()
    m = RE_OPONENTE_DOC.search(texto)
    if m:
        resultado["oponente_tipo_doc"] = m.group(1)
        resultado["oponente_numero_doc"] = m.group(2)
        resultado["oponente_cuit"] = m.group(3)
    m = RE_FUNDAMENTO.search(texto)
    if m:
        fundamento = m.group(1).strip()
        resultado["fundamento_oposicion"] = fundamento
        resultado.update(parsear_marca_oponente(fundamento, acta_propia))
    return resultado


def buscar_marca_por_denominacion(s: "requests.Session", denominacion: str, clase: str = "", timeout: int = 30) -> list:
    """Busca marcas por denominación (contiene) en la API JSON
    GrillaMarcasAvanzada. Misma función que panel/inpi_lead.py (duplicada
    a propósito, ver el comentario de ese archivo: Railway deploya solo
    panel/, no tiene acceso a scripts/).

    Ojo: esta API necesita la sesión/cookies de la página de "búsqueda
    avanzada" puntual (Cod_Funcion=NQA0ADE) — la sesión de
    /MarcasConsultas/Grilla (la que arma crear_sesion() para el resto del
    pipeline) no alcanza, devuelve 0 resultados aunque el status sea 200.
    Confirmado a mano el 29/09/2026."""
    try:
        s.get(f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE", timeout=timeout)
    except requests.RequestException:
        pass
    payload = {
        "Tipo_Resolucion": "", "Clase": str(clase or ""),
        "TipoBusquedaDenominacion": "1", "Denominacion": denominacion,
        "Titular": "", "TipoBusquedaTitular": "0",
        "Fecha_IngresoDesde": "", "Fecha_IngresoHasta": "",
        "Fecha_ResolucionDesde": "", "Fecha_ResolucionHasta": "",
        "vigentes": False, "limit": 20, "offset": 0,
    }
    try:
        r = _get_con_reintentos(
            lambda: s.post(
                f"{BASE}/MarcasConsultas/GrillaMarcasAvanzada",
                json=payload,
                headers={
                    "Referer": f"{BASE}/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=timeout,
            )
        )
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    return [
        {
            "acta": str(row.get("Acta", "")).strip(),
            "denominacion": str(row.get("Denominacion", "")).strip(),
            "clase": row.get("Clase"),
            "numero_resolucion": str(row.get("Numero_Resolucion") or "").strip(),
        }
        for row in (data.get("rows") or [])[:20]
    ]


def resolver_marca_oponente(s: "requests.Session", detalle: dict) -> dict:
    """Si parsear_marca_oponente solo pudo sacar denominación + número de
    registro (sin ACTA directa en el fundamento), intenta resolverla a una
    ACTA concreta buscando por denominación y comparando el número de
    registro — para que el panel muestre un link directo ("Ver ficha",
    igual que cuando el fundamento sí da la ACTA) en vez de un botón
    "Buscar" que la persona tiene que resolver a mano.

    Mejor esfuerzo, no modifica detalle si no hay una coincidencia clara:
      - Prueba primero la denominación tal cual, y si no matchea ningún
        número de registro, reintenta sin espacios (confirmado a mano:
        "BALI STONE" con espacio no encontró nada en INPI, pero la
        denominación puede estar cargada pegada — ver "BALISTONE") ni con
        acentos (aplica lo mismo por consistencia, aunque no se confirmó
        un caso real con acento).
      - Solo reemplaza por una ACTA cuando hay EXACTAMENTE UNA fila cuyo
        Numero_Resolucion (sin puntos) coincide con marca_oponente_numero_registro.
        Con cero o más de una coincidencia, deja denominación/número como
        estaban (la UI ofrece el botón "Buscar" para que la persona elija)."""
    denominacion = detalle.get("marca_oponente_denominacion")
    numero = detalle.get("marca_oponente_numero_registro")
    if not denominacion or not numero or detalle.get("actas_marca_oponente"):
        return detalle
    candidatos_denominacion = [denominacion, re.sub(r"\s+", "", denominacion)]
    for candidato in candidatos_denominacion:
        filas = buscar_marca_por_denominacion(s, candidato)
        coincidencias = [
            f for f in filas
            if f.get("numero_resolucion") and f["numero_resolucion"].replace(".", "") == numero
        ]
        if len(coincidencias) == 1:
            nuevo = dict(detalle)
            nuevo.pop("marca_oponente_denominacion", None)
            nuevo.pop("marca_oponente_numero_registro", None)
            nuevo["actas_marca_oponente"] = coincidencias[0]["acta"]
            return nuevo
    return detalle


def descargar_formulario_oposicion(
    s: "requests.Session", archivos: list[dict], fila_opo: dict,
    acta_propia: str | None = None, timeout: int = 30,
) -> dict:
    """Baja y parsea el PDF "Formulario" de la oposición de tercero
    representada por fila_opo (la fila devuelta por buscar_fila_oposicion).

    Ojo con la fecha: el Formulario y el "Recibo de Ingreso"/"Opo. de
    Marcas" de un mismo trámite NO siempre se cargan en Grilla Digital el
    mismo día calendario. Dos casos reales confirmados a mano (29/09/2026):
      - acta 4764327 (BALISTONE): Recibo y Formulario mismo día 15/09/2026,
        pero con Fecha (timestamp .NET) ligeramente distinta — comparar por
        string exacto los deja afuera.
      - acta 4760629 (ARGENTOS FEST): Recibo "Opo. de Marcas" el 21/09/2026,
        pero el Formulario recién se indexa al día siguiente, 22/09/2026 —
        ni siquiera coinciden en el día calendario, así que comparar solo
        por DÍA (como se había arreglado para el caso anterior) también
        fallaba acá.
    Por eso acá NO se busca un Formulario con la misma fecha exacta ni el
    mismo día: se toma, entre TODOS los "Formulario" de la grilla fechados
    en o después del día del Recibo, el más cercano (el primero
    cronológicamente) — así se ignoran los Formulario de trámites previos
    (ej. el de la solicitud original de la marca, semanas/meses antes) sin
    depender de que ambas filas caigan el mismo día.

    Solo tiene sentido para oposición de TERCERO (Indice/Referencia con
    "OPO"): una VISTA de oficio de INPI no tiene este formulario (la inicia
    el propio organismo, no un tercero que presenta un escrito), así que el
    caller no debería llamar esto para esas filas. Devuelve {} (no rompe
    nada) si no se encuentra el Formulario o falla la descarga/parseo —
    la detección principal de la oposición no depende de esto."""
    fecha_fila = fila_opo.get("Fecha") or ""
    dia_opo = _parsear_fecha_grilla(fecha_fila)
    if not dia_opo:
        return {}
    candidatos = []
    for a in archivos:
        if a.get("Indice") != "Formulario":
            continue
        dia_f = _parsear_fecha_grilla(a.get("Fecha") or "")
        if dia_f and dia_f >= dia_opo:
            candidatos.append((dia_f, a))
    if not candidatos:
        return {}
    formulario = min(candidatos, key=lambda par: par[0])[1]
    id_doc = formulario.get("id_Documento_encriptado")
    ruta = formulario.get("ruta") or ""
    if not id_doc or not ruta:
        return {}
    nombre_archivo = ruta.rsplit("/", 1)[-1]
    try:
        r_pdf = _get_con_reintentos(
            lambda: s.get(
                f"{BASE}/Home/edmsxidd",
                params={"id": id_doc, "nombre": nombre_archivo},
                headers={"Referer": f"{BASE}/Home/GrillaDigital"},
                timeout=timeout,
            )
        )
        if r_pdf.headers.get("Content-Type", "").lower() != "application/pdf":
            return {}
        import io
        import pdfplumber

        with pdfplumber.open(io.BytesIO(r_pdf.content)) as pdf:
            texto = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return {}
    texto_plano = re.sub(r"\s+", " ", texto).strip()
    detalle = parsear_formulario_oposicion(texto_plano, acta_propia)
    return resolver_marca_oponente(s, detalle)


# A veces el campo Fecha no viene como texto "DD/MM/YYYY" sino en el formato
# de fecha .NET clásico de ASP.NET AJAX, con variaciones raras vistas en la
# práctica (ej. "-Date(1790132400000)-00" en vez del clásico "/Date(...)/"):
# lo que importa es el número de milisegundos desde 1970 dentro de "Date(...)".
RE_FECHA_DOTNET = re.compile(r"Date\((-?\d+)")


def _parsear_fecha_grilla(valor: str) -> str | None:
    """Convierte el campo Fecha de un archivo de Grilla Digital (string
    "DD/MM/YYYY" o fecha .NET "Date(ms_desde_epoch)") a ISO (YYYY-MM-DD)."""
    m_dotnet = RE_FECHA_DOTNET.search(valor)
    if m_dotnet:
        try:
            import datetime as _dt

            ms = int(m_dotnet.group(1))
            return _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc).date().isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    m_ddmmyyyy = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", valor.strip())
    if not m_ddmmyyyy:
        return None  # formato inesperado: mejor None que adivinar mal
    d, m, y = m_ddmmyyyy.groups()
    return f"{y}-{m.zfill(2)}-{d.zfill(2)}"


def fecha_publicacion_de_archivos(archivos: list[dict]) -> str | None:
    """Busca la fila 'Hoja Publicacion' en Grilla Digital y devuelve su
    fecha en formato ISO (YYYY-MM-DD) para guardar en la base, o None si no
    está (todavía no se publicó, o falló la consulta)."""
    fila = next((a for a in archivos if a.get("Indice") == "Hoja Publicacion"), None)
    if not fila or not fila.get("Fecha"):
        return None
    return _parsear_fecha_grilla(fila["Fecha"])


# Sección RESOLUCIÓN de la misma página de /MarcasConsultas/Resultado (la
# que ya se pide para leer CARACTER/CUIT — no cuesta un request aparte).
# Confirmado a mano contra un caso real (acta 4534497, marca "Concedida"):
#   RESOLUCIÓN: [ Proyecto de Concesion ]
#     FEC DE PROY: 20/01/2026   NRO: 3790975   TIPO: Concedida
#     DISPOSICION: Fecha: 03/02/2026 - Numero: DI-2026-48-APN-DNM#INPI
#     VENCE: 03/02/2036 0:00:00
# VENCE ya viene calculado por INPI (concesión + 10 años) — no hace falta
# calcularlo nosotros. \w en vez de la letra acentuada cubre "RESOLUCIÓN"
# y "RESOLUCION", "DICTÁMENES" y "DICTAMENES", sin asumir cuál usa INPI.
#
# El HTML real de DISPOSICION (confirmado con la misma acta) envuelve la
# ETIQUETA "Fecha:" en su propio <span>, separado del <span class="text-
# danger"> que envuelve el valor — por eso el primer regex (que solo
# preveía UN span opcional antes del valor) nunca matcheaba y
# fecha_concesion/numero_disposicion quedaban en None pese a que TIPO/VENCE
# sí funcionaban:
#   DISPOSICION: <span> Fecha: </span> <span class="text-danger"> 03/02/2026</span>
#                <span> - Numero: </span> <span class="text-danger"> DI-2026-48-APN-DNM#INPI</span>
RE_RESOLUCION_BLOQUE = re.compile(r"RESOLUCI\wN.*?(?=DICT\wMENES|$)", re.S)
# El valor de TIPO ("Concedida", "Denegada") va en minúscula-inicial; la
# siguiente etiqueta (MOTIVO, NOTIFICACION, etc.) siempre en MAYÚSCULAS
# seguida de ":" — eso es lo que corta el valor cuando no hay <span> de
# por medio y todo viene en la misma línea/nodo de texto.
RE_TIPO_RESOLUCION = re.compile(
    r"TIPO\s*:?\s*(?:<span[^>]*>)?\s*([^<\n]+?)(?=\s+[A-ZÁÉÍÓÚÑ]{2,}\s*:|\s*<|\n|$)"
)
RE_DISPOSICION = re.compile(
    r"DISPOSICION\s*:?\s*(?:<span[^>]*>)?\s*Fecha\s*:?\s*(?:</span>)?\s*(?:<span[^>]*>)?\s*([\d/]+)"
    r"\s*(?:</span>)?\s*(?:<span[^>]*>)?\s*-\s*Numero\s*:?\s*(?:</span>)?\s*(?:<span[^>]*>)?\s*([^\s<]+)",
    re.S,
)
RE_VENCE = re.compile(r"VENCE\s*:?\s*(?:<span[^>]*>)?\s*([\d/]+)")


def _fecha_ddmmyyyy_a_iso(valor: str) -> str | None:
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", valor.strip())
    if not m:
        return None
    d, mes, anio = m.groups()
    return f"{anio}-{mes.zfill(2)}-{d.zfill(2)}"


def parsear_resolucion(texto_pagina: str) -> dict:
    """Busca la sección RESOLUCIÓN del expediente y devuelve
    {estado_tramite, fecha_concesion, numero_disposicion,
    fecha_vencimiento_marca}. Todo en None si la marca todavía está en
    trámite (no hay sección RESOLUCIÓN todavía) o el formato no matchea."""
    resultado = {
        "estado_tramite": None, "fecha_concesion": None,
        "numero_disposicion": None, "fecha_vencimiento_marca": None,
    }
    m_bloque = RE_RESOLUCION_BLOQUE.search(texto_pagina)
    if not m_bloque:
        return resultado
    bloque = m_bloque.group(0)

    m_tipo = RE_TIPO_RESOLUCION.search(bloque)
    if m_tipo:
        resultado["estado_tramite"] = re.sub(r"\s+", " ", m_tipo.group(1)).strip()

    m_disp = RE_DISPOSICION.search(bloque)
    if m_disp:
        resultado["fecha_concesion"] = _fecha_ddmmyyyy_a_iso(m_disp.group(1))
        resultado["numero_disposicion"] = m_disp.group(2).strip()

    m_vence = RE_VENCE.search(bloque)
    if m_vence:
        resultado["fecha_vencimiento_marca"] = _fecha_ddmmyyyy_a_iso(m_vence.group(1))

    return resultado


def consultar_resolucion(s: requests.Session, acta: str, timeout: int = 30) -> dict:
    """Versión liviana de revisar_acta(), pensada para revisar_estado.py:
    solo pide la página de /MarcasConsultas/Resultado y parsea la sección
    RESOLUCIÓN — no toca Grilla Digital ni descarga el PDF del Formulario,
    porque para actas ya cargadas eso ya se hizo (o no aplica). Devuelve
    {estado_tramite, fecha_concesion, numero_disposicion,
    fecha_vencimiento_marca, bloqueado} — bloqueado=True si el WAF de INPI
    frenó la consulta (para reintentar en la próxima corrida, no como error
    definitivo)."""
    resultado = {
        "estado_tramite": None, "fecha_concesion": None,
        "numero_disposicion": None, "fecha_vencimiento_marca": None,
        "bloqueado": False,
    }
    r = _get_con_reintentos(
        lambda: s.post(
            f"{BASE}/MarcasConsultas/Resultado",
            headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
            data={"acta": acta},
            timeout=timeout,
        )
    )
    if "Web Page Blocked" in r.text or "Attack ID" in r.text:
        resultado["bloqueado"] = True
        return resultado
    resultado.update(parsear_resolucion(r.text))
    return resultado


def revisar_acta(s: requests.Session, acta: str, timeout: int = 30) -> dict:
    """Devuelve {caracter, es_lead, email, email_apoderado, motivo_sin_email,
    fecha_publicacion, tuvo_oposicion, detalle_oposicion, estado_tramite,
    fecha_concesion, numero_disposicion, fecha_vencimiento_marca}. Si algo
    falla, devuelve es_lead=None para marcarlo como "no se pudo verificar"
    (en vez de asumir por defecto que es lead). motivo_sin_email queda
    vacío cuando sí hay email o cuando no aplica (tiene apoderado)."""
    resultado = {
        "caracter": None, "es_lead": None, "email": "", "email_apoderado": "",
        "motivo_sin_email": "", "fecha_publicacion": None,
        "estado_tramite": None, "fecha_concesion": None,
        "numero_disposicion": None, "fecha_vencimiento_marca": None,
    }
    try:
        r = _get_con_reintentos(
            lambda: s.post(
                f"{BASE}/MarcasConsultas/Resultado",
                headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
                data={"acta": acta},
                timeout=timeout,
            )
        )
        if "Web Page Blocked" in r.text or "Attack ID" in r.text:
            print(f"  acta {acta}: bloqueado por el WAF de INPI (no se pudo verificar)", file=sys.stderr)
            resultado["motivo_sin_email"] = "bloqueado por el WAF de INPI al consultar el expediente"
            return resultado

        m_gestion = RE_GESTION.search(r.text)
        bloque_gestion = m_gestion.group(1) if m_gestion else r.text  # fallback: buscar en toda la página

        m_caracter = RE_CARACTER_SPAN.search(bloque_gestion)
        if m_caracter:
            caracter = re.sub(r"\s+", " ", m_caracter.group(1)).strip()
        else:
            caracter = ""

        resultado["caracter"] = caracter
        resultado["es_lead"] = caracter == ""

        m_cuit = RE_CUIT_SPAN.search(r.text)
        if m_cuit:
            cuit_encontrado = re.sub(r"[^\d]", "", m_cuit.group(1))
            if len(cuit_encontrado) in (10, 11):
                resultado["cuit"] = cuit_encontrado
        # si no matchea o no parece un CUIT válido, no seteamos la clave: así
        # row.update(info) no pisa un cuit que ya venía de completar_mixtas.py

        # RESOLUCIÓN está en esta misma página, para leads y no-leads por
        # igual — se guarda siempre, sin costo de un request extra.
        resultado.update(parsear_resolucion(r.text))

        if not resultado["es_lead"]:
            return resultado  # ya tiene apoderado/gestor, no hace falta el email

        # GRILLA DIGITAL -> listar archivos -> descargar "Formulario" -> leer EMAIL
        # (misma llamada nos sirve para sacar fecha_publicacion y detectar si
        # ya hay una oposición/vista cargada — normalmente no, a esta altura
        # recién se está cargando el boletín, pero no cuesta nada revisarlo).
        archivos = buscar_archivos_grilla(s, acta, timeout=timeout)
        if not archivos:
            resultado["motivo_sin_email"] = "bloqueado por el WAF de INPI al abrir Grilla Digital"
            return resultado

        resultado["fecha_publicacion"] = fecha_publicacion_de_archivos(archivos)
        resultado["tuvo_oposicion"], resultado["detalle_oposicion"] = detectar_oposicion(
            archivos, resultado["fecha_publicacion"]
        )

        formulario = next((a for a in archivos if a.get("Indice") == "Formulario"), None)
        if not formulario:
            resultado["motivo_sin_email"] = "el expediente no tiene un archivo Formulario en Grilla Digital"
            return resultado

        id_doc = formulario["id_Documento_encriptado"]
        nombre_archivo = formulario["ruta"].rsplit("/", 1)[-1]
        r_pdf = _get_con_reintentos(
            lambda: s.get(
                f"{BASE}/Home/edmsxidd",
                params={"id": id_doc, "nombre": nombre_archivo},
                headers={"Referer": f"{BASE}/Home/GrillaDigital"},
                timeout=timeout,
            )
        )
        if r_pdf.headers.get("Content-Type", "").lower() != "application/pdf":
            resultado["motivo_sin_email"] = "no se pudo descargar el PDF del Formulario"
            return resultado

        import io
        import pdfplumber

        with pdfplumber.open(io.BytesIO(r_pdf.content)) as pdf:
            texto = "\n".join(p.extract_text() or "" for p in pdf.pages)

        m_email = re.search(r"EMAIL:\s*([\w.+-]+@[\w-]+\.[\w.-]+)", texto)
        if m_email:
            resultado["email"] = m_email.group(1)
        else:
            resultado["motivo_sin_email"] = "el Formulario no tiene el campo EMAIL completo"

        m_email_apoderado = re.search(
            r"REPRESENTACION.*?EMAIL:\s*([\w.+-]+@[\w-]+\.[\w.-]+)", texto, re.S
        )
        if m_email_apoderado:
            resultado["email_apoderado"] = m_email_apoderado.group(1)

    except Exception as e:
        print(f"  acta {acta}: error ({e})", file=sys.stderr)
        resultado["caracter"] = None
        resultado["es_lead"] = None
        resultado["motivo_sin_email"] = f"error de conexión al consultar INPI: {e}"
    return resultado


def calcular_lead_score(row: dict) -> int:
    """Puntaje simple: suma puntos si no tiene apoderado/matrícula, resta si sí.
    Si no se pudo verificar (es_lead is None), no se suma ni resta nada por
    ese concepto: mejor subestimar el score que arriesgar un falso positivo."""
    score = 0
    matricula = (row.get("matricula_agente") or "").strip()
    caracter = row.get("caracter")
    es_lead = row.get("es_lead")
    if matricula == "" or matricula == "Part.":
        score += 50
    if es_lead is True:
        score += 50
    elif es_lead is False:
        score -= 100
    # es_lead is None (no verificado): no se suma ni resta
    if row.get("email"):
        score += 20
    return score


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--limit", type=int, default=None,
        help="revisar como máximo N actas candidatas (matrícula vacía o 'Part.'); útil para pruebas",
    )
    ap.add_argument("--delay", type=float, default=1.5, help="segundos de espera entre actas (freno de mano)")
    args = ap.parse_args()

    with open(args.in_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    candidatas = [r for r in rows if (r.get("matricula_agente") or "").strip() in ("", "Part.")]
    if args.limit:
        candidatas = candidatas[: args.limit]
    print(f"Actas candidatas a revisar (sin matrícula o 'Part.'): {len(candidatas)}")

    s = crear_sesion()
    for i, row in enumerate(candidatas, 1):
        info = revisar_acta(s, row["acta"])
        row.update(info)
        row["lead_score"] = calcular_lead_score(row)
        print(f"  [{i}/{len(candidatas)}] acta {row['acta']}: caracter={info['caracter']!r} "
              f"es_lead={info['es_lead']} email={'sí' if info['email'] else 'no'}")
        time.sleep(args.delay)  # freno de mano, no golpear el portal

    for row in rows:
        row.setdefault("caracter", "")
        row.setdefault("es_lead", "")
        row.setdefault("email", "")
        row.setdefault("email_apoderado", "")
        row.setdefault("motivo_sin_email", "")
        row.setdefault("fecha_publicacion", "")
        row.setdefault("tuvo_oposicion", "")
        row.setdefault("detalle_oposicion", "")
        row.setdefault("estado_tramite", "")
        row.setdefault("fecha_concesion", "")
        row.setdefault("numero_disposicion", "")
        row.setdefault("fecha_vencimiento_marca", "")
        row.setdefault("lead_score", row.get("lead_score", 0))

    fieldnames = list(rows[0].keys())
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    leads = sum(1 for r in rows if r.get("es_lead") is True or r.get("es_lead") == "True")
    no_verificados = sum(1 for r in rows if r.get("es_lead") is None)
    print(f"\nLeads confirmados: {leads}. No verificados (bloqueo/error): {no_verificados}. Guardado: {args.out}")


if __name__ == "__main__":
    main()
