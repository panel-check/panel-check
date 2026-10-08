"""
Análisis del estado de una oposición/vista a partir del EXPEDIENTE de INPI
(página /MarcasConsultas/Resultado) y de la Grilla Digital.

Por qué existe: hasta ahora revisar_oposiciones.py solo miraba la Grilla
Digital y adivinaba por texto ("OPO", "PODER"). Eso detecta que HAY una
oposición, pero no en qué estado está, y mezclaba el poder del oponente con el
del titular. El expediente trae la oposición estructurada: el portal arma las
tablas OPOSICIONES y VISTAS Y NOTIFICACIONES desde variables JavaScript
(`opos = JSON.parse('[...]')`, `vistas = JSON.parse('[...]')`) embebidas en la
página. Campos de cada oposición, confirmados con las actas 4764327 y 4760629
(29/09/2026 y 05/10/2026):

    Oponente, Fecha_Presentacion, Fecha_Notificacion, Fecha_Vencimiento,
    Numero, Agente, Caracter, Fecha_Levantamiento, Fundamento,
    Motivo_Levantamiento, Fundamento_Levantamiento, Fecha_Publicacion

Ojo: "Agente"/"Caracter" de la oposición son los del OPONENTE (su
apoderado), no los del titular de la marca. Lo que define que el titular tiene
representante es el bloque GESTION DEL TRAMITE del expediente (AGENTE/CARACTER
del titular) o una fila de poder/gestión posterior a la oposición en la Grilla
(regla del 08/10/2026, ver abajo).

Una fecha vacía viene como 01/01/0001 (/Date(-62135586000000)/): se trata
como "sin fecha".

Estados (estado_oposicion), y si el lead "sirve" para ofrecerle ayuda:

    sin_oposicion          -- no hay oposición ni vista                 (n/a)
    sin_notificar          -- presentada, INPI todavía no se la notificó  SIRVE
                              (el titular ni sabe: mejor momento para avisarle)
    notificada_en_plazo    -- notificada, el plazo para contestar corre   SIRVE
    plazo_vencido          -- venció el plazo y no hay contestación       SIRVE
                              (riesgo de abandono: se avisa como urgente)
    oposicion_sin_detalle  -- la Grilla la muestra pero el expediente
                              todavía no la cargó en OPOSICIONES          SIRVE
    vista_pendiente        -- vista de oficio de INPI sin contestar       SIRVE
    contestada             -- ya se presentó la contestación              no sirve
    levantada              -- oposición levantada / desistida             no sirve
    con_apoderado          -- el titular ya tiene agente/gestor           no sirve

Reglas de la Grilla Digital (acordadas el 08/10/2026; combinaciones EXACTAS, sin
tildes ni mayúsculas, ver _senales_grilla). Las "posteriores" son filas con fecha
≥ presentación de la oposición:
  - Notificación efectiva: una fila "Vista de Marcas" seguida (fila inmediata) de
    "Cédula de Notificación" -> notificada_en_plazo, si todavía no hay notificación
    en el expediente. La fecha de notificación es la de la cédula.
  - Trabajando: Indice "Recibo de Ingreso" y Referencia "Escritos de Marcas" en la
    misma fila -> contestada (atendida, sale de avisos).
  - Desistimiento: "Formula Desistimiento" (Indice o Referencia) -> levantada.
  - Gestor/apoderado: "Acompaña Poder", "Ratifica Gestión en Oposición" o
    "Ratifica Gestión" -> con_apoderado (el lead se descarta), pero SOLO si es de la
    fecha de notificación al titular o posterior y no del mismo día que la
    presentación de una oposición: los oponentes acompañan su poder junto con su
    oposición (acta 4759596). Un poder entre la oposición y la notificación no
    descarta por ahora (puede ser de un gestor del titular: se mide con
    diagnostico_oposiciones.py).
  - Oposición: "Recibo de Ingreso" + "Opo. de Marcas" (confirmado) = alguien se opuso.

Qué cuenta y qué no (casos reales 05/10/2026, actas 4688778, 4726688, 4748835):
  - La tabla VISTAS del expediente es HISTÓRICA: trae las vistas administrativas
    de toda la vida del trámite (campos Fecha_Vista, Fecha_Notificacion,
    Fecha_Vencimiento, Fecha_Contestacion, Tipo). Que el titular haya contestado
    una vista vieja (antes de la oposición) NO importa: ese trámite ya avanzó.
  - Lo que importa es la oposición actual: ¿se notificó?, ¿hay plazo?, ¿contestaron
    ESA oposición? Una contestación solo cuenta si es POSTERIOR a la presentación
    de la oposición (en la tabla de vistas o como fila "Contest..." en la Grilla).
  - Una vista de oficio sin contestar (Fecha_Contestacion vacía) sí sirve: es el
    único caso en que se mira la vista, cuando no hay oposición. Una vista ya
    contestada no genera alerta.
  - Una fecha vacía viene como 01/01/0001: nunca es una contestación.

Todavía no se vio una oposición ya contestada en el expediente real: la
contestación de la OPOSICIÓN se detecta por fecha (posterior a la presentación)
y puede aparecer con otra redacción. Si aparece un caso real, ajustar
TERMINOS_CONTESTACION.
"""

