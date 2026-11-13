"""
Avisos por Telegram a cada tema del grupo (12/11/2026).

Lo lanza el programador del panel cada 15 minutos (workflow avisos_telegram.yml). Mira la base —no consulta
INPI— y manda lo nuevo desde la pasada anterior al tema que corresponde (ver telegram_avisos.py):

  ⚠️ Oposiciones y vistas
     - oposiciones nuevas en leads que todavía sirven (oposicion_detectada_en);
     - vistas nuevas en leads que todavía sirven (vista_detectada_en), con tipo y vencimiento.
  📰 Boletines
     - INPI publicó un boletín nuevo (lo detecta el panel los miércoles de 7 a 11 en boletines_inpi):
       «salió el boletín N, todavía no está cargado»;
     - un boletín terminó de cargarse: marcas, leads, leads con email, los que ya teníamos por el escaneo.
  🚨 Sistema
     - el programador no pudo lanzar un proceso (programador_disparos con error, agotados los reintentos);
     - hay mails a prospectos esperando cupo de Resend (una vez por día).
     (Las corridas que fallan y el bloqueo masivo de INPI los manda reporte_corrida.py al terminar cada una.)
  🔎 Vigilancia marcaria (13/11/2026)
     - alertas de parecido de nivel ALTA, apenas aparecen: las manda la vigilancia al terminar cada corrida
       (scripts/vigilancia.py) y, por las dudas, esta pasada cada 15 minutos (la misma marca: nunca se repiten).
       Cuenta también las que pasaron a ALTA cuando las revisó el juez con IA.
  📊 Resumen
     - todos los días después de las 7:30: lo de las últimas 24 horas y lo que está pendiente;
     - los lunes, además, el de la semana.

Cada tipo de aviso guarda hasta cuándo ya avisó en reportes_config («aviso_desde:<tipo>»), así nunca se
repite ni se pierde nada aunque una pasada no corra. La primera vez arranca desde ese momento (no manda
el historial). En el log no se escriben nombres ni datos de personas: solo cantidades.

Uso: python3 avisos_telegram.py            (una pasada)
     python3 avisos_telegram.py --resumen  (manda el resumen del día ahora, aunque ya se haya mandado)
"""

import argparse
import datetime as _dt
import html
import os
import sys
from urllib.parse import quote
from zoneinfo import ZoneInfo

import telegram_avisos

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
PANEL = (os.environ.get("PANEL_URL") or "https://panel.registrodemimarca.com.ar").rstrip("/")
MAX_FILAS = 25
HORA_RESUMEN = _dt.time(7, 30)
MAX_INTENTOS_PROGRAMADOR = 8   # el mismo que panel/programador.py

TIPOS_VISTA = {
    "fondo": "de fondo", "formal": "formal (logos)", "fondo_y_formal": "de fondo y formal",
    "convenio_paris": "Convenio de París", "clasificacion": "formal (productos/servicios)",
}
ESTADOS_OPOSICION = {
    "sin_notificar": "sin notificar", "notificada_en_plazo": "notificada, plazo corriendo",
    "plazo_vencido": "plazo vencido", "vista_pendiente": "vista pendiente",
    "oposicion_sin_detalle": "sin detalle",
}

e = html.escape


# ── Utilidades ────────────────────────────────────────────────────────────────────────────────────

def link_acta(acta) -> str:
    return f"{PANEL}/?q={quote(str(acta))}&es_lead=todos&contactado=todos"


def fecha_txt(f) -> str:
    if not f:
        return ""
    if isinstance(f, _dt.datetime):
        f = f.astimezone(TZ).date()
    return f.strftime("%d/%m")


def num(v) -> str:
    return f"{int(v or 0):,}".replace(",", ".")


