"""
Presentación de oposición para la llamada con el cliente (página privada de la web
smartiesconsultora.com.ar, ver wordpress/presentacion-oposicion.html).

Se escribe el número de acta y el panel arma, en una sola respuesta, todo lo que muestran las slides:
  - la marca del cliente (denominación, tipo, clase, titular, limitación, logo, estado y ubicación del
    expediente),
  - la publicación en el boletín y el cierre del plazo de oposición (30 días corridos),
  - cada oposición: quién se opuso, cuándo, su agente y la marca que cita (denominación, clase y logo),
  - las fechas de notificación y de vencimiento de los 3 meses para negociar,
  - si existe, el Informe Oposición IA del panel para esa acta (chances, palancas, caminos).

De dónde sale:
  * UN pedido al expediente de INPI (el mismo que usa la pestaña Análisis: oposiciones, vistas, datos de la
    marca y logo vienen en la misma página).
  * Las marcas de los oponentes: primero lo guardado en analisis_marca_opuesta (la tabla de la pestaña
    Análisis); lo que falta se pide a INPI, con pausa y con un tope de tiempo, y se guarda.
  * El informe: informes_viabilidad (tipo 'oposicion', sin archivar), el más nuevo que cita esa acta.
  * Si INPI no responde y la marca ya está en el panel (analisis_marca + marcas), se usa lo guardado y se
    avisa la fecha.

Una consulta buena queda en memoria CACHE_SEG (pasar de una slide a otra o recargar no vuelve a pedir a
INPI); el botón «Actualizar» de la página la fuerza.

Este módulo no abre conexiones a la base: recibe `conexion` y `rcur` (como oposicion_web).
"""

import base64
import datetime as _dt
import json
import logging
import re
import threading
import time

import requests

import analisis_marca as am
import informes_oposicion as iop
import inpi_lead

log = logging.getLogger("presentacion_oposicion")

RE_ACTA = re.compile(r"^\d{5,9}$")
CACHE_SEG = 20 * 60           # una consulta buena vale 20 minutos
PRESUPUESTO_OPUESTAS_SEG = 25.0   # tiempo máximo para leer en INPI las marcas de los oponentes
PAUSA_SEG = 1.0               # entre un pedido a INPI y el siguiente
MAX_OPOSICIONES = 8
MAX_MARCAS_POR_OPOSICION = 3
PLAZO_OPOSICION_DIAS = 30
MESES_NEGOCIACION = 3

_cache = {}                   # acta -> (instante monotonic, respuesta)
_cache_lock = threading.Lock()


class ActaInvalida(ValueError):
    pass


# ── Fechas ────────────────────────────────────────────────────────────────
def _d(iso):
    try:
        return _dt.date.fromisoformat(str(iso)[:10]) if iso else None
    except ValueError:
        return None


def sumar_meses(fecha: _dt.date, meses: int) -> _dt.date:
    """14/10 + 3 meses = 14/01; 31/10 + 3 meses = 31/01; 30/11 + 3 meses = 28/02 (o 29)."""
    m = fecha.month - 1 + meses
    anio, mes = fecha.year + m // 12, m % 12 + 1
    ultimo = (_dt.date(anio + (mes == 12), mes % 12 + 1, 1) - _dt.timedelta(days=1)).day
    return _dt.date(anio, mes, min(fecha.day, ultimo))


def cierre_oposiciones(publicacion):
    """Último día para oponerse: publicación + 30 días corridos (07/10 → 06/11)."""
    f = _d(publicacion)
    return (f + _dt.timedelta(days=PLAZO_OPOSICION_DIAS)).isoformat() if f else None


def _data_uri(logo):
    """(bytes, mime) → data URI del logo ya limpio y reducido (o None)."""
    if not logo:
        return None
    prep = am.preparar_logo(*logo)
    if not prep:
        return None
    return f"data:{prep[1]};base64,{base64.b64encode(prep[0]).decode()}"