import datetime as _dt
import json
import re
import unicodedata

from validar_leads import (
    BASE,
    RE_CARACTER_SPAN,
    RE_GESTION,
    _agente_de_bloque,
    _get_con_reintentos,
    _parsear_fecha_grilla,
    buscar_fila_oposicion,
)

ESTADOS_QUE_SIRVEN = (
    "sin_notificar", "notificada_en_plazo", "plazo_vencido",
    "oposicion_sin_detalle", "vista_pendiente",
)
ESTADOS_CERRADOS = ("contestada", "levantada", "con_apoderado")

# Términos de Grilla Digital (Indice o Referencia, en mayúsculas)
TERMINOS_CONTESTACION = ("CONTEST",)

# Señales de la Grilla Digital con COMBINACIONES EXACTAS (acordadas el 08/10/2026).
# Se comparan con _norm(): sin tildes, mayúsculas y espacios simples. Las palabras
# sueltas ("LEVANT", "PODER", ...) daban falsos positivos, por eso ya no se usan.
GRILLA_TRABAJANDO = ("RECIBO DE INGRESO", "ESCRITOS DE MARCAS")  # Indice y Referencia, misma fila
GRILLA_DESISTIMIENTO = "FORMULA DESISTIMIENTO"                    # Indice o Referencia
GRILLA_PODER = ("ACOMPANA PODER", "RATIFICA GESTION EN OPOSICION", "RATIFICA GESTION")
GRILLA_OPOSICION = ("RECIBO DE INGRESO", "OPO. DE MARCAS")      # Indice y Referencia: alguien se opuso
GRILLA_NOTIF_VISTA = "VISTA DE MARCAS"                            # fila...
GRILLA_NOTIF_CEDULA = "CEDULA DE NOTIFICACION"                    # ...seguida de esta

_ANIO_MINIMO = 1990  # INPI manda 01/01/0001 cuando el campo está vacío


# ---------------------------------------------------------------- lectura --

def _fecha_valida(valor) -> str | None:
    """Fecha ISO (YYYY-MM-DD) o None si viene vacía (01/01/0001) o rota."""
    if not valor or not isinstance(valor, str):
        return None
    iso = _parsear_fecha_grilla(valor)
    if not iso or int(iso[:4]) < _ANIO_MINIMO:
        return None
    return iso


def _json_de_js(html: str, variable: str):
    """Lee `variable = JSON.parse('...')` de la página del expediente.
    Devuelve (lista | None, error). None sin error = la tabla viene vacía
    (el portal pone `variable = null`); error=True = hay datos pero no se
    pudieron parsear (se avisa para no confundirlo con "sin oposición")."""
    m = re.search(
        r"\b" + re.escape(variable) + r"\s*=\s*JSON\.parse\('((?:[^'\\]|\\.)*)'\)",
        html, re.S,
    )
    if not m:
        return None, False
    crudo = m.group(1).strip()
    if not crudo:
        return None, False
    try:
        datos = json.loads(crudo.replace("\\'", "'"))
    except ValueError:
        return None, True
    return (datos if isinstance(datos, list) else None), False