def lista(filas, armar, titulo) -> str:
    """Mensaje con una línea por fila; si son muchas, las primeras y «y N más»."""
    lineas = [titulo, ""] + [armar(f) for f in filas[:MAX_FILAS]]
    if len(filas) > MAX_FILAS:
        lineas.append(f"… y {len(filas) - MAX_FILAS} más (ver el panel).")
    return "\n".join(lineas)


def nombre(f) -> str:
    return (f.get("denominacion_inpi") or f.get("denominacion") or "(sin nombre)").strip()


# ── Marcas de hasta dónde se avisó ────────────────────────────────────────────────────────────────

def desde(cur, tipo, ahora):
    """Hasta cuándo ya se avisó este tipo. La primera vez: ahora (no se manda el historial)."""
    cur.execute("SELECT valor FROM reportes_config WHERE clave = %s", (f"aviso_desde:{tipo}",))
    fila = cur.fetchone()
    if fila and fila[0]:
        return _dt.datetime.fromisoformat(fila[0])
    marcar(cur, tipo, ahora)
    return None


def marcar(cur, tipo, valor):
    texto = valor.isoformat() if hasattr(valor, "isoformat") else str(valor)
    cur.execute("""INSERT INTO reportes_config (clave, valor) VALUES (%s, %s)
                   ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor""", (f"aviso_desde:{tipo}", texto))


def _filas(cur, sql, params):
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, f)) for f in cur.fetchall()]


# ── Oposiciones y vistas ──────────────────────────────────────────────────────────────────────────

def oposiciones_nuevas(cur, d, ahora):
    return _filas(cur, """
        SELECT acta, denominacion, denominacion_inpi, clase, boletin, estado_oposicion,
               oposicion_fecha_vencimiento, COALESCE(email, '') <> '' AS con_email,
               COALESCE(contactado, false) AS contactado
        FROM marcas
        WHERE es_lead AND oposicion_detectada_en > %s AND oposicion_detectada_en <= %s
          AND COALESCE(oposicion_sirve, true)
        ORDER BY oposicion_detectada_en""", (d, ahora))


def armar_oposicion(f) -> str:
    partes = [f'<a href="{e(link_acta(f["acta"]))}">{e(f["acta"])}</a> <b>{e(nombre(f))}</b>']
    if f.get("clase"):
        partes.append(f"clase {f['clase']}")
    if f.get("boletin"):
        partes.append(f"boletín {e(str(f['boletin']))}")
    estado = ESTADOS_OPOSICION.get(f.get("estado_oposicion") or "", "")
    if estado:
        partes.append(estado)
    if f.get("oposicion_fecha_vencimiento"):
        partes.append(f"vence {fecha_txt(f['oposicion_fecha_vencimiento'])}")
    partes.append("con email" if f.get("con_email") else "sin email")
    if f.get("contactado"):
        partes.append("ya contactado")
    return "• " + " · ".join(partes)


def vistas_nuevas(cur, d, ahora):
    return _filas(cur, """
        SELECT acta, denominacion, denominacion_inpi, clase, vista_tipo, vista_motivo, vista_estado,
               LEAST(vista_fecha_vencimiento, vista_vencimiento_calculado) AS vence,
               COALESCE(email, '') <> '' AS con_email, COALESCE(contactado, false) AS contactado
        FROM marcas
        WHERE es_lead AND vista_detectada_en > %s AND vista_detectada_en <= %s
          AND COALESCE(vista_sirve, true)
        ORDER BY vista_detectada_en""", (d, ahora))


def armar_vista(f) -> str:
    partes = [f'<a href="{e(link_acta(f["acta"]))}">{e(f["acta"])}</a> <b>{e(nombre(f))}</b>']
    if f.get("clase"):
        partes.append(f"clase {f['clase']}")
    tipo = TIPOS_VISTA.get(f.get("vista_tipo") or "")
    partes.append(f"vista {tipo}" if tipo else "vista")
    if f.get("vista_motivo"):
        partes.append(f"por «{e(f['vista_motivo'][:60])}»")
    if f.get("vista_estado") == "sin_notificar":
        partes.append("sin notificar")
    if f.get("vence"):
        partes.append(f"vence {fecha_txt(f['vence'])}")
    partes.append("con email" if f.get("con_email") else "sin email")
    if f.get("contactado"):
        partes.append("ya contactado")
    return "• " + " · ".join(partes)