# ── Lectura del expediente ────────────────────────────────────────────────
def _vistas_de_oposicion(html: str) -> list:
    """Tabla VISTAS del expediente, solo las de tipo «Oposiciones»: a veces la notificación al titular
    figura acá y no en la tabla de oposiciones (acta 4775006)."""
    crudas, _ = inpi_lead._json_de_js(html, "vistas")
    out = []
    for v in crudas or []:
        if not isinstance(v, dict) or "oposic" not in str(v.get("Tipo") or "").lower():
            continue
        out.append({"notificacion": inpi_lead._fecha_valida_opo(v.get("Fecha_Notificacion"))
                    or inpi_lead._fecha_valida_opo(v.get("Fecha_Vista")),
                    "vencimiento": inpi_lead._fecha_valida_opo(v.get("Fecha_Vencimiento"))})
    return [v for v in out if v["notificacion"] or v["vencimiento"]]


def leer_expediente(html: str, acta: str) -> dict:
    """Todo lo que la presentación usa de la página del expediente (sin pedir nada más)."""
    d = inpi_lead.datos_de_expediente(html)
    e = inpi_lead.parsear_datos_expediente(html)
    ops = inpi_lead.parsear_oposiciones(html)
    m_gestion = inpi_lead.RE_GESTION.search(html or "")
    bloque = m_gestion.group(1) if m_gestion else ""
    m_car = inpi_lead.RE_CARACTER_SPAN.search(bloque)
    con_representante = bool((m_car and re.sub(r"\s+", " ", m_car.group(1)).strip())
                             or inpi_lead._agente_de_bloque(bloque)["matricula_agente"])
    return {
        "acta": acta,
        "denominacion": e["denominacion"] or d.get("denominacion"),
        "tipo": e["tipo_marca"],
        "clase": e["clase"] or (str(d["clase"]) if d.get("clase") else None),
        "titular": d.get("titular"),
        "limitacion": e["limitacion"],
        "fecha_presentacion": d.get("fecha_presentacion"),
        "estado_tramite": d.get("estado_tramite"),
        "fecha_concesion": d.get("fecha_concesion"),
        "ubicacion": d.get("ubicacion_actual"),
        "ubicacion_fecha": d.get("ubicacion_fecha"),
        "con_representante": con_representante,
        "publicaciones": e["publicaciones"],
        "oposiciones": ops["items"],
        "vistas_oposicion": _vistas_de_oposicion(html),
        "logo": inpi_lead.extraer_logo(html),
    }


def _pedir_expediente(acta: str, s) -> dict:
    """UN pedido al expediente de INPI. {estado: ok|no_existe|bloqueado|error, html, error}."""
    try:
        r = inpi_lead._get_con_reintentos(lambda: s.post(
            f"{inpi_lead.BASE}/MarcasConsultas/Resultado",
            headers={"Referer": f"{inpi_lead.BASE}/MarcasConsultas/Grilla"},
            data={"acta": acta}, timeout=30), intentos=2)
    except requests.RequestException as e:
        return {"estado": "error", "html": "", "error": f"INPI no respondió ({type(e).__name__})"}
    if "Web Page Blocked" in r.text or "Attack ID" in r.text:
        return {"estado": "bloqueado", "html": "", "error": "INPI bloqueó la consulta"}
    if "GESTION DEL TRAMITE" not in r.text and "TITULARIDAD" not in r.text:
        return {"estado": "no_existe", "html": "", "error": "INPI no tiene un expediente con ese número de acta"}
    return {"estado": "ok", "html": r.text, "error": ""}