def _normalizar_oposicion(o: dict) -> dict:
    agente = o.get("Agente")
    caracter = (o.get("Caracter") or "").strip()
    return {
        "presentacion": _fecha_valida(o.get("Fecha_Presentacion")),
        "notificacion": _fecha_valida(o.get("Fecha_Notificacion")),
        "vencimiento": _fecha_valida(o.get("Fecha_Vencimiento")),
        "levantamiento": _fecha_valida(o.get("Fecha_Levantamiento")),
        "numero": o.get("Numero"),
        "agente_oponente": (f"{agente} ({caracter})" if agente and caracter
                            else str(agente) if agente else caracter) or None,
        "motivo_levantamiento": (o.get("Motivo_Levantamiento") or "").strip() or None,
    }


def _normalizar_vista(v: dict) -> dict:
    """Campos reales de la tabla VISTAS (acta 4748835, 05/10/2026):
    Fecha_Vista, Fecha_Notificacion, Fecha_Vencimiento, Fecha_Contestacion
    (vacía = 01/01/0001), Tipo ("Administrativas"), Cod_VistaExp, Acta."""
    return {
        "fecha": _fecha_valida(v.get("Fecha_Vista")),
        "notificacion": _fecha_valida(v.get("Fecha_Notificacion")),
        "vencimiento": _fecha_valida(v.get("Fecha_Vencimiento")),
        "contestacion": _fecha_valida(v.get("Fecha_Contestacion")),
        "tipo": (v.get("Tipo") or "").strip() or None,
    }


def parsear_expediente(html: str) -> dict:
    """Extrae de la página del expediente: oposiciones, vistas y la
    representación ACTUAL del titular (GESTION DEL TRAMITE)."""
    opos_raw, err1 = _json_de_js(html, "opos")
    vistas_raw, err2 = _json_de_js(html, "vistas")
    m_gestion = RE_GESTION.search(html)
    bloque = m_gestion.group(1) if m_gestion else html
    m_car = RE_CARACTER_SPAN.search(bloque)
    caracter = re.sub(r"\s+", " ", m_car.group(1)).strip() if m_car else ""
    ag = _agente_de_bloque(bloque)
    return {
        "oposiciones": [_normalizar_oposicion(o) for o in (opos_raw or []) if isinstance(o, dict)],
        "vistas": [_normalizar_vista(v) for v in (vistas_raw or []) if isinstance(v, dict)],
        "titular_caracter": caracter,
        "titular_agente": ag["agente"],
        "titular_matricula": ag["matricula_agente"],
        "error_lectura": err1 or err2,
        "bloqueado": False,
    }


def consultar_expediente(s, acta: str, timeout: int = 30) -> dict:
    """Pide el expediente a INPI (misma llamada que ya usa el resto del
    sistema) y devuelve parsear_expediente(...). bloqueado=True si el WAF
    frenó la consulta: el que llama reintenta en la próxima corrida."""
    r = _get_con_reintentos(
        lambda: s.post(
            f"{BASE}/MarcasConsultas/Resultado",
            headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
            data={"acta": acta},
            timeout=timeout,
        )
    )
    if "Web Page Blocked" in r.text or "Attack ID" in r.text:
        return {"bloqueado": True, "oposiciones": [], "vistas": [], "titular_caracter": "",
                "titular_agente": "", "titular_matricula": "", "error_lectura": False}
    return parsear_expediente(r.text)


def titular_con_representante(exp: dict) -> bool:
    """True si GESTION DEL TRAMITE muestra agente matriculado o un CARACTER
    (Apoderado / Gestor Ratificado): mismo criterio que define si es lead."""
    return bool((exp.get("titular_caracter") or "").strip() or (exp.get("titular_matricula") or "").strip())


# ----------------------------------------------------------- clasificación --

def _filas_grilla_posteriores(archivos: list[dict], terminos, desde: str | None) -> list[dict]:
    filas = []
    for a in archivos or []:
        txt = f"{a.get('Indice') or ''} {a.get('Referencia') or ''}".upper()
        if not any(t in txt for t in terminos):
            continue
        f = _fecha_valida(a.get("Fecha") or "")
        if desde and (not f or f < desde):
            continue
        filas.append(a)
    return filas