# ── Vigilancia marcaria ───────────────────────────────────────────────────────────────────────────

def alertas_altas(cur, d, ahora):
    """Alertas de parecido abiertas de nivel ALTA, creadas (o juzgadas por la IA) desde la última pasada."""
    return _filas(cur, """
        SELECT a.id, a.acta_cliente, a.acta_nueva, a.denominacion_nueva, a.clase_nueva, a.titular_nuevo, a.agente_nuevo,
               a.puntaje, a.motivos, a.ia_veredicto, a.ia_razon, a.actas_cliente, a.creada_en,
               cm.denominacion AS denominacion_cliente, cm.clase AS clase_cliente, c.nombre AS cliente,
               COALESCE(c.vigilancia_contratada, false) AS contratada,
               (SELECT CASE WHEN m.boletin IS NULL THEN m.fecha_publicacion
                            ELSE COALESCE(m.fecha_publicacion, b.fecha, b.procesado_en::date) END
                  FROM marcas m LEFT JOIN boletines b ON b.numero = m.boletin WHERE m.acta = a.acta_nueva) AS publicacion,
               LEAST(a.creada_en, (SELECT s.creado_en FROM solicitudes_escaneadas s WHERE s.acta = a.acta_nueva)) AS detectada_en
        FROM vigilancia_alertas a
        LEFT JOIN cartera_marcas cm ON cm.acta = a.acta_cliente
        LEFT JOIN clientes c ON c.id = a.cliente_id
        WHERE a.tipo = 'similitud' AND a.estado = 'nueva' AND a.nivel = 'alta'
          AND GREATEST(a.creada_en, COALESCE(a.ia_en, a.creada_en)) > %s
          AND GREATEST(a.creada_en, COALESCE(a.ia_en, a.creada_en)) <= %s
        ORDER BY a.creada_en""", (d, ahora))


VEREDICTOS = {"identica": "Idéntica", "confundible": "Confundible", "cercana": "Cercana", "no_confundible": "No confundible"}


def _clases(f) -> str:
    lista = f.get("actas_cliente") or []
    clases = sorted({x.get("clase") for x in lista if isinstance(x, dict) and x.get("clase") is not None})
    if not clases and f.get("clase_cliente") is not None:
        clases = [f["clase_cliente"]]
    return ", ".join(str(c) for c in clases) or "?"


def armar_alerta_alta(f) -> str:
    hoy = _dt.datetime.now(TZ).date()
    pub = f.get("publicacion")
    det = f.get("detectada_en")
    det = det.astimezone(TZ).date() if isinstance(det, _dt.datetime) else det
    lineas = [f"🔴 <b>{e(f.get('denominacion_cliente') or '?')}</b> (clase {_clases(f)}) · cliente {e(f.get('cliente') or '?')}"
              + ("" if f.get("contratada") else " · <i>sin vigilancia contratada</i>"),
              f"   ↳ <b>{e(f.get('denominacion_nueva') or '(sin nombre)')}</b> · clase {f.get('clase_nueva') or '?'} · "
              f'acta <a href="https://portaltramites.inpi.gob.ar/MarcasConsultas/Resultado?acta={quote(str(f["acta_nueva"]))}">{e(str(f["acta_nueva"]))}</a>'
              + (f" · {e(f['titular_nuevo'])}" if f.get("titular_nuevo") else "")]
    if f.get("ia_veredicto"):
        lineas.append(f"   🤖 {VEREDICTOS.get(f['ia_veredicto'], f['ia_veredicto'])}: {e((f.get('ia_razon') or '')[:300])}")
    else:
        lineas.append(f"   Motor: {e((f.get('motivos') or '')[:200])} (todavía sin revisar por la IA)")
    if pub:
        cierre = pub + _dt.timedelta(days=30)
        txt = f"   ⏳ Plazo para oponerse hasta el <b>{cierre:%d/%m/%Y}</b> ({(cierre - hoy).days} días)"
        if det and (pub - det).days > 0:
            txt += f" · detectada {(pub - det).days} días antes de la publicación"
        lineas.append(txt)
    else:
        lineas.append("   ⏳ Todavía no se publicó: el plazo de 30 días empieza con el boletín"
                      + (f" · detectada {'hoy' if (hoy - det).days == 0 else f'hace {(hoy - det).days} días'}" if det else ""))
    return "\n".join(lineas)


