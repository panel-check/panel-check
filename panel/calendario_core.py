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

Acceso a Google, de dos maneras (variables de entorno en Railway):
  1) Cuenta de servicio (sirve para ver y agendar, pero Google no le deja crear
     links de Meet a una cuenta de servicio con Gmail común):
       GOOGLE_SERVICE_ACCOUNT_JSON  el contenido COMPLETO del .json.
       GOOGLE_CALENDAR_ID           el ID del calendario (para el calendario
                                    principal de una cuenta Gmail es el mismo mail).
     El calendario tiene que estar compartido con el mail de la cuenta de servicio
     (client_email del JSON) con permiso «Hacer cambios en eventos».
  2) La propia cuenta de Google del estudio (OAuth): lo que hace falta para generar
     links de Meet. Si estas tres variables están, se usan en lugar de la cuenta de servicio:
       GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN
     (el token se consigue con el botón «Conectar con Google» de la pantalla Calendario).

Al agendar desde el panel se elige la modalidad (videollamada de Meet, llamada o
presencial) y, si se pide, se manda un mail de aviso a la persona y una copia al
equipo (por Resend, ver armar_aviso / avisar).

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
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"

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


def _oauth_config():
    """(client_id, client_secret, refresh_token); los que falten, None."""
    g = lambda k: (os.environ.get(k) or "").strip() or None
    return g("GOOGLE_OAUTH_CLIENT_ID"), g("GOOGLE_OAUTH_CLIENT_SECRET"), g("GOOGLE_OAUTH_REFRESH_TOKEN")


def oauth_completo() -> bool:
    """Está conectada la cuenta de Google del estudio (puede generar links de Meet)."""
    return all(_oauth_config())


def oauth_pendiente() -> bool:
    """Están cargados el ID y el secreto del cliente OAuth, pero falta conectar la cuenta."""
    cid, secreto, token = _oauth_config()
    return bool(cid and secreto and not token)


def puede_meet() -> bool:
    return oauth_completo() and bool(calendar_id())


