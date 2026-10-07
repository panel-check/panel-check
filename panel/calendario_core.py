"""
Calendario del panel sincronizado con Google Calendar.

Qué hace:
  - Lee los eventos de UN calendario de Google (el del estudio) y los copia a la
    tabla `calendario_eventos`, para que la sección /calendario del panel los
    muestre sin consultar a Google en cada visita.
  - Lo que se agenda desde el panel (ficha del lead, sección Calendario) se crea
    primero en Google y después se copia acá: se ve en los dos lados.
  - Lo que se agenda directamente en Google Calendar aparece en el panel en un
    par de minutos (sondeo incremental con syncToken: Google solo devuelve lo
    que cambió desde la última vez).
  - Si en el título o la nota del evento figura un número de acta ("Acta
    4797001", "Actas 4797001 y 4797002", "#4797001" o incluso el número suelto
    si existe en la base), el evento queda vinculado al titular o cliente dueño
    de esa marca. El vínculo NO se guarda: se calcula al leer, así que si la
    marca se carga después, el evento se vincula solo.

Acceso a Google: cuenta de servicio (sin pantalla de login, sin tokens que se
venzan). Variables de entorno (Railway):
    GOOGLE_SERVICE_ACCOUNT_JSON  el contenido COMPLETO del .json de la cuenta
                                 de servicio.
    GOOGLE_CALENDAR_ID           el ID del calendario (para el calendario
                                 principal de una cuenta Gmail es el mismo mail).
El calendario tiene que estar compartido con el mail de la cuenta de servicio
(client_email del JSON) con permiso «Hacer cambios en eventos».

Sin FastAPI: lo usan el panel (calendario_api.py) y las pruebas.
"""

import datetime as _dt
import html as _html
import json
import os
import re
import threading
import time
from zoneinfo import ZoneInfo

import psycopg2.extras

TZ_NOMBRE = "America/Argentina/Buenos_Aires"
TZ = ZoneInfo(TZ_NOMBRE)

API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar"

# Primera sincronización (y la completa semanal): ventana de eventos que se copian.
DIAS_ATRAS = 90
DIAS_ADELANTE = 400
# Cada tanto se rehace la sincronización completa para que la ventana acompañe al día de hoy.
RESINCRONIZAR_CADA_DIAS = 7
SONDEO_SEGUNDOS = 120

# Candado de Postgres: solo una corrida de sincronización a la vez (aunque haya
# más de una instancia del panel o se apriete «Sincronizar ahora» en simultáneo).
CLAVE_CANDADO = 7424001


class CalendarioError(Exception):
    """Error con un mensaje pensado para mostrarle a una persona."""


class GoogleError(CalendarioError):
    def __init__(self, status, mensaje):
        super().__init__(mensaje)
        self.status = status


# ── Configuración y acceso a Google ─────────────────────────────────────

def _info_cuenta():
    crudo = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not (crudo or "").strip():
        return None
    try:
        # strict=False: tolera saltos de línea sueltos dentro de la clave privada
        # (pasa cuando el JSON se pega en una variable y el editor los "desescapa").
        info = json.loads(crudo, strict=False)
    except ValueError:
        raise CalendarioError("GOOGLE_SERVICE_ACCOUNT_JSON no es un JSON válido: pegá el contenido completo del archivo, de { a }.")
    if not isinstance(info, dict) or not info.get("client_email") or not info.get("private_key"):
        raise CalendarioError("GOOGLE_SERVICE_ACCOUNT_JSON no parece el archivo de una cuenta de servicio (falta client_email o private_key).")
    return info


def calendar_id():
    return (os.environ.get("GOOGLE_CALENDAR_ID") or "").strip() or None