def bloque_vigilancia(conn, ahora=None, mandar=None) -> int:
    """Manda las alertas ALTAS nuevas al tema «Vigilancia marcaria». Devuelve cuántas mandó."""
    ahora = ahora or _dt.datetime.now(_dt.timezone.utc)
    with conn.cursor() as cur:
        d = desde(cur, "vigilancia_altas", ahora)
        if d is None:
            conn.commit()
            return 0
        filas = alertas_altas(cur, d, ahora)
    print(f"vigilancia_altas: {len(filas)} nueva(s)")
    if filas:
        texto = lista(filas, armar_alerta_alta,
                      f"🔎 <b>{len(filas)} alerta(s) ALTA de vigilancia</b>\n"
                      f'<a href="{PANEL}/clientes">Abrir Clientes → Vigilancia</a>')
        enviar = mandar or (lambda t, tema: telegram_avisos.enviar(t, tema=tema, conn=conn))
        if not enviar(texto, "vigilancia"):
            return 0
    with conn.cursor() as cur:
        marcar(cur, "vigilancia_altas", ahora)
    conn.commit()
    return len(filas)


# ── Boletines ─────────────────────────────────────────────────────────────────────────────────────

def boletines_publicados(cur):
    """Boletines que INPI publicó (boletines_inpi) con número mayor al último avisado."""
    cur.execute("SELECT valor FROM reportes_config WHERE clave = 'aviso_desde:boletin_publicado'")
    fila = cur.fetchone()
    cur.execute("SELECT numero, fecha FROM boletines_inpi WHERE numero ~ '^[0-9]+$'")
    publicados = sorted(((int(n), f) for n, f in cur.fetchall()), key=lambda x: x[0])
    if not publicados:
        return []
    if not fila or not (fila[0] or "").isdigit():
        marcar(cur, "boletin_publicado", publicados[-1][0])   # primera vez: no se avisa el historial
        return []
    piso = int(fila[0])
    nuevos = [(n, f) for n, f in publicados if n > piso]
    if not nuevos:
        return []
    cur.execute("SELECT numero, estado FROM boletines WHERE numero = ANY(%s)", ([str(n) for n, _ in nuevos],))
    estados = dict(cur.fetchall())
    return [{"numero": n, "fecha": f, "estado": estados.get(str(n))} for n, f in nuevos]


def boletines_cargados(cur, d, ahora):
    return _filas(cur, """
        SELECT b.numero, b.fecha, COUNT(m.acta) AS marcas,
               COUNT(*) FILTER (WHERE m.es_lead) AS leads,
               COUNT(*) FILTER (WHERE m.es_lead AND COALESCE(m.email, '') <> '') AS con_email,
               COUNT(*) FILTER (WHERE m.fuente = 'escaneo_directo') AS ya_por_escaneo,
               COUNT(*) FILTER (WHERE m.es_lead IS NULL) AS sin_verificar
        FROM boletines b LEFT JOIN marcas m ON m.boletin = b.numero
        WHERE b.estado = 'procesado' AND b.procesado_en > %s AND b.procesado_en <= %s
        GROUP BY b.numero, b.fecha ORDER BY b.numero""", (d, ahora))


