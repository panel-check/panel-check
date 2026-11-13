"""
Avisos por Telegram (bot @smartiesmarcasbot), separados por temas.

El bot manda todo a UN grupo de Telegram con «Temas» activados (12/11/2026): cada clase de aviso va a su
propio tema, para que no se mezcle todo en un mismo chat. El bot crea los temas solo, la primera vez que
los necesita (tiene que ser administrador del grupo con el permiso «Gestionar temas»). Si el chat no es un
grupo con temas (por ejemplo, un chat privado con el bot), todo va al chat sin separar.

Temas (TEMAS): corridas · oposiciones · boletines · sistema · resumen · vigilancia. Para mandar algo desde otro script:

    from telegram_avisos import enviar
    enviar("texto en HTML", tema="sistema")

Configuración: secret TELEGRAM_BOT_TOKEN (el token de @BotFather). El chat sale del secret opcional
TELEGRAM_CHAT_ID o, si no está, del último chat donde alguien le escribió /start al bot; se guarda en la
tabla reportes_config («telegram_chat_id»), junto con el número de cada tema («telegram_tema:<chat>:<tema>»).
Si alguien borra un tema, el bot lo vuelve a crear en el próximo aviso.

Nunca rompe al que lo llama: si algo falla, lo avisa en el log y devuelve False.
"""

import html
import os
import re

import requests

# clave → (nombre del tema, color del ícono: uno de los 6 que acepta Telegram)
TEMAS = {
    "corridas": ("🔄 Corridas", 7322096),        # azul
    "oposiciones": ("⚠️ Oposiciones y vistas", 16766590),  # amarillo
    "boletines": ("📰 Boletines", 9367192),      # verde
    "sistema": ("🚨 Sistema", 16478047),         # rojo
    "resumen": ("📊 Resumen", 13338331),         # violeta
    "vigilancia": ("🔎 Vigilancia marcaria", 16749490),  # rosa
}

DESCRIPCION_TEMAS = {
    "corridas": "el reporte de cada proceso al terminar: si falló, cuánto tardó, leads, oposiciones y vistas.",
    "oposiciones": "oposiciones y vistas nuevas en leads, apenas aparecen.",
    "boletines": "cuando el INPI publica un boletín de marcas nuevas.",
    "sistema": "bloqueos de INPI, cupo de Resend, fallas del deploy o del programador.",
    "resumen": "el resumen diario o semanal.",
    "vigilancia": "alertas ALTAS de la vigilancia marcaria (marca parecida a la de un cliente), apenas aparecen.",
}

CREAR_TABLA = "CREATE TABLE IF NOT EXISTS reportes_config (clave TEXT PRIMARY KEY, valor TEXT)"


def limpiar_token(token) -> str:
    """El token tal como lo da @BotFather («123456:ABC…»), aunque lo hayan pegado con espacios, comillas,
    «bot» adelante o la URL entera de la API."""
    t = (token or "").strip().strip("'\"").strip()
    m = re.search(r"(\d{6,}:[A-Za-z0-9_-]{20,})", t)
    return m.group(1) if m else t


def _tg(metodo, **datos):
    token = limpiar_token(os.environ.get("TELEGRAM_BOT_TOKEN"))
    return requests.post(f"https://api.telegram.org/bot{token}/{metodo}", json=datos, timeout=30)


def chat_de_actualizaciones(actualizaciones: list):
    """El chat más reciente que le habló al bot: un /start (o cualquier mensaje) en privado o en un grupo,
    o el aviso de que lo sumaron a un grupo. Un /start gana sobre los demás. Si un grupo pasó a supergrupo
    (pasa al activar Temas), se usa el número nuevo."""
    con_start = cualquiera = None
    migrado = {}
    for u in actualizaciones:
        msg = u.get("message") or u.get("channel_post") or u.get("edited_message") or {}
        miembro = u.get("my_chat_member") or {}
        chat = msg.get("chat") or miembro.get("chat")
        if not chat or chat.get("id") is None:
            continue
        if msg.get("migrate_to_chat_id"):
            migrado[str(chat["id"])] = str(msg["migrate_to_chat_id"])
        if miembro and (miembro.get("new_chat_member") or {}).get("status") in ("left", "kicked"):
            continue
        cualquiera = str(chat["id"])
        if (msg.get("text") or "").strip().lower().startswith("/start"):
            con_start = str(chat["id"])
    elegido = con_start or cualquiera
    while elegido in migrado:
        elegido = migrado.pop(elegido)
    return elegido


# ── Configuración guardada ─────────────────────────────────────────────────────────────────────────

def _conectar():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        return None
    import psycopg2
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


def _leer(conn, clave):
    if conn is None:
        return None
    with conn.cursor() as cur:
        cur.execute(CREAR_TABLA)
        cur.execute("SELECT valor FROM reportes_config WHERE clave = %s", (clave,))
        fila = cur.fetchone()
        return fila[0] if fila else None


def _guardar(conn, clave, valor):
    if conn is None:
        return
    with conn.cursor() as cur:
        cur.execute(CREAR_TABLA)
        cur.execute("""INSERT INTO reportes_config (clave, valor) VALUES (%s, %s)
                       ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor""", (clave, valor))