# ── Marcas de los oponentes ───────────────────────────────────────────────
def _completar_opuestas(conexion, rcur, actas: list, s) -> dict:
    """{acta: datos (con 'logo' (bytes, mime) si tiene)} de las marcas que citan los oponentes. Lo que no
    está guardado se pide a INPI dentro de PRESUPUESTO_OPUESTAS_SEG y se guarda para la próxima."""
    if not actas:
        return {}
    with conexion() as conn, rcur(conn) as cur:
        guardadas = am.leer_opuestas(cur, actas, con_logo=True)
    inicio = time.monotonic()
    pedidas = 0
    for a in actas:
        if a in guardadas or time.monotonic() - inicio > PRESUPUESTO_OPUESTAS_SEG:
            continue
        if pedidas:
            time.sleep(PAUSA_SEG)
        pedidas += 1
        try:
            r = inpi_lead.consultar_marca_opuesta(a, s)
        except Exception:  # noqa: BLE001 — una marca que falla no corta la presentación
            log.warning("No se pudo leer la marca opuesta %s", a, exc_info=True)
            continue
        if r["estado_consulta"] in ("ok", "no_existe"):
            datos = r["datos"] if r["estado_consulta"] == "ok" else {}
            try:
                with conexion() as conn, rcur(conn) as cur:
                    am.guardar_opuesta(cur, a, r["estado_consulta"], datos, r["logo"])
                    conn.commit()
            except Exception:  # noqa: BLE001
                log.warning("No se pudo guardar la marca opuesta %s", a, exc_info=True)
            guardadas[a] = dict(datos or {}, estado=r["estado_consulta"],
                                logo=r["logo"] if r["estado_consulta"] == "ok" else None)
        elif r["estado_consulta"] == "bloqueado":
            break
    return guardadas


def armar_oposiciones(items: list, acta: str, opuestas: dict, vistas: list) -> list:
    """Las oposiciones como las muestra la slide «¿Quién se opuso?», con la marca que cita cada una."""
    out = []
    for i in (items or [])[:MAX_OPOSICIONES]:
        citadas = inpi_lead.marcas_opuestas_citadas(i.get("fundamento"), acta)
        marcas = []
        for a in citadas["actas"][:MAX_MARCAS_POR_OPOSICION]:
            o = opuestas.get(a) or {}
            if o.get("estado") == "no_existe":
                continue
            marcas.append({"acta": a, "denominacion": o.get("denominacion"), "clase": o.get("clase"),
                           "tipo": o.get("tipo_marca"),
                           "logo": _data_uri(o.get("logo")) if o.get("logo") else None})
        out.append({
            "oponente": i.get("oponente"),
            "presentacion": i.get("presentacion"),
            "notificacion": i.get("notificacion"),
            "vencimiento": i.get("vencimiento"),
            "levantamiento": i.get("levantamiento"),
            "agente_oponente": i.get("agente_oponente"),
            "marcas": marcas,
            "citada": citadas["citada"],
        })
    # La notificación a veces solo figura en VISTAS: se completa en las que no la tienen.
    if vistas:
        v = sorted(vistas, key=lambda x: x["notificacion"] or "")[0]
        for o in out:
            if not o["notificacion"] and not o["levantamiento"]:
                o["notificacion"] = v["notificacion"]
                o["vencimiento"] = o["vencimiento"] or v["vencimiento"]
    return out


def plazos(publicaciones: list, oposiciones: list, vistas: list) -> dict:
    """Las fechas de la línea de tiempo: publicación, cierre de oposiciones, notificación y vencimiento de
    los 3 meses (la oposición vigente que vence primero; si INPI no informa el vencimiento, notificación +
    3 meses)."""
    pubs = sorted(p["fecha"] for p in publicaciones or [] if p.get("fecha"))
    publicacion = pubs[0] if pubs else None
    vigentes = [o for o in oposiciones if not o.get("levantamiento")]
    notifs = sorted(o["notificacion"] for o in vigentes if o.get("notificacion"))
    if not notifs:
        notifs = sorted(v["notificacion"] for v in vistas or [] if v.get("notificacion"))
    notificacion = notifs[0] if notifs else None
    vtos = sorted(o["vencimiento"] for o in vigentes if o.get("vencimiento"))
    vencimiento = vtos[0] if vtos else None
    if not vencimiento and notificacion:
        vencimiento = sumar_meses(_d(notificacion), MESES_NEGOCIACION).isoformat()
    return {"publicacion": publicacion, "cierre_oposiciones": cierre_oposiciones(publicacion),
            "notificacion": notificacion, "vencimiento_negociacion": vencimiento,
            "oposiciones_vigentes": len(vigentes)}