# ── Sistema ───────────────────────────────────────────────────────────────────────────────────────

def disparos_fallidos(cur, d, ahora):
    return _filas(cur, """
        SELECT workflow, programada, intentos, detalle FROM programador_disparos
        WHERE estado = 'error' AND intentos >= %s AND ultimo_intento > %s AND ultimo_intento <= %s
        ORDER BY programada""", (MAX_INTENTOS_PROGRAMADOR, d, ahora))


def mails_esperando_cupo(cur):
    cur.execute("""SELECT COUNT(*), MIN(intentar_desde) FROM mails_cola
                   WHERE estado = 'pendiente' AND intentar_desde > now()""")
    return cur.fetchone()


# ── Resumen ───────────────────────────────────────────────────────────────────────────────────────

def _uno(cur, sql, params=()):
    """Un número; None si la consulta falla (por ejemplo, una tabla que todavía no existe). La conexión
    está en autocommit, así que un error no deja la transacción trabada."""
    try:
        cur.execute(sql, params)
        v = cur.fetchone()
        return v[0] if v else 0
    except Exception:
        return None


def armar_resumen(cur, inicio, fin, titulo) -> str:
    p = {"i": inicio, "f": fin}
    datos = [
        ("Leads nuevos", _uno(cur, "SELECT COUNT(*) FROM marcas WHERE es_lead AND creado_en >= %(i)s AND creado_en < %(f)s", p)),
        ("  con email", _uno(cur, "SELECT COUNT(*) FROM marcas WHERE es_lead AND COALESCE(email,'') <> '' "
                                  "AND creado_en >= %(i)s AND creado_en < %(f)s", p)),
        ("Oposiciones nuevas en leads", _uno(cur, "SELECT COUNT(*) FROM marcas WHERE es_lead AND oposicion_detectada_en >= %(i)s "
                                                  "AND oposicion_detectada_en < %(f)s", p)),
        ("Vistas nuevas en leads", _uno(cur, "SELECT COUNT(*) FROM marcas WHERE es_lead AND vista_detectada_en >= %(i)s "
                                             "AND vista_detectada_en < %(f)s", p)),
        ("Actas contactadas", _uno(cur, "SELECT COUNT(*) FROM marcas WHERE contactado_en >= %(i)s AND contactado_en < %(f)s", p)),
        ("Consultas desde la web", _uno(cur, "SELECT COUNT(*) FROM verificaciones_marca WHERE creada_en >= %(i)s AND creada_en < %(f)s", p)),
        ("Formularios de clientes", _uno(cur, "SELECT COUNT(*) FROM formularios_respuestas WHERE recibido_en >= %(i)s AND recibido_en < %(f)s", p)),
        ("Clientes nuevos (convertidos)", _uno(cur, "SELECT COUNT(*) FROM clientes WHERE convertido_en >= %(i)s AND convertido_en < %(f)s", p)),
        ("Boletines cargados", _uno(cur, "SELECT COUNT(*) FROM boletines WHERE estado = 'procesado' AND procesado_en >= %(i)s "
                                         "AND procesado_en < %(f)s", p)),
    ]
    pendientes = [
        ("Leads con oposición sin contactar", _uno(cur, """SELECT COUNT(*) FROM marcas WHERE es_lead AND tuvo_oposicion
            AND COALESCE(oposicion_sirve, true) AND NOT COALESCE(oposicion_atendida, false)
            AND NOT COALESCE(contactado, false) AND no_contactar_en IS NULL""")),
        ("Leads con vista sin contactar", _uno(cur, """SELECT COUNT(*) FROM marcas WHERE es_lead AND tuvo_vista
            AND COALESCE(vista_sirve, true) AND NOT COALESCE(contactado, false) AND no_contactar_en IS NULL""")),
        ("Mails esperando cupo de Resend", _uno(cur, "SELECT COUNT(*) FROM mails_cola WHERE estado = 'pendiente' AND intentar_desde > now()")),
    ]
    corridas = _filas_seguro(cur, """SELECT nombre, conclusion FROM reportes_corridas
                                     WHERE fin >= %(i)s AND fin < %(f)s""", p)
    lineas = [f"<b>{e(titulo)}</b>", f"<i>{fecha_txt(inicio)} {inicio.astimezone(TZ):%H:%M} → "
                                     f"{fecha_txt(fin)} {fin.astimezone(TZ):%H:%M}</i>", ""]
    lineas += [f"• {e(k)}: <b>{num(v)}</b>" for k, v in datos if v is not None]
    lineas += ["", "<b>Pendiente ahora</b>"]
    lineas += [f"• {e(k)}: <b>{num(v)}</b>" for k, v in pendientes if v is not None]
    if corridas:
        malas = [c["nombre"] for c in corridas if c["conclusion"] not in ("success", "skipped")]
        lineas += ["", f"<b>Procesos</b>: {num(len(corridas))} corridas, "
                       f"{'todas bien ✅' if not malas else f'{len(malas)} con problemas ❌'}"]
        for nombre_proc in sorted(set(malas)):
            lineas.append(f"• {e(nombre_proc)} ({malas.count(nombre_proc)})")
    return "\n".join(lineas)


