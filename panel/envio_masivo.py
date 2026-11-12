"""
Envío masivo y escalonado del mail de oposición / vista (04/11/2026).

Pedido: desde el listado de Leads, con varias marcas seleccionadas, mandar de una vez el mail
«Recibió una oposición» (o «Recibió una vista») a todas las que corresponda, pero que NO salgan todos
juntos: se reparten en el tiempo, de a uno, como si los mandara una persona.

Cómo funciona:
  - Se arma un «lote» (tabla `mails_lotes`) con sus reglas: cada cuántos minutos sale uno (con una
    pequeña variación al azar para que no parezca un robot), en qué franja horaria (hora Argentina),
    si solo de lunes a viernes y cuántos como máximo por día.
  - Cada mail se arma en el momento de programarlo (plantilla guardada + datos reales de la marca, igual
    que el envío de la ficha) y se guarda en la cola de mails (`mails_cola`, ver mails_cola.py) con la
    hora en que le toca salir (`intentar_desde`). El hilo de la cola, que pasa cada minuto, lo manda
    cuando llega su hora. También queda la fila en `mails_envios`, así la ficha muestra «⏳ En cola» y
    el mismo mail no se programa ni se manda dos veces.
  - Justo antes de mandar cada uno se vuelve a mirar si todavía corresponde (sigue siendo lead, la
    oposición o vista no se marcó como contestada, no se volvió cliente, no se dio de baja). Si no, ese
    mail se cancela solo.
  - Cuando sale de verdad: queda como gestión «Mail» en el CRM y el acta como contactada (lo mismo que
    al mandarlo desde la ficha).
  - Si igual se choca con el cupo diario de Resend (100 por día en el plan free), el resto del lote se
    reprograma desde el día siguiente con las mismas reglas: nunca salen todos de golpe a las 21:02.
  - El lote se puede pausar, reanudar (vuelve a calcular los horarios desde ese momento) o cancelar.

Sin FastAPI: lo usan app.py (rutas /api/masivo/mail-oposicion y /api/mails/lotes) y las pruebas.
"""

import datetime as _dt
import random
from zoneinfo import ZoneInfo

import psycopg2.extras

import mails_core as mc
import mails_cola

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")

CLAVES = ("prospecto_oposicion", "prospecto_vista")
CUENTA = "prospectos"
# Tope del día para todos los mails a prospectos (lote + los de a mano): deja margen bajo los 100 de Resend.
LIMITE_GLOBAL_DIA = 95
# La franja termina a las 21:00 como máximo: así cada día de envío cae entero dentro del mismo día de cupo
# de Resend (que se reinicia a las 21:00 hora Argentina = medianoche UTC).
HORA_MAXIMA = 21

PREDETERMINADOS = {
    "intervalo_minutos": 4,
    "variar": True,
    "hora_desde": 9,
    "hora_hasta": 19,
    "solo_habiles": True,
    "max_por_dia": 60,
    "incluir_contactadas": False,
}

MOTIVOS = {
    "no_existe": "No existe en la base",
    "sin_pendiente": "No tiene una oposición o vista pendiente para ofrecer ayuda",
    "no_lead": "No es lead (tiene gestor/apoderado)",
    "no_contactar": "Está marcada «No contactar»",
    "cliente": "Ya es cliente",
    "sin_mail": "No tiene mail cargado",
    "mail_invalido": "El mail cargado no es válido",
    "baja": "El mail está en la lista de bajas",
    "ya_enviado": "Ya se le mandó (o ya está programado) ese mail",
    "contactada": "Ya está contactada",
    "repetida": "Mismo mail y misma marca que otra de la selección",
}


# ── Parámetros ──────────────────────────────────────────────────────────────

