"""
Login del panel: usuarios con clave hasheada en Postgres + sesiones con cookie.

Reemplaza al HTTP Basic de antes, que tenía dos problemas:
  - no se podía cerrar sesión (el navegador guarda usuario/clave y los manda
    solo en cada pedido hasta que se cierra el navegador), y
  - las claves vivían en texto plano en variables de Railway.

Cómo funciona ahora:
  - usuarios_panel: un usuario por fila, con la clave hasheada con scrypt
    (nunca se guarda la clave en sí). Se pueden crear, desactivar y resetear
    desde el panel (pantalla "Usuarios", solo administradores).
  - sesiones_panel: al entrar se genera un token aleatorio de 256 bits que va
    al navegador en una cookie HttpOnly + Secure + SameSite=Lax (JavaScript no
    la puede leer y no viaja en pedidos de otros sitios). En la base se guarda
    solo el SHA-256 del token: aunque alguien leyera la tabla, no podría usar
    esas sesiones. "Cerrar sesión" borra la fila, así que la cookie deja de
    servir al instante (también se pueden cerrar las demás sesiones abiertas).
  - intentos_login: registro de ingresos (buenos y fallidos). Con 5 claves
    mal en 15 minutos para un mismo usuario, o 20 desde una misma IP, se
    bloquea el login por un rato (frena a quien prueba claves a lo bruto).

Primer arranque / migración: si la tabla usuarios_panel está vacía, se crean
los usuarios que estaban en las variables viejas PANEL_USER/PANEL_PASSWORD y
PANEL_USERS (como administradores, con sus mismas claves) — así nadie se queda
afuera con el cambio. Después de eso esas variables ya no se usan y conviene
borrarlas de Railway.

Recuperación: si alguna vez nadie puede entrar (ej. se olvidaron todas las
claves de administrador), se define en Railway PANEL_RECUPERAR="usuario:clave"
y se redeploya: al arrancar, ese usuario queda activo, administrador y con esa
clave (y se le pide cambiarla al entrar). Después hay que borrar la variable.
"""

import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

COOKIE = "panel_sesion"
DURACION_RECORDAR = timedelta(days=30)   # con "Mantener la sesión iniciada"
DURACION_NORMAL = timedelta(hours=12)    # sin tildarlo (y la cookie muere al cerrar el navegador)
INACTIVIDAD_MAX = timedelta(days=14)     # sesión sin uso por más de esto -> se cierra

VENTANA_INTENTOS = timedelta(minutes=15)
MAX_FALLOS_USUARIO = 5
MAX_FALLOS_IP = 20
MAX_FALLOS_USUARIO_TOTAL = 30

LARGO_MIN_CLAVE = 10

# scrypt: ~16 MB y ~50 ms por hash. Suficientemente caro para que probar claves
# robadas sea lento, y barato para un login de vez en cuando.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