def _filas_seguro(cur, sql, params):
    try:
        return _filas(cur, sql, params)
    except Exception:
        return []


def toca_resumen(ultimo: str | None, ahora_local: _dt.datetime, forzar=False) -> bool:
    """¿Hay que mandar el resumen del día? Una vez por día, a partir de las 7:30."""
    if forzar:
        return True
    return ahora_local.time() >= HORA_RESUMEN and ultimo != ahora_local.date().isoformat()


# ── Pasada ────────────────────────────────────────────────────────────────────────────────────────

def pasada(conn, forzar_resumen=False):
    ahora = _dt.datetime.now(_dt.timezone.utc)
    enviados = 0

    def mandar(texto, tema):
        nonlocal enviados
        if telegram_avisos.enviar(texto, tema=tema, conn=conn):
            enviados += 1
            return True
        return False

    def bloque(tipo, consulta, tema, armar_msg):
        """Busca lo nuevo de `tipo` y, si se mandó (o no había nada), corre la marca hasta `ahora`."""
        with conn.cursor() as cur:
            d = desde(cur, tipo, ahora)
            if d is None:
                return
            try:
                filas = consulta(cur, d, ahora)
            except Exception as ex:
                print(f"::warning::{tipo}: no se pudo consultar ({type(ex).__name__}: {ex})")
                return
        print(f"{tipo}: {len(filas)} nueva(s)")
        if not filas or mandar(armar_msg(filas), tema):
            with conn.cursor() as cur:
                marcar(cur, tipo, ahora)

    bloque("oposiciones", oposiciones_nuevas, "oposiciones", lambda fs: lista(
        fs, armar_oposicion, f"⚠️ <b>{len(fs)} oposición(es) nueva(s) en leads</b>"))
    bloque("vistas", vistas_nuevas, "oposiciones", lambda fs: lista(
        fs, armar_vista, f"👁 <b>{len(fs)} vista(s) nueva(s) en leads</b>"))
    bloque("vigilancia_altas", alertas_altas, "vigilancia", lambda fs: lista(
        fs, armar_alerta_alta, f"🔎 <b>{len(fs)} alerta(s) ALTA de vigilancia</b>\n"
                               f'<a href="{PANEL}/clientes">Abrir Clientes → Vigilancia</a>'))

    # Boletín publicado por INPI
    try:
        with conn.cursor() as cur:
            nuevos = boletines_publicados(cur)
    except Exception as ex:
        print(f"::warning::boletines publicados: no se pudo consultar ({type(ex).__name__})")
        nuevos = []
    if nuevos:
        lineas = ["📰 <b>INPI publicó boletín nuevo</b>", ""]
        for b in nuevos:
            estado = "ya está cargado ✅" if b["estado"] == "procesado" else "todavía no está cargado"
            lineas.append(f"• Boletín <b>{b['numero']}</b>{' del ' + fecha_txt(b['fecha']) if b['fecha'] else ''}: {estado}")
        if any(b["estado"] != "procesado" for b in nuevos):
            lineas += ["", f'Para cargarlo: <a href="{PANEL}/boletines">Boletines → Importar</a>']
        print(f"boletines publicados: {len(nuevos)} nuevo(s)")
        if mandar("\n".join(lineas), "boletines"):
            with conn.cursor() as cur:
                marcar(cur, "boletin_publicado", max(b["numero"] for b in nuevos))

    bloque("boletines_cargados", boletines_cargados, "boletines", lambda fs: "\n\n".join(
        f"✅ <b>Boletín {e(str(b['numero']))} cargado</b>\n"
        f"• Marcas: {num(b['marcas'])}\n• Leads: {num(b['leads'])} (con email: {num(b['con_email'])})\n"
        f"• Ya los teníamos por el escaneo: {num(b['ya_por_escaneo'])}"
        + (f"\n• Sin verificar todavía: {num(b['sin_verificar'])}" if b["sin_verificar"] else "")
        for b in fs))

    bloque("disparos", disparos_fallidos, "sistema", lambda fs: lista(
        fs, lambda f: f"• {e(f['workflow'])} de las {f['programada'].astimezone(TZ):%H:%M} "
                      f"({f['intentos']} intentos){': ' + e((f['detalle'] or '')[:120]) if f.get('detalle') else ''}",
        "🚨 <b>El programador del panel no pudo lanzar procesos</b>"))

    # Mails esperando cupo (una vez por día)
    try:
        with conn.cursor() as cur:
            cantidad, sale = mails_esperando_cupo(cur)
            cur.execute("SELECT valor FROM reportes_config WHERE clave = 'aviso_desde:cupo_resend'")
            fila = cur.fetchone()
    except Exception:
        cantidad, fila = 0, None
    hoy = ahora.astimezone(TZ).date().isoformat()
    if cantidad and (not fila or fila[0] != hoy):
        texto = f"✉️ <b>{num(cantidad)} mail(s) a prospectos esperando cupo de Resend</b>\nSe terminó el cupo diario"
        texto += f"; salen solos desde las {sale.astimezone(TZ):%H:%M}." if sale else "."
        if mandar(texto, "sistema"):
            with conn.cursor() as cur:
                marcar(cur, "cupo_resend", hoy)

    # Resumen
    ahora_local = ahora.astimezone(TZ)
    with conn.cursor() as cur:
        cur.execute("SELECT valor FROM reportes_config WHERE clave = 'aviso_desde:resumen'")
        fila = cur.fetchone()
    if toca_resumen(fila[0] if fila else None, ahora_local, forzar_resumen):
        with conn.cursor() as cur:
            texto = armar_resumen(cur, ahora - _dt.timedelta(days=1), ahora, "📊 Resumen de las últimas 24 horas")
            if ahora_local.weekday() == 0:
                texto += "\n\n" + armar_resumen(cur, ahora - _dt.timedelta(days=7), ahora, "📅 Resumen de la semana")
        if mandar(texto, "resumen"):
            with conn.cursor() as cur:
                marcar(cur, "resumen", ahora_local.date().isoformat())

    print(f"Avisos mandados: {enviados}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--resumen", action="store_true", help="mandar el resumen del día ahora")
    args = ap.parse_args()
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("::warning::Falta TELEGRAM_BOT_TOKEN: no hay avisos para mandar.")
        return 0
    conn = telegram_avisos._conectar()
    if conn is None:
        print("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)", file=sys.stderr)
        return 1
    try:
        with conn.cursor() as cur:
            cur.execute(telegram_avisos.CREAR_TABLA)
        pasada(conn, forzar_resumen=args.resumen)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