# ── Informe Oposición IA ──────────────────────────────────────────────────
def _t(v, largo=400):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:largo]


def resumen_informe(d: dict, generado_en=None) -> dict:
    """Lo que la slide «Análisis» muestra del informe: chances, motivo, palancas, caminos y la conclusión de
    la comparación de signos. Nunca costos ni preguntas internas."""
    ch = d.get("chances") if isinstance(d.get("chances"), dict) else {}
    rx = d.get("resumen_express") if isinstance(d.get("resumen_express"), dict) else {}
    comp = d.get("comparacion") if isinstance(d.get("comparacion"), dict) else {}
    signos = comp.get("signos") if isinstance(comp.get("signos"), dict) else {}
    palancas = [{"palanca": _t(p.get("palanca"), 120), "detalle": _t(p.get("detalle"), 260)}
                for p in d.get("palancas") or [] if isinstance(p, dict) and p.get("palanca")][:4]
    caminos = [{"titulo": _t(c.get("titulo"), 100), "detalle": _t(c.get("detalle"), 300),
                "recomendado": bool(c.get("recomendado"))}
               for c in d.get("caminos") or [] if isinstance(c, dict) and c.get("titulo")][:4]
    return {
        "fecha": _t(d.get("fecha_analisis"), 20) or (generado_en.strftime("%d/%m/%Y") if generado_en else ""),
        "chances": iop.nivel_chances(ch.get("nivel")),
        "niveles": iop.NIVELES,
        "motivo": _t(ch.get("motivo"), 400),
        "camino": _t(rx.get("camino_sugerido"), 300),
        "signos": _t(signos.get("conclusion"), 300),
        "palancas": palancas,
        "caminos": caminos,
    }


def buscar_informe(cur, acta: str):
    """El Informe Oposición IA más nuevo (sin archivar) que menciona esa acta del cliente."""
    cur.execute(
        "SELECT datos, generado_en FROM informes_viabilidad WHERE tipo = 'oposicion' AND archivado_en IS NULL "
        "AND (EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(datos->'oposiciones') = 'array' "
        "             THEN datos->'oposiciones' ELSE '[]'::jsonb END) o WHERE o->>'acta_cliente' = %s) "
        "  OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(datos->'marca_cliente'->'actas_opuestas') = 'array' "
        "             THEN datos->'marca_cliente'->'actas_opuestas' ELSE '[]'::jsonb END) a WHERE a->>'acta' = %s)) "
        "ORDER BY generado_en DESC LIMIT 1", (acta, acta))
    f = cur.fetchone()
    if not f:
        return None
    datos = f["datos"] if isinstance(f, dict) else f[0]
    if isinstance(datos, str):
        try:
            datos = json.loads(datos)
        except ValueError:
            return None
    return resumen_informe(datos, f["generado_en"] if isinstance(f, dict) else f[1]) if isinstance(datos, dict) else None


# ── Lo guardado en el panel (si INPI no responde) ─────────────────────────
def _desde_panel(cur, acta: str):
    m = am.leer_marca(cur, acta)
    if not m:
        return None
    g = am.leer_analisis(cur, acta)
    snap = g["oposiciones"]
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except ValueError:
            snap = None
    items = (snap or {}).get("items") if isinstance(snap, dict) else None
    if items is None:
        items = am.oposiciones_del_sistema(m)
    e = g["expediente"] or {}
    pubs = e.get("publicaciones") or ([{"fecha": str(m["fecha_publicacion"])[:10]}] if m.get("fecha_publicacion") else [])
    return {
        "acta": acta, "denominacion": e.get("denominacion") or am.nombre_marca(m), "tipo": e.get("tipo_marca"),
        "clase": e.get("clase") or (str(m["clase"]) if m.get("clase") else None), "titular": m.get("titular"),
        "limitacion": e.get("limitacion"), "fecha_presentacion": None, "estado_tramite": None,
        "fecha_concesion": None, "ubicacion": None, "ubicacion_fecha": None, "con_representante": None,
        "publicaciones": pubs, "oposiciones": items or [], "vistas_oposicion": [],
        "logo": am.leer_logo(cur, acta),
        "consultado_en": (snap or {}).get("consultado_en") if isinstance(snap, dict) else None,
    }