def normalizar_parametros(p: dict | None) -> dict:
    """Completa con los valores por defecto y controla los rangos. Levanta ValueError con un texto claro."""
    p = {**PREDETERMINADOS, **{k: v for k, v in (p or {}).items() if v is not None}}
    try:
        out = {
            "intervalo_minutos": int(p["intervalo_minutos"]),
            "variar": bool(p["variar"]),
            "hora_desde": int(p["hora_desde"]),
            "hora_hasta": int(p["hora_hasta"]),
            "solo_habiles": bool(p["solo_habiles"]),
            "max_por_dia": int(p["max_por_dia"]),
            "incluir_contactadas": bool(p["incluir_contactadas"]),
        }
    except (TypeError, ValueError):
        raise ValueError("Revisá los números del envío (minutos, horas y máximo por día).")
    if not 1 <= out["intervalo_minutos"] <= 240:
        raise ValueError("El intervalo tiene que ser de 1 a 240 minutos.")
    if not 0 <= out["hora_desde"] < out["hora_hasta"] <= HORA_MAXIMA:
        raise ValueError(f"La franja horaria tiene que empezar antes de terminar y terminar a las {HORA_MAXIMA}:00 como máximo "
                         f"(a esa hora se reinicia el cupo diario de Resend).")
    if not 1 <= out["max_por_dia"] <= LIMITE_GLOBAL_DIA:
        raise ValueError(f"El máximo por día tiene que ser de 1 a {LIMITE_GLOBAL_DIA} (Resend permite 100 por día).")
    inicio = p.get("inicio")
    out["inicio"] = None
    if inicio:
        try:
            d = _dt.datetime.fromisoformat(str(inicio))
        except ValueError:
            raise ValueError("La fecha y hora de inicio no es válida.")
        out["inicio"] = (d if d.tzinfo else d.replace(tzinfo=TZ_AR)).astimezone(TZ_AR)
    return out


# ── Cálculo de horarios (puro, sin base) ───────────────────────────────────

def _dia_permitido(d: _dt.date, solo_habiles: bool) -> bool:
    return not solo_habiles or d.weekday() < 5