# ── Claves ──────────────────────────────────────────────────────────────
def hashear_clave(clave: str) -> str:
    sal = secrets.token_bytes(16)
    h = hashlib.scrypt(clave.encode("utf-8"), salt=sal, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${sal.hex()}${h.hex()}"


def verificar_clave(clave: str, guardado: str) -> bool:
    try:
        algo, n, r, p, sal, h = guardado.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(clave.encode("utf-8"), salt=bytes.fromhex(sal), n=int(n), r=int(r), p=int(p),
                              dklen=len(bytes.fromhex(h)))
        return hmac.compare_digest(calc, bytes.fromhex(h))
    except (ValueError, TypeError):
        return False


# Hash de una clave cualquiera: cuando el usuario no existe igual se calcula un
# scrypt, para que el tiempo de respuesta no delate qué usuarios son válidos.
_HASH_FALSO = hashear_clave(secrets.token_urlsafe(16))


def problema_clave(clave: str, usuario: str = "") -> Optional[str]:
    """Devuelve un texto con el problema, o None si la clave sirve."""
    if len(clave or "") < LARGO_MIN_CLAVE:
        return f"La clave tiene que tener al menos {LARGO_MIN_CLAVE} caracteres"
    if len(clave) > 200:
        return "La clave es demasiado larga"
    if usuario and usuario.lower() in clave.lower():
        return "La clave no puede contener el nombre de usuario"
    if len(set(clave)) < 4:
        return "La clave es demasiado simple"
    return None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ip_cliente(request: Request) -> str:
    # Railway pone la IP real del visitante en X-Real-IP (y la agrega al final
    # de X-Forwarded-For). No se usa el primer valor de X-Forwarded-For porque
    # ese lo puede inventar el que hace el pedido (y así esquivar el bloqueo).
    ip = request.headers.get("x-real-ip", "").strip()
    if not ip:
        xff = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
        ip = xff[-1] if xff else (request.client.host if request.client else "")
    return ip[:64]


# ── Tablas ──────────────────────────────────────────────────────────────
def crear_tablas(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios_panel (
            usuario             TEXT PRIMARY KEY,
            clave_hash          TEXT NOT NULL,
            es_admin            BOOLEAN NOT NULL DEFAULT FALSE,
            activo              BOOLEAN NOT NULL DEFAULT TRUE,
            debe_cambiar_clave  BOOLEAN NOT NULL DEFAULT FALSE,
            creado_en           TIMESTAMPTZ NOT NULL DEFAULT now(),
            creado_por          TEXT,
            clave_cambiada_en   TIMESTAMPTZ,
            ultimo_ingreso      TIMESTAMPTZ
        );
        CREATE TABLE IF NOT EXISTS sesiones_panel (
            token_hash        TEXT PRIMARY KEY,
            usuario           TEXT NOT NULL REFERENCES usuarios_panel(usuario) ON DELETE CASCADE,
            creada_en         TIMESTAMPTZ NOT NULL DEFAULT now(),
            ultima_actividad  TIMESTAMPTZ NOT NULL DEFAULT now(),
            expira_en         TIMESTAMPTZ NOT NULL,
            recordar          BOOLEAN NOT NULL DEFAULT FALSE,
            ip                TEXT,
            dispositivo       TEXT
        );
        CREATE INDEX IF NOT EXISTS sesiones_panel_usuario ON sesiones_panel (usuario);
        CREATE TABLE IF NOT EXISTS intentos_login (
            id       BIGSERIAL PRIMARY KEY,
            usuario  TEXT,
            ip       TEXT,
            exito    BOOLEAN NOT NULL,
            motivo   TEXT,
            cuando   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS intentos_login_cuando ON intentos_login (cuando);
        """
    )


def usuarios_de_variables_viejas() -> dict:
    """Lee PANEL_USER/PANEL_PASSWORD y PANEL_USERS (formato viejo)."""
    usuarios = {}
    u, c = os.environ.get("PANEL_USER"), os.environ.get("PANEL_PASSWORD")
    if u and c:
        usuarios[u.strip()] = c
    for par in os.environ.get("PANEL_USERS", "").split(","):
        usuario, _, clave = par.strip().partition(":")
        if usuario.strip() and clave:
            usuarios[usuario.strip()] = clave
    return usuarios


def inicializar(cur):
    """Crea tablas, migra los usuarios viejos si la tabla está vacía y aplica
    PANEL_RECUPERAR si está definida. Idempotente."""
    crear_tablas(cur)
    cur.execute("SELECT COUNT(*) FROM usuarios_panel")
    if cur.fetchone()[0] == 0:
        viejos = usuarios_de_variables_viejas()
        for usuario, clave in viejos.items():
            cur.execute(
                """INSERT INTO usuarios_panel (usuario, clave_hash, es_admin, creado_por, clave_cambiada_en)
                   VALUES (%s, %s, TRUE, 'migración', now()) ON CONFLICT DO NOTHING""",
                (usuario, hashear_clave(clave)),
            )
        if viejos:
            print(f"[auth] migrados {len(viejos)} usuarios desde PANEL_USER/PANEL_USERS: {', '.join(sorted(viejos))}")
        else:
            print("[auth] ATENCIÓN: no hay usuarios. Definí PANEL_RECUPERAR=usuario:clave para crear el primero.")

    recuperar = os.environ.get("PANEL_RECUPERAR", "")
    usuario, _, clave = recuperar.partition(":")
    usuario = usuario.strip()
    if usuario and clave:
        cur.execute(
            """INSERT INTO usuarios_panel (usuario, clave_hash, es_admin, activo, debe_cambiar_clave, creado_por, clave_cambiada_en)
               VALUES (%s, %s, TRUE, TRUE, TRUE, 'PANEL_RECUPERAR', now())
               ON CONFLICT (usuario) DO UPDATE SET clave_hash = EXCLUDED.clave_hash, es_admin = TRUE,
                   activo = TRUE, debe_cambiar_clave = TRUE, clave_cambiada_en = now()""",
            (usuario, hashear_clave(clave)),
        )
        cur.execute("DELETE FROM sesiones_panel WHERE usuario = %s", (usuario,))
        print(f"[auth] PANEL_RECUPERAR aplicado para '{usuario}'. Borrá la variable de Railway.")

    # Limpieza: sesiones vencidas e intentos de más de 90 días.
    cur.execute("DELETE FROM sesiones_panel WHERE expira_en < now() OR ultima_actividad < now() - %s",
                (INACTIVIDAD_MAX,))
    cur.execute("DELETE FROM intentos_login WHERE cuando < now() - interval '90 days'")


# ── Caché corta de sesiones ───────────────────────────────────────────────
# Cada pantalla hace varios pedidos a la API; sin esto, cada uno abriría una
# conexión extra a Postgres solo para validar la sesión. Se guarda 30 s y se
# invalida al instante cuando se cierra sesión / se desactiva un usuario en
# este mismo proceso (Railway corre una sola instancia del panel).
_CACHE_TTL = 30
_cache: dict = {}
_cache_lock = threading.Lock()


def _cache_get(th):
    with _cache_lock:
        item = _cache.get(th)
        if item and item[0] > time.monotonic():
            return item[1]
        _cache.pop(th, None)
        return None


def _cache_set(th, datos):
    with _cache_lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[th] = (time.monotonic() + _CACHE_TTL, datos)


def olvidar_cache(usuario: Optional[str] = None):
    """Sin usuario: borra toda la caché. Con usuario: solo sus sesiones."""
    with _cache_lock:
        if usuario is None:
            _cache.clear()
        else:
            for k in [k for k, v in _cache.items() if v[1]["usuario"] == usuario]:
                _cache.pop(k, None)


# ── Sesiones ────────────────────────────────────────────────────────────
def sesion_de_request(request: Request, conexion) -> Optional[dict]:
    """{usuario, es_admin, debe_cambiar_clave, token_hash} o None."""
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 200:
        return None
    th = _hash_token(token)
    datos = _cache_get(th)
    if datos:
        return datos
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.usuario, u.es_admin, u.debe_cambiar_clave,
                       s.ultima_actividad < now() - interval '5 minutes'
                FROM sesiones_panel s JOIN usuarios_panel u ON u.usuario = s.usuario
                WHERE s.token_hash = %s AND s.expira_en > now() AND u.activo
                  AND s.ultima_actividad > now() - %s
                """,
                (th, INACTIVIDAD_MAX),
            )
            fila = cur.fetchone()
            if not fila:
                return None
            if fila[3]:  # se actualiza la última actividad cada 5 min como mucho
                cur.execute("UPDATE sesiones_panel SET ultima_actividad = now(), ip = %s WHERE token_hash = %s",
                            (ip_cliente(request), th))
                conn.commit()
    datos = {"usuario": fila[0], "es_admin": fila[1], "debe_cambiar_clave": fila[2], "token_hash": th}
    _cache_set(th, datos)
    return datos


def bloqueo_vigente(cur, usuario: str, ip: str) -> Optional[int]:
    """Minutos que faltan si el login está bloqueado por demasiados intentos.

    Tres topes dentro de la ventana de 15 min:
      - 5 fallos para ese usuario desde esa misma conexión (lo normal: alguien
        probando claves),
      - 20 fallos desde esa conexión con cualquier usuario,
      - 30 fallos para ese usuario desde cualquier lado (ataque repartido).
    Así, alguien de afuera probando claves no deja bloqueado al usuario real
    que entra desde otra conexión (salvo un ataque grande y repartido)."""
    cur.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE lower(usuario) = lower(%s) AND ip = %s),
          COUNT(*) FILTER (WHERE ip = %s),
          COUNT(*) FILTER (WHERE lower(usuario) = lower(%s)),
          MAX(cuando)
        FROM intentos_login
        WHERE NOT exito AND motivo IS DISTINCT FROM 'bloqueado' AND cuando > now() - %s
          AND (ip = %s OR lower(usuario) = lower(%s))
        """,
        (usuario, ip, ip, usuario, VENTANA_INTENTOS, ip, usuario),
    )
    usuario_ip, solo_ip, solo_usuario, ultimo = cur.fetchone()
    if usuario_ip >= MAX_FALLOS_USUARIO or solo_ip >= MAX_FALLOS_IP or solo_usuario >= MAX_FALLOS_USUARIO_TOTAL:
        resta = (ultimo + VENTANA_INTENTOS) - datetime.now(timezone.utc) if ultimo else VENTANA_INTENTOS
        return max(1, int(resta.total_seconds() // 60) + 1)
    return None


def intentar_login(cur, usuario: str, clave: str, ip: str) -> tuple:
    """(fila_usuario | None, motivo_error | None). No crea la sesión."""
    usuario = (usuario or "").strip()[:100]
    minutos = bloqueo_vigente(cur, usuario, ip)
    if minutos:
        cur.execute("INSERT INTO intentos_login (usuario, ip, exito, motivo) VALUES (%s, %s, FALSE, 'bloqueado')",
                    (usuario, ip))
        return None, f"Demasiados intentos fallidos. Probá de nuevo en {minutos} min."
    cur.execute(
        "SELECT usuario, clave_hash, activo, es_admin, debe_cambiar_clave FROM usuarios_panel WHERE lower(usuario) = lower(%s)",
        (usuario,),
    )
    fila = cur.fetchone()
    ok = verificar_clave(clave or "", fila[1] if fila else _HASH_FALSO)
    if not fila or not ok or not fila[2]:
        motivo = "inactivo" if (fila and ok and not fila[2]) else "clave"
        cur.execute("INSERT INTO intentos_login (usuario, ip, exito, motivo) VALUES (%s, %s, FALSE, %s)",
                    (usuario, ip, motivo))
        return None, "Usuario o clave incorrectos"
    cur.execute("INSERT INTO intentos_login (usuario, ip, exito) VALUES (%s, %s, TRUE)", (fila[0], ip))
    cur.execute("UPDATE usuarios_panel SET ultimo_ingreso = now() WHERE usuario = %s", (fila[0],))
    return fila, None


def crear_sesion(cur, usuario: str, recordar: bool, request: Request) -> tuple:
    """Devuelve (token, max_age_segundos | None)."""
    token = secrets.token_urlsafe(32)
    duracion = DURACION_RECORDAR if recordar else DURACION_NORMAL
    cur.execute(
        """INSERT INTO sesiones_panel (token_hash, usuario, expira_en, recordar, ip, dispositivo)
           VALUES (%s, %s, now() + %s, %s, %s, %s)""",
        (_hash_token(token), usuario, duracion, recordar, ip_cliente(request),
         (request.headers.get("user-agent") or "")[:300]),
    )
    return token, (int(duracion.total_seconds()) if recordar else None)


def poner_cookie(respuesta, request: Request, token: str, max_age: Optional[int]):
    seguro = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    respuesta.set_cookie(COOKIE, token, max_age=max_age, httponly=True, secure=seguro,
                         samesite="lax", path="/")


def borrar_cookie(respuesta):
    respuesta.delete_cookie(COOKIE, path="/")


# ── Protección CSRF ─────────────────────────────────────────────────────
def origen_valido(request: Request) -> bool:
    """Para pedidos que cambian datos: si el navegador manda Origin (o, en su
    defecto, Referer), tiene que ser este mismo sitio. La cookie SameSite=Lax
    ya evita lo grueso; esto es una segunda capa."""
    origen = request.headers.get("origin") or request.headers.get("referer")
    if not origen:
        return True  # clientes que no son navegadores (curl, scripts)
    host_origen = (urlsplit(origen).netloc or "").lower()
    hosts = {(request.headers.get("host") or "").lower(), (request.headers.get("x-forwarded-host") or "").lower()}
    return host_origen in hosts


def describir_dispositivo(ua: str) -> str:
    ua = ua or ""
    nav = next((n for k, n in [("Edg/", "Edge"), ("OPR/", "Opera"), ("Chrome/", "Chrome"),
                                ("Firefox/", "Firefox"), ("Safari/", "Safari")] if k in ua), "Navegador")
    so = next((n for k, n in [("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                               ("Windows", "Windows"), ("Mac OS X", "Mac"), ("Linux", "Linux")] if k in ua), "")
    return f"{nav} en {so}" if so else nav


def no_autorizado(detalle: str = "Tenés que iniciar sesión"):
    return HTTPException(status_code=401, detail=detalle)