def configurado() -> bool:
    credenciales = oauth_completo() or bool((os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip())
    return bool(credenciales and calendar_id())


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
    if not calendar_id():
        raise CalendarioError("Falta configurar GOOGLE_CALENDAR_ID en Railway.")
    cid, secreto, token = _oauth_config()
    if cid and secreto and token:
        clave = ("oauth", cid, token)
    else:
        info = _info_cuenta()
        if not info:
            raise CalendarioError("Falta configurar GOOGLE_SERVICE_ACCOUNT_JSON (o conectar la cuenta de Google) y GOOGLE_CALENDAR_ID en Railway.")
        clave = ("servicio", info["client_email"], info.get("private_key_id"))
    with _sesion_lock:
        if _sesion_cache["clave"] != clave:
            from google.auth.transport.requests import AuthorizedSession

            if clave[0] == "oauth":
                from google.oauth2.credentials import Credentials

                creds = Credentials(None, refresh_token=token, token_uri=TOKEN_URI, client_id=cid,
                                    client_secret=secreto, scopes=[SCOPE])
            else:
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
    if oauth_completo():
        if status == 404:
            return ("Google no encuentra el calendario: con la cuenta del estudio conectada, GOOGLE_CALENDAR_ID tiene que ser el mail "
                    "de esa misma cuenta (o el ID de un calendario suyo).")
        if status == 403:
            return f"Google rechazó el acceso con la cuenta conectada (¿Calendar API desactivada o sin permiso de edición?). {detalle}".strip()
        if status == 401:
            return f"Google no aceptó la conexión con la cuenta del estudio: hay que volver a conectarla. {detalle}".strip()
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
        if "invalid_grant" in str(e) or "invalid_client" in str(e):
            raise CalendarioError("Google ya no acepta la conexión con la cuenta del estudio (el permiso se revocó, venció o cambió el cliente OAuth). "
                                  "Entrá a Calendario → «Conectar con Google» y cargá el token nuevo en GOOGLE_OAUTH_REFRESH_TOKEN.")
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
    # Agregadas con la modalidad (07/10/2026): 'meet' | 'llamada' | 'presencial' y el link de la videollamada.
    cur.execute("ALTER TABLE calendario_eventos ADD COLUMN IF NOT EXISTS modalidad TEXT")
    cur.execute("ALTER TABLE calendario_eventos ADD COLUMN IF NOT EXISTS meet_url TEXT")
    # Mail de la persona a la que se le avisa (para poder avisarle también si después se cambia la fecha u hora).
    cur.execute("ALTER TABLE calendario_eventos ADD COLUMN IF NOT EXISTS email_aviso TEXT")
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


MODALIDADES = ("meet", "llamada", "presencial")


def meet_de_evento(ev: dict):
    """Link de la videollamada de Meet de un evento de Google (o None)."""
    if ev.get("hangoutLink"):
        return ev["hangoutLink"]
    for punto in ((ev.get("conferenceData") or {}).get("entryPoints") or []):
        if punto.get("entryPointType") == "video" and punto.get("uri"):
            return punto["uri"]
    return None


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
    meet = meet_de_evento(ev)
    modalidad = privadas.get("modalidad") if privadas.get("modalidad") in MODALIDADES else None
    if meet:
        modalidad = "meet"
    elif modalidad == "meet":
        modalidad = None  # le sacaron el Meet desde Google
    return {
        "modalidad": modalidad,
        "meet_url": meet,
        "email_aviso": (privadas.get("email_aviso") or "").strip() or None,
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
             origen, creado_por, link, etag, actualizado_google, sincronizado_en, modalidad, meet_url, email_aviso)
        VALUES
            (%(calendar_id)s, %(google_id)s, %(titulo)s, %(descripcion)s, %(lugar)s, %(inicio)s, %(fin)s,
             %(todo_el_dia)s, %(actas)s, %(origen)s, %(creado_por)s, %(link)s, %(etag)s, %(actualizado_google)s, now(),
             %(modalidad)s, %(meet_url)s, %(email_aviso)s)
        ON CONFLICT (calendar_id, google_id) DO UPDATE SET
            titulo = EXCLUDED.titulo, descripcion = EXCLUDED.descripcion, lugar = EXCLUDED.lugar,
            inicio = EXCLUDED.inicio, fin = EXCLUDED.fin, todo_el_dia = EXCLUDED.todo_el_dia,
            actas = EXCLUDED.actas, origen = EXCLUDED.origen, creado_por = EXCLUDED.creado_por,
            link = EXCLUDED.link, etag = EXCLUDED.etag,
            actualizado_google = EXCLUDED.actualizado_google, sincronizado_en = now(),
            modalidad = EXCLUDED.modalidad, meet_url = EXCLUDED.meet_url, email_aviso = EXCLUDED.email_aviso
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
                        (cal, sorted(vistos)),
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
    modalidad = (datos.get("modalidad") or "").strip() or None
    if modalidad not in (None,) + MODALIDADES:
        raise CalendarioError("La modalidad tiene que ser videollamada de Meet, llamada o presencial.")
    start, end, _ = _inicio_fin_google(datos)
    privadas = {"panel": "1", "usuario": usuario or "", "actas": ",".join(actas)}
    if modalidad:
        privadas["modalidad"] = modalidad
    email = " ".join(str(datos.get("email_aviso") or "").split())
    if email:
        privadas["email_aviso"] = email
    return {
        "summary": titulo,
        "description": _nota_con_actas(datos.get("descripcion"), actas),
        "location": (datos.get("lugar") or "").strip(),
        "start": start,
        "end": end,
        "extendedProperties": {"private": privadas},
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


def _pedir_meet():
    """El fragmento que le pide a Google que cree la videollamada de Meet del evento."""
    import uuid

    return {"createRequest": {"requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}


def _exigir_meet_posible():
    if not puede_meet():
        raise CalendarioError(
            "Para generar el link de Meet hay que conectar el panel con la cuenta de Google del estudio: "
            "entrá a Calendario → «Conectar con Google» (Google no deja crear Meet con la cuenta de servicio). "
            "Mientras tanto se puede agendar como llamada o presencial."
        )


def _esperar_meet(google_id: str, recurso: dict) -> dict:
    """Google arma la sala de Meet en el momento, pero a veces el link llega unos segundos
    después de crear el evento: se vuelve a pedir hasta tenerlo."""
    for _ in range(4):
        if meet_de_evento(recurso):
            return recurso
        time.sleep(1.2)
        recurso = _pedir("GET", _ruta_eventos("/" + google_id)) or recurso
    return recurso


def crear_evento(conexion, datos: dict, usuario: str) -> int:
    """Crea el evento en Google y lo copia al panel. Devuelve el id local. Con modalidad
    «meet», Google genera la videollamada y su link queda guardado en el evento."""
    cuerpo = _cuerpo_evento(datos, usuario)
    params = {"sendUpdates": "none"}
    if datos.get("modalidad") == "meet":
        _exigir_meet_posible()
        cuerpo["conferenceData"] = _pedir_meet()
        params["conferenceDataVersion"] = 1
    recurso = _pedir("POST", _ruta_eventos(), json=cuerpo, params=params)
    if datos.get("modalidad") == "meet":
        recurso = _esperar_meet(recurso["id"], recurso)
        if not meet_de_evento(recurso):
            _guardar_resultado(conexion, recurso)
            raise CalendarioError("El evento se creó, pero Google todavía no devolvió el link de Meet. Editalo y guardalo de nuevo en un momento.")
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
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT google_id, meet_url FROM calendario_eventos WHERE id = %s AND calendar_id = %s",
                        (evento_id, calendar_id()))
            fila = cur.fetchone()
    if not fila:
        raise CalendarioError("Ese evento ya no existe (puede que lo hayan borrado desde Google Calendar).")
    google_id, meet_actual = fila[0], fila[1]
    cuerpo = _cuerpo_evento(datos, usuario)
    params = {"sendUpdates": "none"}
    quiere_meet = datos.get("modalidad") == "meet"
    if quiere_meet and not meet_actual:
        _exigir_meet_posible()
        cuerpo["conferenceData"] = _pedir_meet()
        params["conferenceDataVersion"] = 1
    elif not quiere_meet and meet_actual:
        cuerpo["conferenceData"] = None  # se saca la videollamada del evento
        params["conferenceDataVersion"] = 1
    if not datos.get("modalidad"):
        cuerpo["extendedProperties"]["private"]["modalidad"] = None  # en un PATCH, null borra la marca anterior
    if "email_aviso" not in cuerpo["extendedProperties"]["private"]:
        cuerpo["extendedProperties"]["private"]["email_aviso"] = None
    # En un PATCH, start y end se MEZCLAN con lo que ya tiene el evento: pasar de un evento con horario a «todo el día»
    # (o al revés) deja date y dateTime juntos y Google lo rechaza. Se anula explícitamente el que no corresponde.
    for k in ("start", "end"):
        t = cuerpo[k]
        for campo in ("date", "dateTime", "timeZone"):
            t.setdefault(campo, None)
    recurso = _pedir("PATCH", _ruta_eventos("/" + google_id), json=cuerpo, params=params)
    if quiere_meet and not meet_actual:
        recurso = _esperar_meet(google_id, recurso)
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


# ── Avisos por mail (a la persona y copia al equipo) ───────────────────

DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre")
# Los avisos salen con el remitente, la cuenta de Resend y el Reply-To del mail
# «Presupuesto de registro de marca» (se editan en Más → Mails): escrito como una persona, sin encabezado.
CLAVE_MAIL_BASE = "prospecto_presupuesto_registro"


def mail_equipo():
    """A dónde llega la copia de cada aviso: el mail del estudio (el del calendario)."""
    valor = (os.environ.get("CALENDARIO_AVISO_EQUIPO") or calendar_id() or "").strip()
    return valor if re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", valor) else None


def _fecha_larga(d: _dt.date) -> str:
    return f"{DIAS_SEMANA[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


def cuando_texto(ev: dict) -> str:
    """«miércoles 7 de octubre de 2026, de 16:00 a 17:00 hs (hora de Argentina)»."""
    d0 = _dt.date.fromisoformat(ev["fecha"])
    if ev.get("todo_el_dia") or not ev.get("hora"):
        d1 = _dt.date.fromisoformat(ev.get("fecha_fin") or ev["fecha"])
        if d1 > d0:
            return f"del {_fecha_larga(d0)} al {_fecha_larga(d1)}"
        return f"{_fecha_larga(d0)} (todo el día)"
    return f"{_fecha_larga(d0)}, de {ev['hora']} a {ev['hora_fin']} hs (hora de Argentina)"


MESES_CORTOS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _hora12(hhmm: str) -> str:
    """«13:30» → «1:30pm»; «14:00» → «2pm»."""
    h, m = [int(x) for x in hhmm.split(":")[:2]]
    sufijo = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{sufijo}" if m == 0 else f"{h12}:{m:02d}{sufijo}"


def _fecha_corta_larga(d: _dt.date) -> str:
    return f"{DIAS_SEMANA[d.weekday()]} {d.day} {MESES_CORTOS[d.month - 1]} {d.year}"


def cuando_corto(ev: dict) -> str:
    """Como lo muestra Google Calendar, para leerlo de un vistazo en la bandeja de entrada:
    «viernes 9 oct 2026 · 1:30pm – 2pm (Hora estándar de Argentina)»."""
    d0 = _dt.date.fromisoformat(ev["fecha"])
    if ev.get("todo_el_dia") or not ev.get("hora"):
        d1 = _dt.date.fromisoformat(ev.get("fecha_fin") or ev["fecha"])
        if d1 > d0:
            return f"{_fecha_corta_larga(d0)} – {_fecha_corta_larga(d1)}"
        return f"{_fecha_corta_larga(d0)} · todo el día"
    return f"{_fecha_corta_larga(d0)} · {_hora12(ev['hora'])} – {_hora12(ev['hora_fin'])} (Hora estándar de Argentina)"


def como_texto(ev: dict, para_equipo: bool = False) -> str:
    """Cómo es la reunión. Para la persona («Te llamamos por teléfono al …») o para el equipo («Llamada al …»)."""
    lugar = (ev.get("lugar") or "").strip()
    modalidad = ev.get("modalidad")
    if modalidad == "meet":
        return "Videollamada por Google Meet"
    if modalidad == "llamada":
        if para_equipo:
            return f"Llamada al {lugar}" if lugar else "Llamada (sin teléfono anotado)"
        return f"Te llamamos por teléfono al {lugar}" if lugar else "Te llamamos por teléfono"
    if modalidad == "presencial":
        return f"En persona: {lugar}" if lugar else "En persona"
    return lugar or "A coordinar"


def link_agregar_a_calendario(ev: dict) -> str:
    """Link «Agregar a Google Calendar» (sirve aunque la persona no use Google)."""
    from urllib.parse import quote

    d0 = _dt.date.fromisoformat(ev["fecha"])
    if ev.get("todo_el_dia") or not ev.get("hora"):
        d1 = _dt.date.fromisoformat(ev.get("fecha_fin") or ev["fecha"]) + _dt.timedelta(days=1)
        fechas = f"{d0:%Y%m%d}/{d1:%Y%m%d}"
    else:
        h0, h1 = ev["hora"].replace(":", ""), ev["hora_fin"].replace(":", "")
        d_fin = d0 if ev["hora_fin"] > ev["hora"] else d0 + _dt.timedelta(days=1)
        fechas = f"{d0:%Y%m%d}T{h0}00/{d_fin:%Y%m%d}T{h1}00"
    detalle = como_texto(ev) + (f"\n{ev['meet_url']}" if ev.get("meet_url") else "")
    lugar = ev.get("meet_url") or ev.get("lugar") or ""
    return ("https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={quote(ev['titulo'], safe='')}&dates={fechas}&ctz={quote(TZ_NOMBRE, safe='')}"
            f"&details={quote(detalle, safe='')}&location={quote(lugar, safe='')}")


_VERBOS = {"agendada": "Agendamos", "modificada": "Actualizamos"}


def armar_aviso(ev: dict, accion: str = "agendada", para_equipo: bool = False, usuario: str = "",
                email_persona: str = "", panel_url: str = "") -> tuple:
    """(asunto, html, texto) del aviso. `ev` es un evento ya convertido con evento_a_json
    (con sus vínculos). Para la persona: qué, cuándo y cómo, con el link de Meet si hay y un
    link para sumarlo a su calendario. Para el equipo: lo mismo más quién lo agendó, a quién se
    avisó y de qué acta/cliente es."""
    import mails_core as mc

    verbo_persona = _VERBOS.get(accion, _VERBOS["agendada"])
    datos = {
        "titulo": ev["titulo"], "cuando": cuando_texto(ev), "como": como_texto(ev),
        "link_meet": ev.get("meet_url") or "", "link_cal": link_agregar_a_calendario(ev),
        "quien": usuario or "Alguien del equipo", "persona": email_persona or "",
        "panel": f"{(panel_url or mc.DEFAULT_PANEL).rstrip('/')}/calendario",
    }
    meet = "* **Videollamada (link):** {{link_meet}}\n" if ev.get("meet_url") else ""
    if not para_equipo:
        asunto = f"{verbo_persona} tu reunión: {{{{titulo}}}}"
        intro = ("Te confirmamos que agendamos una reunión con Smarties Consultora." if accion == "agendada"
                 else "Actualizamos los datos de tu reunión con Smarties Consultora.")
        cuerpo = (f"Hola:\n\n{intro}\n\n"
                  "* **Qué:** {{titulo}}\n* **Cuándo:** {{cuando}}\n* **Cómo:** {{como}}\n" + meet.rstrip("\n") +
                  f"\n\n[Sumarla a mi calendario]({datos['link_cal']})"
                  "\n\nSi necesitás cambiar el horario, respondé este mail.\n\nSaludos,\nSmarties Consultora")
    else:
        # Para el equipo el mail se arma para leerlo en la lista de la bandeja: el asunto es el título del
        # evento y lo primero del cuerpo (lo que Gmail muestra al lado del asunto) es «Agendada: <cuándo>».
        vinculos = [v for v in ev.get("vinculos") or []]
        etiquetas = {"cliente": "cliente", "lead": "lead", "tercero": "tercero", "desconocida": "acta sin base"}
        datos["cuando_corto"] = cuando_corto(ev)
        datos["como"] = como_texto(ev, para_equipo=True)
        datos["estado"] = "Agendada" if accion == "agendada" else "Actualizada"
        if vinculos:
            datos["actas"] = "; ".join(
                f"{v['acta']}" + (f" ({v['denominacion']}" + (f", {v['titular']}" if v.get("titular") else "") + ")" if v.get("denominacion") else "")
                + f" – {etiquetas.get(v.get('tipo'), '')}" for v in vinculos)
        asunto = "{{titulo}}" if accion == "agendada" else "Actualizada: {{titulo}}"
        cuerpo = ("{{estado}}: {{cuando_corto}}\n\n"
                  "* **Cómo:** {{como}}\n" + meet.replace("Videollamada (link)", "Link de Meet") +
                  ("* **Actas:** {{actas}}\n" if vinculos else "") +
                  ("* **Aviso a la persona:** {{persona}}\n" if email_persona else "* **Aviso a la persona:** no se mandó\n") +
                  "* **Agendó:** {{quien}}\n"
                  "\nVerlo en el panel: {{panel}}")
    return mc.render_prospecto(asunto, cuerpo.strip(), datos, simple=True)


def avisar(conexion, evento_id: int, accion: str, usuario: str, email_persona: str = "", a_equipo: bool = True) -> dict:
    """Manda el aviso del evento: a la persona (si hay mail) y una copia al equipo. Un mail que
    falla no frena al otro ni al evento (que ya está en Google): el resultado cuenta qué pasó con cada uno.
    Devuelve {"persona": {...}|None, "equipo": {...}|None}; cada uno {email, enviado, error}."""
    import mails_core as mc

    out = {"persona": None, "equipo": None}
    destino_persona = []
    if (email_persona or "").strip():
        destino_persona = mc.lista_de_emails(email_persona)
        malos = [d for d in destino_persona if not mc._RE_EMAIL.match(d)]
        if malos or not destino_persona:
            out["persona"] = {"email": email_persona.strip(), "enviado": False, "error": f"«{(malos or [email_persona])[0]}» no es un mail válido"}
            destino_persona = []
        destino_persona = destino_persona[:3]
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM calendario_eventos WHERE id = %s", (evento_id,))
            fila = cur.fetchone()
            if not fila:
                raise CalendarioError("El evento ya no existe.")
            ev = _con_vinculos(cur, [evento_a_json(fila)])[0]
            de_baja = {d: mc.esta_de_baja(cur, d) for d in destino_persona}
    try:
        base = mc.preparar(CLAVE_MAIL_BASE)
    except Exception as e:
        base = None
        error_config = f"No se pudo leer la configuración de mails: {e}"
    panel_url = (os.environ.get("PANEL_URL") or mc.DEFAULT_PANEL).rstrip("/")

    def mandar(para, para_equipo):
        if base is None:
            return {"email": ", ".join(para), "enviado": False, "error": error_config}
        try:
            asunto, html_, texto = armar_aviso(ev, accion, para_equipo, usuario, ", ".join(destino_persona), panel_url)
            mc.enviar(base["cuenta"], base["remitente"], base["responder_a"], para, asunto, html_, texto)
            return {"email": ", ".join(para), "enviado": True, "error": None}
        except Exception as e:  # sin RESEND_API_KEY_PROSPECTOS, Resend caído, etc.
            return {"email": ", ".join(para), "enviado": False, "error": str(e)[:300]}

    if destino_persona:
        permitidos = [d for d in destino_persona if not de_baja.get(d)]
        if permitidos:
            out["persona"] = mandar(permitidos, False)
        else:
            out["persona"] = {"email": ", ".join(destino_persona), "enviado": False,
                              "error": "esa persona pidió no recibir más mails (baja)"}
    equipo = mail_equipo()
    if a_equipo and equipo:
        out["equipo"] = mandar([equipo], True)
    return out


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
        "modalidad": f.get("modalidad"),
        "meet_url": f.get("meet_url"),
        "email_aviso": f.get("email_aviso"),
    }


def resolver_actas(cur, actas) -> dict:
    """Para cada acta: de quién es. {acta: {acta, existe, denominacion, titular, clave,
    cliente_id, cliente, tipo}}. `tipo`: «cliente» (está en la cartera o su titular es un cliente
    del estudio), «lead» (marca de un titular que todavía no es cliente), «tercero» (solicitud
    que vio el escaneo pero tiene agente: no es lead ni cliente) o «desconocida» (no está en la base)."""
    actas = [a for a in dict.fromkeys(actas or []) if a]
    res = {a: {"acta": a, "existe": False, "denominacion": None, "titular": None, "clave": None,
               "cliente_id": None, "cliente": None, "tipo": "desconocida", "email": None,
               "en_marcas": False, "tiene_analisis": False} for a in actas}
    if not actas:
        return res

    cur.execute(
        """
        SELECT m.acta, COALESCE(NULLIF(m.denominacion_inpi, ''), NULLIF(m.denominacion, '')) AS denominacion,
               m.titular, cc.clave, m.email
        FROM marcas m LEFT JOIN crm_claves cc ON cc.acta = m.acta
        WHERE m.acta = ANY(%s)
        """,
        (actas,),
    )
    for f in cur.fetchall():
        r = res[f["acta"]]
        r.update(existe=True, denominacion=f["denominacion"], titular=f["titular"], clave=f["clave"], tipo="lead",
                 email=(f["email"] or "").strip() or None, en_marcas=True)

    # ¿Ya tiene análisis de marca escrito? (para el botón «Ver / Generar análisis» de la tarjeta del evento)
    en_marcas = [a for a, r in res.items() if r["en_marcas"]]
    if en_marcas:
        cur.execute("SELECT acta FROM analisis_marca WHERE acta = ANY(%s) AND btrim(texto) <> ''", (en_marcas,))
        for f in cur.fetchall():
            res[f["acta"]]["tiene_analisis"] = True

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
        SELECT cm.acta, cm.denominacion, cm.titular, c.id AS cliente_id, c.nombre, c.email
        FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id
        WHERE cm.acta = ANY(%s)
        """,
        (actas,),
    )
    for f in cur.fetchall():
        r = res[f["acta"]]
        r.update(existe=True, cliente_id=f["cliente_id"], cliente=f["nombre"], tipo="cliente",
                 email=(f["email"] or "").strip() or r["email"])
        r["denominacion"] = r["denominacion"] or f["denominacion"]
        r["titular"] = r["titular"] or f["titular"]

    # Titular de un lead que ya se convirtió en cliente del estudio.
    claves = list({r["clave"] for r in res.values() if r["clave"] and r["tipo"] == "lead"})
    if claves:
        cur.execute("SELECT id, nombre, clave_crm, email FROM clientes WHERE clave_crm = ANY(%s)", (claves,))
        por_clave = {f["clave_crm"]: f for f in cur.fetchall()}
        for r in res.values():
            c = por_clave.get(r["clave"]) if r["tipo"] == "lead" else None
            if c:
                r.update(cliente_id=c["id"], cliente=c["nombre"], tipo="cliente",
                         email=(c["email"] or "").strip() or r["email"])
    return res


# Eventos que NO se muestran en el panel (siguen en Google Calendar): el Meet fijo del equipo,
# que se repite todos los días y estorba. Se compara el título sin tildes, mayúsculas ni espacios
# de más. Se cambia con CALENDARIO_OCULTAR (títulos separados por «;»; vacío = no ocultar nada).
OCULTAR_POR_DEFECTO = "MEET PAME / TOMI"


def _titulo_normal(texto: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFD", texto or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    t = re.sub(r"\s*/\s*", "/", t)
    return " ".join(t.split())


def titulos_ocultos() -> set:
    crudo = os.environ.get("CALENDARIO_OCULTAR")
    crudo = OCULTAR_POR_DEFECTO if crudo is None else crudo
    return {n for n in (_titulo_normal(t) for t in crudo.split(";")) if n}


def _sin_ocultos(filas: list) -> list:
    ocultos = titulos_ocultos()
    return [f for f in filas if _titulo_normal(f["titulo"]) not in ocultos] if ocultos else filas


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
    return _con_vinculos(cur, [evento_a_json(f) for f in _sin_ocultos(cur.fetchall())])


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
    return _con_vinculos(cur, [evento_a_json(f) for f in _sin_ocultos(cur.fetchall())])


_ACENTOS = ("áéíóúüñ", "aeiouun")


def _buscable(texto: str) -> str:
    t = (texto or "").lower()
    for a, b in zip(*_ACENTOS):
        t = t.replace(a, b)
    return " ".join(t.split())


def buscar_marcas(cur, texto: str, limite: int = 12) -> list:
    """Búsqueda en vivo para vincular un evento: por nombre de la marca, del titular o del cliente,
    o por número de acta (los primeros dígitos). Todas las palabras tienen que aparecer (en cualquier
    orden, sin tildes ni mayúsculas). Primero van las marcas de clientes y después las de leads.
    Cada resultado: {acta, denominacion, titular, clase, tipo, cliente, email}."""
    palabras = [w for w in _buscable(texto).split(" ") if w][:5]
    if not palabras or sum(len(w) for w in palabras) < 2:
        return []
    patrones = ["%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for w in palabras]
    trad = "translate(lower(concat_ws(' ', {})), 'áéíóúüñ', 'aeiouun')"
    clientes = trad.format("cm.denominacion, cm.titular, c.nombre, cm.acta")
    leads = trad.format("m.denominacion_inpi, m.denominacion, m.titular, m.acta")
    cur.execute(
        f"""
        SELECT * FROM (
            SELECT 'cliente' AS tipo, cm.acta, cm.denominacion, cm.titular, cm.clase, c.nombre AS cliente, c.email
            FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id
            WHERE {" AND ".join([clientes + " LIKE %s"] * len(patrones))}
            UNION ALL
            SELECT 'lead', m.acta, COALESCE(NULLIF(m.denominacion_inpi, ''), NULLIF(m.denominacion, '')),
                   m.titular, m.clase, NULL, m.email
            FROM marcas m
            WHERE m.es_lead AND {" AND ".join([leads + " LIKE %s"] * len(patrones))}
        ) t
        ORDER BY (tipo = 'cliente') DESC, denominacion, acta
        LIMIT 60
        """,
        patrones + patrones,
    )
    por_acta = {}
    for f in cur.fetchall():
        previa = por_acta.get(f["acta"])
        if previa and not (previa["tipo"] == "lead" and f["tipo"] == "cliente"):
            continue   # una marca de cliente que también es lead se muestra como cliente
        por_acta[f["acta"]] = {"acta": f["acta"], "denominacion": f["denominacion"], "titular": f["titular"], "clase": f["clase"],
                               "tipo": f["tipo"], "cliente": f["cliente"], "email": (f["email"] or "").strip() or None}
    out = list(por_acta.values())
    # las que empiezan con lo escrito primero (sin cambiar el orden cliente/lead dentro de cada grupo)
    q0 = _buscable(texto)
    out.sort(key=lambda r: (r["tipo"] != "cliente", not _buscable(r["denominacion"]).startswith(q0)))
    return out[:limite]


# ── Agendar desde un texto pegado (IA) ────────────────────────────────
# La IA (ia_texto.extraer_agenda) solo EXTRAE datos. Acá se validan uno por uno contra el texto
# original y contra la base, se arma el título y se calcula qué falta para preguntárselo a la persona.
# Nada se agenda sin que la persona revise y guarde el formulario.
_RE_MAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_OMITIBLES = ("quien", "email", "telefono", "lugar")   # lo que la persona puede decidir no cargar
_BASE_TITULO = {"meet": "Reunión virtual", "llamada": "Llamada", "presencial": "Reunión", None: "Reunión"}


def _str_o_none(v):
    v = " ".join(str(v).split()) if v is not None else ""
    return v if v and v.lower() not in ("null", "none", "n/a") else None


def _hora_valida(v):
    m = re.fullmatch(r"(\d{1,2})(?::|\.|h)?(\d{2})?\s*(?:hs?)?", (_str_o_none(v) or "").lower())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def _fecha_valida(v, texto: str, hoy: _dt.date):
    """(fecha, ya_paso). Si el texto no trae el año y la fecha quedó atrás, es la del año que viene."""
    try:
        d = _dt.date.fromisoformat((_str_o_none(v) or "")[:10])
    except ValueError:
        return None, False
    if d < hoy and str(d.year) not in (texto or ""):
        try:
            d = d.replace(year=d.year + 1)
        except ValueError:
            return None, False
    return d, d < hoy


def _modalidad_valida(v, texto: str):
    m = (_str_o_none(v) or "").lower()
    if m in MODALIDADES:
        return m
    t = _buscable(texto)
    if re.search(r"\bmeet\b|videollamada|google meet", t):
        return "meet"
    if re.search(r"\bllamada\b|\btelefon", t):
        return "llamada"
    if re.search(r"presencial|en persona", t):
        return "presencial"
    return None


def _se_parecen(a: str, b: str) -> bool:
    """¿El nombre de la marca del texto y el de la base comparten alguna palabra (o uno contiene al otro)?"""
    a, b = _buscable(a), _buscable(b)
    if not a or not b:
        return True
    if a in b or b in a:
        return True
    pa = {w for w in re.findall(r"[a-z0-9]+", a) if len(w) >= 3}
    pb = {w for w in re.findall(r"[a-z0-9]+", b) if len(w) >= 3}
    return bool(pa & pb)


def armar_propuesta(cur, crudo: dict, texto: str, hoy: _dt.date, omitir=()) -> dict:
    """De lo que devolvió la IA (y el texto original) a una propuesta lista para el formulario.
    {campos, negocio, faltantes: [{campo, pregunta, omitible, opciones?}], advertencias, marcas}.
    `omitir`: campos que la persona ya dijo que no quiere cargar (no se vuelven a preguntar)."""
    omitir = set(omitir or ())
    crudo = crudo or {}
    adv, faltan = [], []
    t_norm = _buscable(texto)

    # Nombre del negocio: solo si realmente está en el texto (la IA puede inventar).
    negocio = _str_o_none(crudo.get("negocio"))
    if negocio and _buscable(negocio) not in t_norm:
        negocio = None

    # Actas: las que el texto nombra; la de la IA solo si sus dígitos están en el texto.
    actas = extraer_actas(texto)
    ia_acta = re.sub(r"\D", "", str(crudo.get("acta") or ""))
    if not actas and re.fullmatch(r"\d{4,9}", ia_acta) and ia_acta in re.sub(r"[.\s]", "", texto):
        actas = [ia_acta]

    # Mail(es): salen del texto con una expresión regular, no de la IA.
    mails = list(dict.fromkeys(m.rstrip(".'-") for m in _RE_MAIL.findall(texto)))

    fecha, ya_paso = _fecha_valida(crudo.get("fecha"), texto, hoy)
    hora = _hora_valida(crudo.get("hora"))
    modalidad = _modalidad_valida(crudo.get("modalidad"), texto)
    try:
        dur = int(crudo.get("duracion_min"))
        dur = dur if 5 <= dur <= 480 else None
    except (TypeError, ValueError):
        dur = None
    tel = _str_o_none(crudo.get("telefono"))
    if tel and (len(re.sub(r"\D", "", tel)) < 6 or re.sub(r"\D", "", tel) not in re.sub(r"\D", "", texto)):
        tel = None
    lugar = _str_o_none(crudo.get("lugar"))
    notas = _str_o_none(crudo.get("notas"))

    # Quién es: por acta (se confirma contra la base) o por nombre (se busca).
    marcas, denominacion, email_base = [], None, None
    if actas:
        res = resolver_actas(cur, actas)
        marcas = [res[a] for a in actas if a in res]
        for r in marcas:
            if r["tipo"] == "desconocida":
                adv.append(f"El acta {r['acta']} no está en la base: se agenda igual y se vincula si después se carga.")
            else:
                denominacion = denominacion or r.get("denominacion")
                email_base = email_base or r.get("email")
                if negocio and r.get("denominacion") and not _se_parecen(negocio, r["denominacion"]):
                    adv.append(f"El acta {r['acta']} figura como «{r['denominacion']}», pero el texto dice «{negocio}»: revisá que sea la correcta.")
    elif negocio:
        halladas = buscar_marcas(cur, negocio, limite=6)
        if len(halladas) == 1:
            h = halladas[0]
            actas = [h["acta"]]
            denominacion, email_base = h["denominacion"], h.get("email")
            marcas = [{**h, "existe": True}]
            adv.append(f"Vinculé el evento con la acta {h['acta']} ({h['denominacion'] or '—'}, {h['titular'] or h['cliente'] or '—'}): revisá que sea la correcta.")
        elif len(halladas) > 1 and "quien" not in omitir:
            faltan.append({"campo": "quien", "omitible": True,
                           "pregunta": f"Encontré varias marcas parecidas a «{negocio}»: ¿cuál es?",
                           "opciones": halladas})
        else:
            adv.append(f"No encontré «{negocio}» en la base: el evento se agenda sin vincular a ninguna acta.")
    elif "quien" not in omitir:
        faltan.append({"campo": "quien", "omitible": True,
                       "pregunta": "¿De qué marca o negocio es? (el nombre o el número de acta)"})

    # Qué falta (lo importante se pregunta; lo secundario se puede omitir).
    if fecha is None or ya_paso:
        faltan.insert(0, {"campo": "fecha", "omitible": False,
                          "pregunta": (f"La fecha que entendí ({fecha.day}/{fecha.month}/{fecha.year}) ya pasó. ¿Qué día es?"
                                       if fecha else "¿Qué día es?")})
        if ya_paso:
            fecha = None
    if hora is None:
        faltan.append({"campo": "hora", "omitible": False, "pregunta": "¿A qué hora es?"})
    if modalidad is None:
        faltan.append({"campo": "modalidad", "omitible": False,
                       "pregunta": "¿Cómo es: videollamada de Meet, llamada o presencial?"})
    mail_final = ", ".join(mails) or email_base
    if not mail_final and "email" not in omitir:
        faltan.append({"campo": "email", "omitible": True,
                       "pregunta": "¿A qué mail le aviso a la persona? (si no tiene, omitilo y el aviso va solo al equipo)"})
    if modalidad == "llamada" and not tel and "telefono" not in omitir:
        faltan.append({"campo": "telefono", "omitible": True, "pregunta": "¿A qué teléfono se llama?"})
    if modalidad == "presencial" and not lugar and "lugar" not in omitir:
        faltan.append({"campo": "lugar", "omitible": True, "pregunta": "¿Dónde es? (lugar o dirección)"})

    if fecha and fecha.weekday() >= 5:
        adv.append(f"El {fecha.day} de {MESES[fecha.month - 1]} cae {DIAS_SEMANA[fecha.weekday()]}: revisá que la fecha esté bien.")

    nombre = denominacion or negocio   # el nombre que tiene la marca en el panel; si no está, el del texto
    titulo = _BASE_TITULO.get(modalidad, "Reunión") + (f" ({nombre})" if nombre else "")
    return {
        "campos": {
            "titulo": titulo, "fecha": fecha.isoformat() if fecha else None, "hora": hora, "duracion_min": dur,
            "modalidad": modalidad, "email": mail_final,
            "lugar": (tel if modalidad == "llamada" else lugar if modalidad == "presencial" else None),
            "actas": actas, "notas": notas,
        },
        "negocio": negocio, "nombre": nombre, "marcas": marcas, "faltantes": faltan, "advertencias": adv,
    }


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
        "puede_meet": puede_meet(),
        "oauth_conectado": oauth_completo(),
        "oauth_pendiente": oauth_pendiente(),
        "aviso_equipo": mail_equipo(),
        "ultimo_ok_en": f.get("ultimo_ok_en"),
        "ultimo_intento_en": f.get("ultimo_intento_en"),
        "ultimo_error": f.get("ultimo_error"),
        "ultima_completa_en": f.get("ultima_completa_en"),
        "ultimo_cambios": f.get("ultimo_cambios"),
        "sondeo_segundos": SONDEO_SEGUNDOS,
    }