def chat_id(conn):
    """El chat al que se manda: el secret TELEGRAM_CHAT_ID, o el último chat que le escribió /start al bot
    (se guarda), o el guardado."""
    if os.environ.get("TELEGRAM_CHAT_ID"):
        return os.environ["TELEGRAM_CHAT_ID"].strip()
    guardado = _leer(conn, "telegram_chat_id")
    try:
        yo = _tg("getMe").json()
        print(f"Bot de Telegram: @{(yo.get('result') or {}).get('username') or '?'} "
              f"({'ok' if yo.get('ok') else yo.get('description')})")
    except Exception:
        pass
    try:
        resp = _tg("getUpdates").json()
    except Exception as ex:
        print(f"::warning::No se pudo leer el bot de Telegram ({type(ex).__name__})")
        resp = {}
    if not resp.get("ok", True):
        print(f"::warning::Telegram respondió: {resp.get('description')}")
    actualizaciones = resp.get("result", [])
    nuevo = chat_de_actualizaciones(actualizaciones)
    print(f"Telegram: {len(actualizaciones)} mensaje(s) pendientes en el bot; chat guardado: {'sí' if guardado else 'no'}.")
    if nuevo and nuevo != guardado:
        _guardar(conn, "telegram_chat_id", nuevo)
        print(f"Chat de Telegram detectado por /start y guardado ({'grupo' if nuevo.startswith('-') else 'privado'}).")
    return nuevo or guardado


def es_grupo_con_temas(chat) -> bool:
    try:
        datos = _tg("getChat", chat_id=chat).json().get("result") or {}
    except Exception:
        return False
    return bool(datos.get("is_forum"))


def tema_id(conn, chat, tema, crear=True):
    """El número del tema (message_thread_id) en ese grupo; lo crea si no existe. None si no hay temas."""
    if tema not in TEMAS:
        tema = "sistema"
    clave = f"telegram_tema:{chat}:{tema}"
    guardado = _leer(conn, clave)
    if guardado:
        return int(guardado)
    if not crear or not es_grupo_con_temas(chat):
        return None
    nombre, color = TEMAS[tema]
    r = _tg("createForumTopic", chat_id=chat, name=nombre, icon_color=color).json()
    if not r.get("ok"):
        print(f"::warning::No se pudo crear el tema «{nombre}»: {r.get('description')} "
              f"(¿el bot es administrador con permiso para gestionar temas?)")
        return None
    numero = r["result"]["message_thread_id"]
    _guardar(conn, clave, str(numero))
    print(f"Tema de Telegram creado: {nombre}")
    return numero


def _mandar(chat, texto, hilo):
    extra = {"message_thread_id": hilo} if hilo else {}
    r = _tg("sendMessage", chat_id=chat, text=texto, parse_mode="HTML", disable_web_page_preview=True, **extra)
    if r.status_code != 200 and "thread" not in r.text.lower():
        # Si el HTML no le gustó a Telegram, se manda en texto plano antes que perder el aviso.
        plano = html.unescape(re.sub(r"<[^>]+>", "", texto))
        r = _tg("sendMessage", chat_id=chat, text=plano, disable_web_page_preview=True, **extra)
    return r


def enviar(texto, tema="corridas", conn=None) -> bool:
    """Manda `texto` (HTML de Telegram) al tema `tema` del grupo. True si salió."""
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("::warning::Falta el secret TELEGRAM_BOT_TOKEN: el aviso no se mandó.")
        return False
    propia = False
    try:
        if conn is None:
            try:
                conn = _conectar()
                propia = conn is not None
            except Exception as ex:
                print(f"::warning::Sin base para la configuración de Telegram ({type(ex).__name__})")
        chat = chat_id(conn)
        if not chat:
            print("::warning::No hay chat de Telegram: escribí /start en el grupo del bot.")
            return False
        hilo = tema_id(conn, chat, tema)
        r = _mandar(chat, texto, hilo)
        if r.status_code != 200 and hilo and "thread" in r.text.lower():
            # Borraron el tema: se crea de nuevo y se reintenta.
            _guardar(conn, f"telegram_tema:{chat}:{tema}", "")
            hilo = tema_id(conn, chat, tema)
            r = _mandar(chat, texto, hilo)
        print(f"Telegram ({tema}): HTTP {r.status_code}")
        return r.status_code == 200
    except Exception as ex:
        print(f"::warning::No se pudo mandar el aviso por Telegram ({type(ex).__name__})")
        return False
    finally:
        if propia:
            conn.close()


def preparar_temas(conn=None) -> bool:
    """Crea todos los temas (si el grupo los tiene activados) y deja un mensaje de bienvenida en cada uno."""
    ok = True
    for clave, (nombre, _c) in TEMAS.items():
        ok &= enviar(f"<b>{html.escape(nombre)}</b>\nAcá llega: {html.escape(DESCRIPCION_TEMAS[clave])}",
                     tema=clave, conn=conn)
    return ok