def configurado() -> bool:
    return bool((os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip() and calendar_id())


def cuenta_servicio():
    """Mail de la cuenta de servicio (con quien hay que compartir el calendario)."""
    try:
        info = _info_cuenta()
    except CalendarioError:
        return None
    return info and info.get("client_email")


_sesion_lock = threading.Lock()
_sesion_cache = {"clave": None, "sesion": None}


def _sesion():
    info = _info_cuenta()
    if not info or not calendar_id():
        raise CalendarioError("Falta configurar GOOGLE_SERVICE_ACCOUNT_JSON y GOOGLE_CALENDAR_ID en Railway.")
    clave = (info["client_email"], info.get("private_key_id"))
    with _sesion_lock:
        if _sesion_cache["clave"] != clave:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
            _sesion_cache["sesion"] = AuthorizedSession(creds)
            _sesion_cache["clave"] = clave
        return _sesion_cache["sesion"]


def _mensaje_google(status, cuerpo):
    detalle = ""
    try:
        detalle = (cuerpo.get("error") or {}).get("message") or ""
    except Exception:
        pass
    if status == 404:
        return ("Google no encuentra el calendario: revisá GOOGLE_CALENDAR_ID y que el calendario esté compartido con "
                f"{cuenta_servicio() or 'la cuenta de servicio'} (permiso «Hacer cambios en eventos»).")
    if status == 403:
        return ("Google rechazó el acceso: puede que la Calendar API no esté activada en el proyecto de Google Cloud, "
                f"o que el calendario no esté compartido con {cuenta_servicio() or 'la cuenta de servicio'} con permiso de edición. {detalle}").strip()
    if status == 401:
        return f"Google no aceptó la cuenta de servicio (clave inválida o revocada). {detalle}".strip()
    return f"Google respondió {status}. {detalle}".strip()


def _pedir(metodo, ruta, **kw):
    """Pedido a la API de Google Calendar; devuelve el JSON (o None si no hay cuerpo)."""
    sesion = _sesion()
    try:
        r = sesion.request(metodo, API + ruta, timeout=30, **kw)
    except CalendarioError:
        raise
    except Exception as e:  # red caída, clave mal formada, etc.
        raise CalendarioError(f"No se pudo hablar con Google: {e}")
    if r.status_code >= 400:
        try:
            cuerpo = r.json()
        except Exception:
            cuerpo = {}
        raise GoogleError(r.status_code, _mensaje_google(r.status_code, cuerpo))
    if not r.content:
        return None
    return r.json()


def _ruta_eventos(extra=""):
    from urllib.parse import quote

    return f"/calendars/{quote(calendar_id(), safe='')}/events{extra}"


# ── Tablas ─────────────────────────────────────────────────────────────

def crear_tablas(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS calendario_eventos (
            id                  BIGSERIAL PRIMARY KEY,
            calendar_id         TEXT NOT NULL,
            google_id           TEXT NOT NULL,
            titulo              TEXT NOT NULL DEFAULT '',
            descripcion         TEXT,
            lugar               TEXT,
            inicio              TIMESTAMPTZ NOT NULL,
            fin                 TIMESTAMPTZ NOT NULL,
            todo_el_dia         BOOLEAN NOT NULL DEFAULT false,
            actas               TEXT[] NOT NULL DEFAULT '{}',
            origen              TEXT NOT NULL DEFAULT 'google',
            creado_por          TEXT,
            link                TEXT,
            etag                TEXT,
            actualizado_google  TIMESTAMPTZ,
            sincronizado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (calendar_id, google_id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_calendario_eventos_inicio ON calendario_eventos(inicio)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_calendario_eventos_actas ON calendario_eventos USING GIN (actas)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS calendario_estado (
            id                 SMALLINT PRIMARY KEY DEFAULT 1,
            calendar_id        TEXT,
            sync_token         TEXT,
            ultimo_intento_en  TIMESTAMPTZ,
            ultimo_ok_en       TIMESTAMPTZ,
            ultima_completa_en TIMESTAMPTZ,
            ultimo_error       TEXT,
            ultimo_cambios     INTEGER,
            CONSTRAINT calendario_estado_una_fila CHECK (id = 1)
        )
        """
    )


# ── Números de acta en el texto del evento ─────────────────────────────

_NUM_ACTA = r"(?<!\d)\d{4,9}(?!\d)"
_RE_PALABRA_ACTA = re.compile(
    r"\bactas?\b(?:\s*(?:n[°ºo]\.?|nro\.?|n[úu]m(?:ero|\.)?))?\s*[:#\-–]?\s*",
    re.IGNORECASE,
)
_RE_SEPARADOR = re.compile(r"\s*(?:,|;|/|&|\by\b|\be\b|\bo\b|\+)\s*", re.IGNORECASE)
_RE_NUMERAL = re.compile(r"(?<![\w/&])#\s?(" + _NUM_ACTA + ")")
_RE_NUMERO_SUELTO = re.compile(r"(?<![\d.,/-])(\d{6,8})(?![\d]|[.,]\d)")


def extraer_actas(texto: str) -> list:
    """Actas que el texto nombra explícitamente: «Acta 4797001», «Actas 4797001 y
    4797002», «Acta N° 4797001», «#4797001». Sin repetir y en orden de aparición.
    No mira la base: un número que no es de ninguna marca igual se devuelve."""
    texto = texto or ""
    encontradas = []

    def sumar(n):
        if n not in encontradas:
            encontradas.append(n)

    for palabra in _RE_PALABRA_ACTA.finditer(texto):
        pos = palabra.end()
        while True:
            m = re.compile(_NUM_ACTA).match(texto, pos)
            if not m:
                break
            sumar(m.group(0))
            sep = _RE_SEPARADOR.match(texto, m.end())
            if not sep:
                break
            pos = sep.end()
    for m in _RE_NUMERAL.finditer(texto):
        sumar(m.group(1))
    return encontradas


def numeros_sueltos(texto: str) -> list:
    """Números de 6 a 8 cifras sin la palabra «acta» (candidatos a validar contra la base:
    así alcanza con pegar el número solo, sin confundir un teléfono o un monto)."""
    return list(dict.fromkeys(m.group(1) for m in _RE_NUMERO_SUELTO.finditer(texto or "")))


def actas_que_existen(cur, numeros) -> set:
    numeros = [n for n in dict.fromkeys(numeros) if n]
    if not numeros:
        return set()
    cur.execute(
        """
        SELECT acta FROM marcas WHERE acta = ANY(%(n)s)
        UNION SELECT acta FROM cartera_marcas WHERE acta = ANY(%(n)s)
        UNION SELECT acta FROM solicitudes_escaneadas WHERE acta = ANY(%(n)s)
        """,
        {"n": numeros},
    )
    return {f[0] if not isinstance(f, dict) else f["acta"] for f in cur.fetchall()}


def actas_del_evento(titulo, descripcion, privadas, existentes: set) -> list:
    """Actas de un evento: las nombradas con la palabra «acta» o «#» + las que el panel
    guardó al crearlo + números sueltos que son actas reales de la base."""
    texto = f"{titulo or ''}\n{descripcion or ''}"
    actas = list(extraer_actas(texto))
    for n in (privadas or "").split(","):
        n = n.strip()
        if re.fullmatch(r"\d{4,9}", n) and n not in actas:
            actas.append(n)
    for n in numeros_sueltos(texto):
        if n in existentes and n not in actas:
            actas.append(n)
    return actas


# ── Eventos de Google → filas ──────────────────────────────────────────

def texto_plano(s: str) -> str:
    """La nota de un evento de Google puede traer HTML (<br>, <a>...): se guarda como texto."""
    if not s:
        return ""
    s = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", s)
    s = re.sub(r"(?i)</\s*(p|div|li|ul|ol)\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _fecha_hora(valor: dict):
    """{'dateTime': ...} o {'date': ...} de Google → (datetime con zona, es_todo_el_dia)."""
    if valor.get("dateTime"):
        s = valor["dateTime"].replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=TZ)
        return d, False
    d = _dt.date.fromisoformat(valor["date"])
    return _dt.datetime.combine(d, _dt.time(0, 0), tzinfo=TZ), True


def evento_a_fila(ev: dict, calendar: str) -> dict:
    """Un evento de la API de Google → los campos de calendario_eventos (sin actas validadas)."""
    inicio, todo = _fecha_hora(ev.get("start") or {})
    fin, _ = _fecha_hora(ev.get("end") or ev.get("start") or {})
    if fin <= inicio:
        fin = inicio + (_dt.timedelta(days=1) if todo else _dt.timedelta(minutes=30))
    privadas = ((ev.get("extendedProperties") or {}).get("private")) or {}
    actualizado = None
    if ev.get("updated"):
        try:
            actualizado = _dt.datetime.fromisoformat(ev["updated"].replace("Z", "+00:00"))
        except ValueError:
            pass
    return {
        "calendar_id": calendar,
        "google_id": ev["id"],
        "titulo": (ev.get("summary") or "").strip() or "(sin título)",
        "descripcion": texto_plano(ev.get("description") or ""),
        "lugar": (ev.get("location") or "").strip() or None,
        "inicio": inicio,
        "fin": fin,
        "todo_el_dia": todo,
        "privadas_actas": privadas.get("actas") or "",
        "origen": "panel" if privadas.get("panel") == "1" else "google",
        "creado_por": privadas.get("usuario") or ((ev.get("creator") or {}).get("email")),
        "link": ev.get("htmlLink"),
        "etag": ev.get("etag"),
        "actualizado_google": actualizado,
    }


def _guardar_eventos(cur, filas: list) -> None:
    """Upsert de una tanda de filas (calcula las actas, validando los números sueltos de una vez)."""
    if not filas:
        return
    candidatos = []
    for f in filas:
        candidatos += numeros_sueltos(f"{f['titulo']}\n{f['descripcion']}")
    existentes = actas_que_existen(cur, candidatos)
    for f in filas:
        f["actas"] = actas_del_evento(f["titulo"], f["descripcion"], f.pop("privadas_actas"), existentes)
    psycopg2.extras.execute_batch(
        cur,
        """
        INSERT INTO calendario_eventos
            (calendar_id, google_id, titulo, descripcion, lugar, inicio, fin, todo_el_dia, actas,
             origen, creado_por, link, etag, actualizado_google, sincronizado_en)
        VALUES
            (%(calendar_id)s, %(google_id)s, %(titulo)s, %(descripcion)s, %(lugar)s, %(inicio)s, %(fin)s,
             %(todo_el_dia)s, %(actas)s, %(origen)s, %(creado_por)s, %(link)s, %(etag)s, %(actualizado_google)s, now())
        ON CONFLICT (calendar_id, google_id) DO UPDATE SET
            titulo = EXCLUDED.titulo, descripcion = EXCLUDED.descripcion, lugar = EXCLUDED.lugar,
            inicio = EXCLUDED.inicio, fin = EXCLUDED.fin, todo_el_dia = EXCLUDED.todo_el_dia,
            actas = EXCLUDED.actas, origen = EXCLUDED.origen, creado_por = EXCLUDED.creado_por,
            link = EXCLUDED.link, etag = EXCLUDED.etag,
            actualizado_google = EXCLUDED.actualizado_google, sincronizado_en = now()
        """,
        filas,
    )


# ── Sincronización Google → panel ──────────────────────────────────────

def _leer_estado(cur):
    cur.execute("INSERT INTO calendario_estado (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
    cur.execute("SELECT * FROM calendario_estado WHERE id = 1")
    return cur.fetchone()


def sincronizar(conexion, completa: bool = False) -> dict:
    """Trae de Google lo que cambió y lo copia a la base. Devuelve un resumen.

    `completa=True` ignora el syncToken y vuelve a leer toda la ventana de fechas
    (y borra del panel lo que ya no existe en Google). Si otra corrida está en
    marcha, devuelve {"omitida": True} sin hacer nada."""
    if not configurado():
        raise CalendarioError("El calendario todavía no está configurado (faltan variables en Railway).")
    cal = calendar_id()
    ahora = _dt.datetime.now(_dt.timezone.utc)

    with conexion() as conn:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (CLAVE_CANDADO,))
            if not cur.fetchone()["ok"]:
                conn.rollback()
                return {"omitida": True}
            try:
                return _sincronizar_con_candado(conn, cur, cal, ahora, completa)
            finally:
                try:
                    conn.rollback()
                    cur.execute("SELECT pg_advisory_unlock(%s)", (CLAVE_CANDADO,))
                    conn.commit()
                except Exception:
                    pass


def _sincronizar_con_candado(conn, cur, cal, ahora, completa) -> dict:
    estado = _leer_estado(cur)
    conn.commit()
    token = estado["sync_token"]
    if estado["calendar_id"] != cal:
        token = None  # cambió de calendario: lo anterior no sirve
    ultima_completa = estado["ultima_completa_en"]
    if not completa and (not ultima_completa or ahora - ultima_completa > _dt.timedelta(days=RESINCRONIZAR_CADA_DIAS)):
        token = None
    if completa:
        token = None

    cur.execute("UPDATE calendario_estado SET ultimo_intento_en = now() WHERE id = 1")
    conn.commit()

    cambios = 0
    try:
        for intento in (1, 2):
            vistos = set()
            cambios = 0
            es_completa = token is None
            params = {"singleEvents": "true", "showDeleted": "true", "maxResults": 250}
            if token:
                params["syncToken"] = token
            else:
                params["timeMin"] = (ahora - _dt.timedelta(days=DIAS_ATRAS)).isoformat()
                params["timeMax"] = (ahora + _dt.timedelta(days=DIAS_ADELANTE)).isoformat()
            siguiente_token = None
            try:
                while True:
                    resp = _pedir("GET", _ruta_eventos(), params=params) or {}
                    filas, borrar = [], []
                    for ev in resp.get("items", []):
                        if ev.get("status") == "cancelled":
                            borrar.append(ev["id"])
                            continue
                        if not (ev.get("start") or {}):
                            continue
                        vistos.add(ev["id"])
                        filas.append(evento_a_fila(ev, cal))
                    _guardar_eventos(cur, filas)
                    if borrar:
                        cur.execute("DELETE FROM calendario_eventos WHERE calendar_id = %s AND google_id = ANY(%s)", (cal, borrar))
                    cambios += len(filas) + len(borrar)
                    conn.commit()
                    if resp.get("nextPageToken"):
                        params["pageToken"] = resp["nextPageToken"]
                        continue
                    siguiente_token = resp.get("nextSyncToken")
                    break
            except GoogleError as e:
                if e.status == 410 and token and intento == 1:
                    token = None  # el syncToken venció: se rehace completa
                    conn.rollback()
                    continue
                raise
            if es_completa:
                # Lo que está en el panel y ya no vino de Google se borró allá.
                if vistos:
                    cur.execute(
                        "DELETE FROM calendario_eventos WHERE calendar_id = %s AND NOT (google_id = ANY(%s))",
                        (cal, list(vistos)),
                    )
                else:
                    cur.execute("DELETE FROM calendario_eventos WHERE calendar_id = %s", (cal,))
            cur.execute(
                """
                UPDATE calendario_estado SET calendar_id = %s, sync_token = %s, ultimo_ok_en = now(),
                       ultimo_error = NULL, ultimo_cambios = %s,
                       ultima_completa_en = CASE WHEN %s THEN now() ELSE ultima_completa_en END
                WHERE id = 1
                """,
                (cal, siguiente_token, cambios, es_completa),
            )
            conn.commit()
            return {"omitida": False, "cambios": cambios, "completa": es_completa}
    except Exception as e:
        conn.rollback()
        try:
            cur.execute("UPDATE calendario_estado SET ultimo_error = %s WHERE id = 1", (str(e)[:500],))
            conn.commit()
        except Exception:
            conn.rollback()
        raise


def iniciar_sondeo(conexion, cada: int = SONDEO_SEGUNDOS):
    """Arranca (una vez) el hilo que sincroniza cada `cada` segundos mientras el panel esté
    prendido. Si el calendario no está configurado no hace nada."""
    if not configurado():
        print("[calendario] sin GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_CALENDAR_ID: sincronización desactivada")
        return None

    def bucle():
        time.sleep(15)  # que el panel termine de arrancar
        while True:
            try:
                r = sincronizar(conexion)
                if r.get("cambios"):
                    print(f"[calendario] {r['cambios']} cambio(s) traídos de Google{' (completa)' if r.get('completa') else ''}")
            except Exception as e:
                print(f"[calendario] la sincronización falló: {e}")
            time.sleep(cada)

    hilo = threading.Thread(target=bucle, name="sondeo-calendario", daemon=True)
    hilo.start()
    return hilo


# ── Panel → Google ─────────────────────────────────────────────────────

def _inicio_fin_google(datos: dict):
    """Arma start/end de Google a partir de fecha (YYYY-MM-DD) + hora + duración.
    Devuelve (start, end, inicio_datetime). Sin hora = evento de todo el día."""
    try:
        fecha = _dt.date.fromisoformat(datos["fecha"])
    except (KeyError, ValueError, TypeError):
        raise CalendarioError("Falta la fecha del evento (formato AAAA-MM-DD).")
    if datos.get("todo_el_dia") or not datos.get("hora"):
        dias = 1
        if datos.get("fecha_fin"):
            try:
                ultimo = _dt.date.fromisoformat(datos["fecha_fin"])
            except ValueError:
                raise CalendarioError("La fecha de fin no es válida.")
            if ultimo < fecha:
                raise CalendarioError("La fecha de fin es anterior a la de inicio.")
            dias = (ultimo - fecha).days + 1
        fin = fecha + _dt.timedelta(days=dias)
        return ({"date": fecha.isoformat()}, {"date": fin.isoformat()},
                _dt.datetime.combine(fecha, _dt.time(0, 0), tzinfo=TZ))
    try:
        h, m = [int(x) for x in str(datos["hora"]).split(":")[:2]]
        inicio = _dt.datetime.combine(fecha, _dt.time(h, m), tzinfo=TZ)
    except (ValueError, TypeError):
        raise CalendarioError("La hora no es válida (formato HH:MM).")
    minutos = int(datos.get("duracion_min") or 60)
    if not 5 <= minutos <= 24 * 60:
        raise CalendarioError("La duración tiene que estar entre 5 minutos y 24 horas.")
    fin = inicio + _dt.timedelta(minutes=minutos)
    return ({"dateTime": inicio.isoformat(), "timeZone": TZ_NOMBRE},
            {"dateTime": fin.isoformat(), "timeZone": TZ_NOMBRE}, inicio)


def _normalizar_actas(actas) -> list:
    out = []
    for a in actas or []:
        a = str(a).strip()
        if not re.fullmatch(r"\d{4,9}", a):
            raise CalendarioError(f"«{a}» no es un número de acta válido (4 a 9 cifras).")
        if a not in out:
            out.append(a)
    return out


def _nota_con_actas(descripcion: str, actas: list) -> str:
    """La nota que se manda a Google: el texto + una línea «Acta N» por si falta, para que
    el número se vea en Google Calendar y el vínculo sobreviva si alguien edita el evento allá."""
    descripcion = (descripcion or "").strip()
    faltan = [a for a in actas if a not in extraer_actas(descripcion)]
    if faltan:
        etiqueta = "Acta" if len(faltan) == 1 else "Actas"
        descripcion = (descripcion + "\n\n" if descripcion else "") + f"{etiqueta} {', '.join(faltan)}"
    return descripcion


def _cuerpo_evento(datos: dict, usuario: str):
    titulo = " ".join((datos.get("titulo") or "").split())
    if not titulo:
        raise CalendarioError("Poné un título para el evento.")
    if len(titulo) > 200:
        raise CalendarioError("El título es demasiado largo (máximo 200 letras).")
    actas = _normalizar_actas(datos.get("actas"))
    start, end, _ = _inicio_fin_google(datos)
    return {
        "summary": titulo,
        "description": _nota_con_actas(datos.get("descripcion"), actas),
        "location": (datos.get("lugar") or "").strip(),
        "start": start,
        "end": end,
        "extendedProperties": {"private": {"panel": "1", "usuario": usuario or "", "actas": ",".join(actas)}},
    }


def _guardar_resultado(conexion, recurso: dict) -> int:
    cal = calendar_id()
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _guardar_eventos(cur, [evento_a_fila(recurso, cal)])
            cur.execute("SELECT id FROM calendario_eventos WHERE calendar_id = %s AND google_id = %s", (cal, recurso["id"]))
            fila = cur.fetchone()
        conn.commit()
    return fila["id"]


def crear_evento(conexion, datos: dict, usuario: str) -> int:
    """Crea el evento en Google y lo copia al panel. Devuelve el id local."""
    recurso = _pedir("POST", _ruta_eventos(), json=_cuerpo_evento(datos, usuario))
    return _guardar_resultado(conexion, recurso)


def _google_id_de(conexion, evento_id: int) -> str:
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT google_id FROM calendario_eventos WHERE id = %s AND calendar_id = %s", (evento_id, calendar_id()))
            fila = cur.fetchone()
    if not fila:
        raise CalendarioError("Ese evento ya no existe (puede que lo hayan borrado desde Google Calendar).")
    return fila[0]


def editar_evento(conexion, evento_id: int, datos: dict, usuario: str) -> int:
    google_id = _google_id_de(conexion, evento_id)
    cuerpo = _cuerpo_evento(datos, usuario)
    recurso = _pedir("PATCH", _ruta_eventos("/" + google_id), json=cuerpo)
    return _guardar_resultado(conexion, recurso)


def borrar_evento(conexion, evento_id: int) -> None:
    google_id = _google_id_de(conexion, evento_id)
    try:
        _pedir("DELETE", _ruta_eventos("/" + google_id))
    except GoogleError as e:
        if e.status not in (404, 410):  # ya estaba borrado en Google: igual se limpia acá
            raise
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM calendario_eventos WHERE id = %s", (evento_id,))
        conn.commit()


# ── Lectura para el panel ──────────────────────────────────────────────

def _hora_local(d: _dt.datetime) -> _dt.datetime:
    return d.astimezone(TZ)


def evento_a_json(f: dict) -> dict:
    ini, fin = _hora_local(f["inicio"]), _hora_local(f["fin"])
    if f["todo_el_dia"]:
        ultimo = (fin - _dt.timedelta(days=1)).date()  # el fin de Google es exclusivo
    else:
        ultimo = fin.date()
        if fin.time() == _dt.time(0, 0) and fin > ini:
            ultimo = (fin - _dt.timedelta(days=1)).date()
    return {
        "id": f["id"],
        "titulo": f["titulo"],
        "descripcion": f["descripcion"] or "",
        "lugar": f["lugar"] or "",
        "todo_el_dia": f["todo_el_dia"],
        "fecha": ini.date().isoformat(),
        "fecha_fin": max(ultimo, ini.date()).isoformat(),
        "hora": None if f["todo_el_dia"] else ini.strftime("%H:%M"),
        "hora_fin": None if f["todo_el_dia"] else fin.strftime("%H:%M"),
        "actas": list(f["actas"] or []),
        "origen": f["origen"],
        "creado_por": f["creado_por"],
        "link": f["link"],
    }


def resolver_actas(cur, actas) -> dict:
    """Para cada acta: de quién es. {acta: {acta, existe, denominacion, titular, clave,
    cliente_id, cliente, tipo}}. `tipo`: «cliente» (está en la cartera o su titular es un cliente
    del estudio), «lead» (marca de un titular que todavía no es cliente), «tercero» (solicitud
    que vio el escaneo pero tiene agente: no es lead ni cliente) o «desconocida» (no está en la base)."""
    actas = [a for a in dict.fromkeys(actas or []) if a]
    res = {a: {"acta": a, "existe": False, "denominacion": None, "titular": None, "clave": None,
               "cliente_id": None, "cliente": None, "tipo": "desconocida"} for a in actas}
    if not actas:
        return res

    cur.execute(
        """
        SELECT m.acta, COALESCE(NULLIF(m.denominacion_inpi, ''), NULLIF(m.denominacion, '')) AS denominacion,
               m.titular, cc.clave
        FROM marcas m LEFT JOIN crm_claves cc ON cc.acta = m.acta
        WHERE m.acta = ANY(%s)
        """,
        (actas,),
    )
    for f in cur.fetchall():
        r = res[f["acta"]]
        r.update(existe=True, denominacion=f["denominacion"], titular=f["titular"], clave=f["clave"], tipo="lead")

    cur.execute(
        """
        SELECT s.acta, s.denominacion, s.titular FROM solicitudes_escaneadas s
        WHERE s.acta = ANY(%s)
        """,
        (actas,),
    )
    for f in cur.fetchall():
        r = res[f["acta"]]
        if not r["existe"]:
            r.update(existe=True, denominacion=f["denominacion"], titular=f["titular"], tipo="tercero")

    cur.execute(
        """
        SELECT cm.acta, cm.denominacion, cm.titular, c.id AS cliente_id, c.nombre
        FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id
        WHERE cm.acta = ANY(%s)
        """,
        (actas,),
    )
    for f in cur.fetchall():
        r = res[f["acta"]]
        r.update(existe=True, cliente_id=f["cliente_id"], cliente=f["nombre"], tipo="cliente")
        r["denominacion"] = r["denominacion"] or f["denominacion"]
        r["titular"] = r["titular"] or f["titular"]

    # Titular de un lead que ya se convirtió en cliente del estudio.
    claves = list({r["clave"] for r in res.values() if r["clave"] and r["tipo"] == "lead"})
    if claves:
        cur.execute("SELECT id, nombre, clave_crm FROM clientes WHERE clave_crm = ANY(%s)", (claves,))
        por_clave = {f["clave_crm"]: f for f in cur.fetchall()}
        for r in res.values():
            c = por_clave.get(r["clave"]) if r["tipo"] == "lead" else None
            if c:
                r.update(cliente_id=c["id"], cliente=c["nombre"], tipo="cliente")
    return res


def listar_eventos(cur, desde: _dt.date, hasta: _dt.date) -> list:
    """Eventos que tocan el rango [desde, hasta] (fechas inclusive, hora Argentina), con sus
    vínculos resueltos."""
    d0 = _dt.datetime.combine(desde, _dt.time(0, 0), tzinfo=TZ)
    d1 = _dt.datetime.combine(hasta + _dt.timedelta(days=1), _dt.time(0, 0), tzinfo=TZ)
    cur.execute(
        """
        SELECT * FROM calendario_eventos
        WHERE calendar_id = %s AND inicio < %s AND fin > %s
        ORDER BY inicio, id
        """,
        (calendar_id(), d1, d0),
    )
    return _con_vinculos(cur, [evento_a_json(f) for f in cur.fetchall()])


def eventos_de_actas(cur, actas, dias_atras: int = 60) -> list:
    """Eventos (futuros y de los últimos `dias_atras`) que nombran alguna de estas actas."""
    actas = [a for a in dict.fromkeys(actas or []) if a]
    if not actas:
        return []
    desde = _dt.datetime.now(TZ) - _dt.timedelta(days=dias_atras)
    cur.execute(
        """
        SELECT * FROM calendario_eventos
        WHERE calendar_id = %s AND actas && %s AND fin > %s
        ORDER BY inicio, id
        """,
        (calendar_id(), actas, desde),
    )
    return _con_vinculos(cur, [evento_a_json(f) for f in cur.fetchall()])


def _con_vinculos(cur, eventos: list) -> list:
    todas = [a for e in eventos for a in e["actas"]]
    info = resolver_actas(cur, todas)
    for e in eventos:
        e["vinculos"] = [info[a] for a in e["actas"]]
    return eventos


def estado(cur) -> dict:
    cur.execute("SELECT * FROM calendario_estado WHERE id = 1")
    f = cur.fetchone() or {}
    return {
        "configurado": configurado(),
        "calendario": calendar_id(),
        "cuenta_servicio": cuenta_servicio(),
        "ultimo_ok_en": f.get("ultimo_ok_en"),
        "ultimo_intento_en": f.get("ultimo_intento_en"),
        "ultimo_error": f.get("ultimo_error"),
        "ultima_completa_en": f.get("ultima_completa_en"),
        "ultimo_cambios": f.get("ultimo_cambios"),
        "sondeo_segundos": SONDEO_SEGUNDOS,
    }