# ── Armado ────────────────────────────────────────────────────────────────
def validar_acta(acta) -> str:
    a = re.sub(r"[\s.]", "", str(acta or ""))
    if not RE_ACTA.match(a):
        raise ActaInvalida("Escribí un número de acta válido (solo números).")
    return a


def armar(acta: str, conexion, rcur, actualizar: bool = False) -> tuple:
    """(status HTTP, cuerpo). 200 con la presentación; 404 si INPI no tiene el acta; 502 si INPI no
    respondió y la marca tampoco está guardada en el panel."""
    acta = validar_acta(acta)
    if not actualizar:
        with _cache_lock:
            c = _cache.get(acta)
        if c and time.monotonic() - c[0] < CACHE_SEG:
            return 200, c[1]

    s = inpi_lead._crear_sesion()
    pedido = _pedir_expediente(acta, s)
    aviso, origen = None, "inpi"
    if pedido["estado"] == "ok":
        exp = leer_expediente(pedido["html"], acta)
        consultado_en = _dt.datetime.now(_dt.timezone.utc).isoformat()
    elif pedido["estado"] == "no_existe":
        return 404, {"detail": f"INPI no tiene un expediente con el acta {acta}. Revisá el número."}
    else:
        with conexion() as conn, rcur(conn) as cur:
            exp = _desde_panel(cur, acta)
        if not exp:
            return 502, {"detail": f"{pedido['error']}. Probá de nuevo en un rato."}
        origen = "panel"
        consultado_en = exp.pop("consultado_en", None)
        aviso = "INPI no respondió: se muestran los datos guardados en el panel."

    actas_op = []
    for i in exp["oposiciones"][:MAX_OPOSICIONES]:
        for a in inpi_lead.marcas_opuestas_citadas(i.get("fundamento"), acta)["actas"][:MAX_MARCAS_POR_OPOSICION]:
            if a not in actas_op:
                actas_op.append(a)
    if origen == "inpi":
        opuestas = _completar_opuestas(conexion, rcur, actas_op, s)
    else:
        with conexion() as conn, rcur(conn) as cur:
            opuestas = am.leer_opuestas(cur, actas_op, con_logo=True)
    oposiciones = armar_oposiciones(exp["oposiciones"], acta, opuestas, exp["vistas_oposicion"])

    with conexion() as conn, rcur(conn) as cur:
        informe = buscar_informe(cur, acta)

    pubs = exp["publicaciones"] or []
    cuerpo = {
        "acta": acta,
        "url_inpi": am.url_expediente(acta),
        "origen": origen,
        "aviso": aviso,
        "consultado_en": consultado_en,
        "marca": {
            "denominacion": exp["denominacion"], "tipo": exp["tipo"], "clase": exp["clase"],
            "titular": exp["titular"], "limitacion": exp["limitacion"],
            "fecha_presentacion": exp["fecha_presentacion"], "estado_tramite": exp["estado_tramite"],
            "fecha_concesion": exp["fecha_concesion"], "ubicacion": exp["ubicacion"],
            "ubicacion_fecha": exp["ubicacion_fecha"], "con_representante": exp["con_representante"],
            "logo": _data_uri(exp["logo"]),
        },
        "publicacion": {"numero": next((p.get("numero") for p in pubs if p.get("numero")), None)},
        "plazos": plazos(pubs, oposiciones, exp["vistas_oposicion"]),
        "oposiciones": oposiciones,
        "informe": informe,
    }
    if origen == "inpi":
        with _cache_lock:
            _cache[acta] = (time.monotonic(), cuerpo)
            for k in [k for k, (t, _) in _cache.items() if time.monotonic() - t > CACHE_SEG]:
                _cache.pop(k, None)
    return 200, cuerpo