def calcular_horarios(n: int, params: dict, ahora: _dt.datetime, ocupados: dict | None = None,
                      rng: random.Random | None = None) -> list:
    """Horarios (datetime con zona horaria Argentina) para `n` mails, en orden.

    Reglas: el primero sale en el próximo minuto válido desde `ahora` (o desde params["inicio"] si es
    posterior); entre uno y otro pasan `intervalo_minutos` (hasta un 50 % más si `variar`); solo dentro de
    [hora_desde, hora_hasta) hora Argentina; si `solo_habiles`, de lunes a viernes; por día, como mucho
    `max_por_dia` de este lote y, sumando lo que ya ocupa ese día (`ocupados`: {fecha: cantidad} de mails
    ya mandados o programados), como mucho LIMITE_GLOBAL_DIA."""
    rng = rng or random.Random()
    ocupados = dict(ocupados or {})
    intervalo = params["intervalo_minutos"]
    desde_h, hasta_h = params["hora_desde"], params["hora_hasta"]
    t = ahora.astimezone(TZ_AR)
    if params.get("inicio") and params["inicio"] > t:
        t = params["inicio"]
    # al minuto siguiente si tiene segundos: el hilo de la cola pasa una vez por minuto
    if t.second or t.microsecond:
        t = (t + _dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
    propios: dict = {}
    salida = []

    def inicio_de(d):
        return _dt.datetime(d.year, d.month, d.day, desde_h, tzinfo=TZ_AR)

    for _ in range(n):
        for _guardia in range(800):
            d = t.date()
            if not _dia_permitido(d, params["solo_habiles"]):
                t = inicio_de(d + _dt.timedelta(days=1))
                continue
            if t.hour < desde_h:
                t = inicio_de(d)
                continue
            if t.hour >= hasta_h:
                t = inicio_de(d + _dt.timedelta(days=1))
                continue
            if propios.get(d, 0) >= params["max_por_dia"] or propios.get(d, 0) + ocupados.get(d, 0) >= LIMITE_GLOBAL_DIA:
                t = inicio_de(d + _dt.timedelta(days=1))
                continue
            break
        else:  # pragma: no cover — con parámetros validados no pasa
            raise ValueError("No se pudieron calcular los horarios del envío.")
        salida.append(t)
        propios[t.date()] = propios.get(t.date(), 0) + 1
        paso = intervalo
        if params.get("variar"):
            paso += rng.randint(0, max(0, intervalo // 2))
        t = t + _dt.timedelta(minutes=paso)
    return salida


def ordenar_espaciando(items: list, clave=lambda x: x["email"]) -> list:
    """Mantiene el orden, pero si un mismo destinatario tiene varias marcas, la segunda va después de todos
    los primeros, la tercera después de todos los segundos, etc.: no le llegan dos mails seguidos."""
    vistos: dict = {}
    marcados = []
    for i, it in enumerate(items):
        k = (clave(it) or "").lower()
        vistos[k] = vistos.get(k, 0) + 1
        marcados.append((vistos[k], i, it))
    return [it for _, _, it in sorted(marcados, key=lambda x: (x[0], x[1]))]


def resumen_horarios(horarios: list) -> dict:
    if not horarios:
        return {"primero": None, "ultimo": None, "dias": 0, "por_dia": []}
    por_dia: dict = {}
    for h in horarios:
        por_dia.setdefault(h.date(), []).append(h)
    return {
        "primero": horarios[0].isoformat(),
        "ultimo": horarios[-1].isoformat(),
        "dias": len(por_dia),
        "por_dia": [{"fecha": d.isoformat(), "cantidad": len(hs), "desde": hs[0].isoformat(), "hasta": hs[-1].isoformat()}
                    for d, hs in sorted(por_dia.items())],
    }


# ── Base de datos ───────────────────────────────────────────────────────────

def crear_tablas(cur):
    """Idempotente. Va después de mails_cola.crear_tablas."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mails_lotes (
            id           SERIAL PRIMARY KEY,
            creado_por   TEXT,
            creado_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
            parametros   JSONB NOT NULL,
            estado       TEXT NOT NULL DEFAULT 'activo',
            total        INT NOT NULL DEFAULT 0,
            descripcion  TEXT,
            actualizado_en TIMESTAMPTZ,
            actualizado_por TEXT
        )
        """
    )
    cur.execute("ALTER TABLE mails_cola ADD COLUMN IF NOT EXISTS lote_id INT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mails_cola_lote ON mails_cola(lote_id) WHERE lote_id IS NOT NULL")


def ocupados_por_dia(cur, excluir_lote: int = None) -> dict:
    """{fecha Argentina: cantidad} de mails a prospectos que esperan en la cola, por el día en que salen."""
    filtro = "AND (lote_id IS NULL OR lote_id <> %s)" if excluir_lote else ""
    cur.execute(f"""SELECT (intentar_desde AT TIME ZONE 'America/Argentina/Buenos_Aires')::date AS dia, COUNT(*) AS n
                      FROM mails_cola WHERE estado = 'pendiente' AND cuenta = %s {filtro}
                     GROUP BY 1""", [CUENTA] + ([excluir_lote] if excluir_lote else []))
    out = {}
    for f in cur.fetchall():
        dia, n = (f["dia"], f["n"]) if isinstance(f, dict) else (f[0], f[1])
        out[dia] = int(n)
    return out


def sumar_enviados_hoy(ocupados: dict, ahora: _dt.datetime, enviados_hoy: int | None) -> dict:
    """Le suma a hoy lo que Resend ya mandó (si se pudo consultar)."""
    if enviados_hoy:
        hoy = ahora.astimezone(TZ_AR).date()
        ocupados = {**ocupados, hoy: ocupados.get(hoy, 0) + int(enviados_hoy)}
    return ocupados


def enviados_hoy_resend() -> int | None:
    try:
        r = mc.contar_enviados_hoy(CUENTA)
        return int(r.get("enviados") or 0)
    except Exception:  # noqa: BLE001 — sin datos de Resend se calcula igual, solo con la cola
        return None


def armar_mail(cur, clave: str, acta: str, cfg: dict, panel_url: str) -> dict:
    """Mismo armado que el envío de la ficha (mails_api.enviar_a_lead), sin presupuesto adjunto."""
    datos = mc.datos_de_marca(cur, acta)
    if not datos:
        raise ValueError(f"No existe la marca con acta {acta}")
    simple = mc.es_simple(clave)
    token = mc.nuevo_token()
    datos = {**datos, "codigo": token}
    asunto, html, texto = mc.render_prospecto(cfg["asunto"], cfg["cuerpo"], datos, cfg.get("encabezado"), simple=simple)
    if not simple:
        html, texto = mc.rastrear_links(cur, html, texto, token, panel_url)
    return {"asunto": asunto, "html": html, "texto": texto, "token": token}


def programar(cur, *, items: list, params: dict, usuario: str, panel_url: str, ahora: _dt.datetime = None,
              enviados_hoy: int | None = None, rng: random.Random | None = None) -> dict:
    """Crea el lote y deja cada mail en la cola con su hora. `items`: [{acta, email, clave}] ya filtrados y
    ordenados. No hace commit. Devuelve {lote_id, programados, resumen}."""
    ahora = ahora or _dt.datetime.now(_dt.timezone.utc)
    ocup = sumar_enviados_hoy(ocupados_por_dia(cur), ahora, enviados_hoy)
    horarios = calcular_horarios(len(items), params, ahora, ocup, rng)
    guardar = {k: (v.isoformat() if isinstance(v, _dt.datetime) else v) for k, v in params.items()}
    cur.execute("SELECT * FROM mails_config")
    guardadas = {f["clave"]: f for f in cur.fetchall()}
    cfgs = {c: mc.combinar(c, guardadas.get(c)) for c in {it["clave"] for it in items}}
    n_opo = sum(1 for it in items if it["clave"] == "prospecto_oposicion")
    descripcion = ", ".join(x for x in (f"{n_opo} de oposición" if n_opo else "",
                                        f"{len(items) - n_opo} de vista" if len(items) - n_opo else "") if x)
    cur.execute("INSERT INTO mails_lotes (creado_por, parametros, total, descripcion) VALUES (%s, %s, %s, %s) RETURNING id",
                (usuario, psycopg2.extras.Json(guardar), len(items), descripcion))
    lote_id = _id(cur.fetchone())
    for it, hora in zip(items, horarios):
        cfg = cfgs[it["clave"]]
        m = armar_mail(cur, it["clave"], it["acta"], cfg, panel_url)
        cur.execute("INSERT INTO mails_envios (clave_mail, acta, para, asunto, resend_id, enviado_por, token) "
                    "VALUES (%s, %s, %s, %s, NULL, %s, %s) RETURNING id",
                    (it["clave"], it["acta"], it["email"], m["asunto"], usuario, m["token"]))
        envio_id = _id(cur.fetchone())
        cur.execute(
            """INSERT INTO mails_cola (cuenta, remitente, responder_a, para, asunto, html, texto, clave_mail, acta, origen,
                                       encolado_por, al_enviar, motivo, intentar_desde, lote_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'masivo', %s, %s, 'envio_masivo', %s, %s) RETURNING id""",
            (cfg["cuenta"], cfg["remitente"], cfg["responder_a"] or None, psycopg2.extras.Json([it["email"]]), m["asunto"],
             m["html"], m["texto"], it["clave"], it["acta"], usuario,
             psycopg2.extras.Json({"tabla": "mails_envios", "id": envio_id}), hora, lote_id))
        cola_id = _id(cur.fetchone())
        cur.execute("UPDATE mails_envios SET cola_id = %s WHERE id = %s", (cola_id, envio_id))
    return {"lote_id": lote_id, "programados": len(items), "resumen": resumen_horarios(horarios)}


def _id(fila):
    return fila["id"] if isinstance(fila, dict) else fila[0]


def _parametros_del_lote(cur, lote_id: int) -> dict | None:
    cur.execute("SELECT parametros FROM mails_lotes WHERE id = %s", (lote_id,))
    f = cur.fetchone()
    if not f:
        return None
    p = dict((f["parametros"] if isinstance(f, dict) else f[0]) or {})
    p.pop("inicio", None)
    return normalizar_parametros(p)


def reprogramar(cur, lote_id: int, desde: _dt.datetime, estados=("pendiente",), rng=None) -> int:
    """Vuelve a calcular los horarios de lo que falta del lote, desde `desde`, con sus reglas. Deja esas filas
    como 'pendiente'. Devuelve cuántas reprogramó."""
    params = _parametros_del_lote(cur, lote_id)
    if not params:
        return 0
    cur.execute("SELECT id FROM mails_cola WHERE lote_id = %s AND estado = ANY(%s) ORDER BY intentar_desde, id",
                (lote_id, list(estados)))
    ids = [_id(f) for f in cur.fetchall()]
    if not ids:
        return 0
    horarios = calcular_horarios(len(ids), params, desde, ocupados_por_dia(cur, excluir_lote=lote_id), rng)
    for i, h in zip(ids, horarios):
        cur.execute("UPDATE mails_cola SET intentar_desde = %s, estado = 'pendiente' WHERE id = %s", (h, i))
    return len(ids)


def al_chocar_con_cupo(cur, cuenta: str, reinicio: _dt.datetime) -> set:
    """Lo llama la cola cuando Resend dice que no hay más cupo hoy: cada lote con mails pendientes en esa
    cuenta se reprograma desde el reinicio del cupo con sus reglas (no salen todos juntos a las 21:02).
    Devuelve los ids de lote reprogramados."""
    cur.execute("SELECT DISTINCT lote_id FROM mails_cola WHERE estado = 'pendiente' AND cuenta = %s AND lote_id IS NOT NULL",
                (cuenta,))
    lotes = {_id_lote(f) for f in cur.fetchall()}
    for lote in sorted(lotes):
        reprogramar(cur, lote, reinicio)
    return lotes


def _id_lote(f):
    return f["lote_id"] if isinstance(f, dict) else f[0]


def pausar(cur, lote_id: int, usuario: str) -> int:
    cur.execute("UPDATE mails_cola SET estado = 'pausado' WHERE lote_id = %s AND estado = 'pendiente'", (lote_id,))
    n = cur.rowcount
    cur.execute("UPDATE mails_lotes SET estado = 'pausado', actualizado_en = now(), actualizado_por = %s WHERE id = %s",
                (usuario, lote_id))
    return n


def reanudar(cur, lote_id: int, usuario: str, ahora: _dt.datetime = None) -> int:
    n = reprogramar(cur, lote_id, ahora or _dt.datetime.now(_dt.timezone.utc), estados=("pausado", "pendiente"))
    cur.execute("UPDATE mails_lotes SET estado = 'activo', actualizado_en = now(), actualizado_por = %s WHERE id = %s",
                (usuario, lote_id))
    return n


def cancelar(cur, lote_id: int, usuario: str) -> int:
    """Cancela todo lo que falta del lote (no sale). Igual que cancelar de a uno: se borra el registro del
    envío, así la ficha no dice «ya se le mandó» un mail que nunca salió."""
    cur.execute("SELECT * FROM mails_cola WHERE lote_id = %s AND estado IN ('pendiente', 'pausado', 'error') FOR UPDATE",
                (lote_id,))
    filas = cur.fetchall()
    for f in filas:
        if not isinstance(f, dict):
            f = dict(zip([d[0] for d in cur.description], f))
        mails_cola._cancelar_fila(cur, f, f"Envío masivo cancelado por {usuario}.")
    cur.execute("UPDATE mails_lotes SET estado = 'cancelado', actualizado_en = now(), actualizado_por = %s WHERE id = %s",
                (usuario, lote_id))
    return len(filas)


def listar(cur) -> list:
    """Lotes activos o pausados, y los de los últimos 7 días, con sus cuentas."""
    cur.execute(
        """SELECT l.id, l.creado_por, l.creado_en, l.parametros, l.estado, l.total, l.descripcion,
                  l.actualizado_en, l.actualizado_por,
                  COUNT(*) FILTER (WHERE c.estado = 'enviado') AS enviados,
                  COUNT(*) FILTER (WHERE c.estado = 'pendiente') AS pendientes,
                  COUNT(*) FILTER (WHERE c.estado = 'pausado') AS pausados,
                  COUNT(*) FILTER (WHERE c.estado = 'cancelado') AS cancelados,
                  COUNT(*) FILTER (WHERE c.estado = 'error') AS errores,
                  MIN(c.intentar_desde) FILTER (WHERE c.estado = 'pendiente') AS proximo,
                  MAX(c.intentar_desde) FILTER (WHERE c.estado = 'pendiente') AS ultimo,
                  MAX(c.enviado_en) AS ultimo_enviado
             FROM mails_lotes l LEFT JOIN mails_cola c ON c.lote_id = l.id
            GROUP BY l.id
           HAVING l.estado IN ('activo', 'pausado') AND COUNT(*) FILTER (WHERE c.estado IN ('pendiente', 'pausado')) > 0
               OR l.creado_en > now() - interval '7 days'
            ORDER BY l.id DESC LIMIT 30""")
    filas = cur.fetchall()
    for f in filas:
        for k in ("creado_en", "actualizado_en", "proximo", "ultimo", "ultimo_enviado"):
            f[k] = f[k].isoformat() if f.get(k) else None
        # un lote «activo» sin nada pendiente ya terminó
        if f["estado"] == "activo" and not f["pendientes"] and not f["pausados"]:
            f["estado"] = "terminado"
    return filas


def detalle(cur, lote_id: int) -> list:
    cur.execute(
        """SELECT c.id, c.para, c.asunto, c.clave_mail, c.acta, c.estado, c.ultimo_error, c.intentar_desde, c.enviado_en,
                  COALESCE(NULLIF(m.denominacion_inpi, ''), m.denominacion) AS marca, m.titular
             FROM mails_cola c LEFT JOIN marcas m ON m.acta = c.acta
            WHERE c.lote_id = %s ORDER BY c.intentar_desde, c.id""", (lote_id,))
    filas = cur.fetchall()
    for f in filas:
        for k in ("intentar_desde", "enviado_en"):
            f[k] = f[k].isoformat() if f.get(k) else None
        f["para"] = ", ".join(f["para"] or [])
    return filas