def _norm(texto) -> str:
    """Texto para comparar con la Grilla: sin tildes, en mayúsculas y con espacios simples."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", texto or "")
                         if not unicodedata.combining(c))
    return " ".join(sin_tildes.upper().split())


def es_presentacion_oposicion(fila: dict) -> bool:
    """Fila de la Grilla que es la presentación de una oposición: Indice "Recibo de
    Ingreso" y Referencia "Opo. de Marcas" (confirmado). Más estricta que buscar "OPO"
    suelto, que también matchea "Ratifica Gestión en OPOsición"."""
    return (GRILLA_OPOSICION[0] in _norm(fila.get("Indice"))
            and GRILLA_OPOSICION[1] in _norm(fila.get("Referencia")))


def _senales_grilla(
    archivos: list[dict], desde: str | None, notificacion_expediente: str | None = None,
    presentaciones_expediente=(),
) -> dict:
    """Señales de la Grilla Digital posteriores a `desde` (presentación de la
    oposición), con las combinaciones exactas de GRILLA_* arriba:
      trabajando  -- Indice "Recibo de Ingreso" + Referencia "Escritos de Marcas"
      desistio    -- "Formula Desistimiento" (en Indice o Referencia)
      poder       -- "Acompaña Poder", "Ratifica Gestión en Oposición" o "Ratifica Gestión",
                     solo si es de la fecha de notificación al titular o posterior y NO
                     del mismo día que la presentación de una oposición
      notificada  -- filas vecinas "Vista de Marcas" y "Cédula de Notificación"
                     (fecha_notificacion = la de la primera cédula).
    Cada columna (Indice, Referencia) se compara por "contiene": INPI a veces
    agrega texto extra a la celda.

    Por qué el poder exige notificación previa y no ser del día de una oposición
    (acta 4759596, 08/10/2026): los oponentes acompañan SU poder junto con la
    oposición, el mismo día y antes de que INPI se la notifique al titular; el del
    titular o su gestor llega después, para contestar. Sin notificación conocida (ni
    en la Grilla ni en el expediente) el poder no descarta: ahí manda el
    AGENTE/CARACTER del titular en el expediente."""
    filas = []
    for a in archivos or []:
        f = _fecha_valida(a.get("Fecha") or "")
        if desde and (not f or f < desde):
            continue
        filas.append(a)
    indices = [_norm(a.get("Indice")) for a in filas]
    referencias = [_norm(a.get("Referencia")) for a in filas]
    textos = [f"{i} {r}" for i, r in zip(indices, referencias)]

    # La Grilla viene con lo más nuevo arriba (la cédula aparece ANTES que su vista),
    # así que el par se busca en filas vecinas en cualquier orden. La fecha de
    # notificación es la de la cédula (la más antigua si hay varias).
    fechas = [_fecha_valida(a.get("Fecha") or "") for a in filas]
    notificada, fechas_cedula = False, []
    for k in range(len(filas) - 1):
        for vista, cedula in ((k, k + 1), (k + 1, k)):
            if GRILLA_NOTIF_VISTA in indices[vista] and GRILLA_NOTIF_CEDULA in indices[cedula]:
                notificada = True
                if fechas[cedula]:
                    fechas_cedula.append(fechas[cedula])
    fecha_notificacion = min(fechas_cedula, default=None)

    # Un poder solo descarta si es de la notificación al titular o posterior, y no
    # del mismo día que la presentación de una oposición (poder del oponente).
    referencia = min([f for f in (fecha_notificacion, notificacion_expediente) if f], default=None)
    dias_oposicion = {f for a, f in zip(filas, fechas) if f and es_presentacion_oposicion(a)}
    dias_oposicion.update(f for f in presentaciones_expediente if f)
    poder = bool(referencia) and any(
        term in t and f and f >= referencia and f not in dias_oposicion
        for t, f in zip(textos, fechas) for term in GRILLA_PODER)

    return {
        "trabajando": any(GRILLA_TRABAJANDO[0] in i and GRILLA_TRABAJANDO[1] in r
                          for i, r in zip(indices, referencias)),
        "desistio": any(GRILLA_DESISTIMIENTO in t for t in textos),
        "poder": poder,
        "notificada": notificada,
        "fecha_notificacion": fecha_notificacion,
    }


def _fmt(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else "—"


def _vista_vigente(v: dict, fecha_publicacion: str | None) -> bool:
    """Una vista cuyas fechas son todas anteriores a la publicación vigente ya
    se resolvió con una presentación anterior: no es una alerta nueva."""
    if not fecha_publicacion:
        return True
    fechas = [f for f in (v["fecha"], v["notificacion"], v["vencimiento"], v["contestacion"]) if f]
    return bool(fechas) and max(fechas) >= fecha_publicacion


def clasificar_estado_oposicion(
    exp: dict, archivos: list[dict], fecha_publicacion: str | None, hoy: _dt.date | None = None,
) -> dict:
    """Devuelve el estado de la oposición/vista de un lead (ver estados en el
    docstring del módulo). `fecha_publicacion` es ISO. Campos devueltos:
        estado, sirve (True/False/None), detalle (texto legible),
        presentacion, notificacion, vencimiento, levantamiento,
        agente_oponente, posible_apoderado, representacion_confirmada."""
    hoy = hoy or _dt.date.today()
    hoy_iso = hoy.isoformat()

    # Solo cuentan las oposiciones de la publicación vigente (una anterior,
    # ya resuelta, no es una alerta nueva — mismo criterio que detectar_oposicion).
    opos = [o for o in exp.get("oposiciones", [])
            if not (fecha_publicacion and o["presentacion"] and o["presentacion"] < fecha_publicacion)]
    vistas = exp.get("vistas", [])
    fila_grilla = buscar_fila_oposicion(archivos or [], fecha_publicacion)
    texto_fila = f"{(fila_grilla or {}).get('Indice') or ''} {(fila_grilla or {}).get('Referencia') or ''}".upper()
    fila_opo_grilla = fila_grilla if fila_grilla and "OPO" in texto_fila else None
    fila_vista_grilla = fila_grilla if fila_grilla and not fila_opo_grilla else None

    base = {"estado": "sin_oposicion", "sirve": None, "detalle": "", "presentacion": None,
            "notificacion": None, "vencimiento": None, "levantamiento": None,
            "agente_oponente": None, "posible_apoderado": False, "representacion_confirmada": False}
    senales = _senales_grilla([], None)  # se llena solo si hay oposición

    def contestacion_desde(desde: str | None) -> bool:
        """¿Hay una contestación POSTERIOR a `desde`? Las anteriores son de un
        trámite viejo (ej. una vista contestada antes de publicarse) y no cuentan."""
        if not desde:
            return False
        if any(v["contestacion"] and v["contestacion"] >= desde for v in vistas):
            return True
        return bool(_filas_grilla_posteriores(archivos, TERMINOS_CONTESTACION, desde))

    o = None
    extra = {}
    if opos or fila_opo_grilla:
        # ---- hay una oposición de tercero: es lo único que se evalúa
        desde = (min([x["presentacion"] for x in opos if x["presentacion"]], default=None)
                 or _fecha_valida((fila_opo_grilla or {}).get("Fecha") or "") or fecha_publicacion)
        contestada = contestacion_desde(desde)
        senales = _senales_grilla(
            archivos, desde, min([x["notificacion"] for x in opos if x["notificacion"]], default=None),
            [x["presentacion"] for x in opos])
        # Trabajando = contestación del expediente/grilla o "Recibo de Ingreso" + "Escritos de Marcas"
        trabajando = contestada or senales["trabajando"]

        def estado_de(x):
            if x["levantamiento"] or senales["desistio"]:
                return "levantada"
            if trabajando:
                return "contestada"
            if x["vencimiento"]:
                return "plazo_vencido" if x["vencimiento"] < hoy_iso else "notificada_en_plazo"
            # Notificación efectiva: "Vista de Marcas" seguida de "Cédula de Notificación"
            if x["notificacion"] or senales["notificada"]:
                return "notificada_en_plazo"
            return "sin_notificar"

        orden = ("notificada_en_plazo", "sin_notificar", "plazo_vencido", "contestada", "levantada")
        if opos:
            estado, o = sorted(((estado_de(x), x) for x in opos), key=lambda p: orden.index(p[0]))[0]
        elif senales["desistio"]:
            estado = "levantada"
        elif trabajando:
            estado = "contestada"
        elif senales["notificada"]:
            estado = "notificada_en_plazo"
        else:
            estado = "oposicion_sin_detalle"
    else:
        # ---- sin oposición: solo importa una vista de oficio SIN contestar
        # (la tabla del expediente manda; la fila de la Grilla es el respaldo
        # cuando el expediente no la lista).
        pendientes = [v for v in vistas if _vista_vigente(v, fecha_publicacion) and not v["contestacion"]]
        if pendientes:
            v = max(pendientes, key=lambda x: (x["notificacion"] or "", x["fecha"] or ""))
            estado = "vista_pendiente"
            extra = {"notificacion": v["notificacion"], "vencimiento": v["vencimiento"]}
        elif not vistas and fila_vista_grilla:
            desde = _fecha_valida(fila_vista_grilla.get("Fecha") or "") or fecha_publicacion
            if contestacion_desde(desde):
                return base
            estado = "vista_pendiente"
        else:
            return base  # sin oposición, y las vistas que hubo ya están contestadas o son viejas

    # Representación del titular: GESTION DEL TRAMITE del expediente, o una fila
    # de poder/gestión posterior a la oposición en la Grilla Digital (regla acordada
    # el 08/10/2026: descarta el lead aunque el poder pueda ser del oponente).
    confirmada = titular_con_representante(exp)
    representacion = confirmada or senales["poder"]
    if representacion:
        estado = "con_apoderado"

    res = dict(base)
    res.update({
        "estado": estado,
        "sirve": estado in ESTADOS_QUE_SIRVEN,
        "presentacion": o["presentacion"] if o else None,
        "notificacion": (o["notificacion"] if o else extra.get("notificacion")) or senales["fecha_notificacion"],
        "vencimiento": o["vencimiento"] if o else extra.get("vencimiento"),
        "levantamiento": o["levantamiento"] if o else None,
        "agente_oponente": o["agente_oponente"] if o else None,
        "posible_apoderado": False,  # ya no hay caso ambiguo: el poder de la Grilla descarta
        "representacion_confirmada": representacion,
    })
    res["detalle"] = _detalle_legible(res, exp, hoy_iso)
    return res


def _detalle_legible(r: dict, exp: dict, hoy_iso: str = '') -> str:
    e = r["estado"]
    if e == "sin_notificar":
        t = f"Oposición presentada el {_fmt(r['presentacion'])}; INPI todavía no se la notificó al titular."
    elif e == "notificada_en_plazo":
        t = (f"Oposición notificada el {_fmt(r['notificacion'])}; el titular tiene hasta el "
             f"{_fmt(r['vencimiento'])} para contestar." if r["vencimiento"]
             else f"Oposición notificada el {_fmt(r['notificacion'])}; sin vencimiento cargado todavía.")
    elif e == "plazo_vencido":
        t = f"Venció el plazo para contestar ({_fmt(r['vencimiento'])}) y no hay contestación: riesgo de abandono."
    elif e == "contestada":
        t = "El titular ya contestó la oposición/vista: se está trabajando."
    elif e == "levantada":
        t = f"Oposición levantada{' el ' + _fmt(r['levantamiento']) if r['levantamiento'] else ''}."
    elif e == "con_apoderado":
        if titular_con_representante(exp):
            t = (f"El titular ya tiene representante ({(exp.get('titular_caracter') or 'agente').strip()}"
                 f"{', matrícula ' + exp['titular_matricula'] if exp.get('titular_matricula') else ''}).")
        else:
            t = "Ya hay un gestor/apoderado trabajando la oposición (poder o gestión posterior en la Grilla Digital)."
    elif e == "vista_pendiente":
        if r["notificacion"] and r["vencimiento"]:
            t = (f"Vista de INPI notificada el {_fmt(r['notificacion'])}, sin contestar: el titular tiene hasta el "
                 f"{_fmt(r['vencimiento'])}." if r["vencimiento"] >= hoy_iso else
                 f"Vista de INPI notificada el {_fmt(r['notificacion'])}: venció el plazo ({_fmt(r['vencimiento'])}) sin contestar.")
        else:
            t = "Vista de oficio de INPI sin contestar."
    elif e == "oposicion_sin_detalle":
        t = "Oposición detectada en Grilla Digital; el expediente todavía no la lista en OPOSICIONES."
    else:
        t = ""
    if r["agente_oponente"]:
        t += f" Agente del oponente: {r['agente_oponente']}."
    return t
