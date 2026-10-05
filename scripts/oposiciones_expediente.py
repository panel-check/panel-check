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
apoderado), no los del titular de la marca. Una fila "Acompaña Poder" en la
Grilla Digital puede ser del abogado del oponente, así que NO alcanza para
decir que el titular ya tiene representante. Lo definitivo es el bloque
GESTION DEL TRAMITE del expediente (AGENTE/CARACTER del titular).

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

"Contestada" se detecta por la tabla de vistas/contestaciones del expediente
o por una fila de Grilla Digital con "CONTEST" posterior a la presentación.
Esa parte NO se pudo verificar todavía con un caso real (las dos actas
disponibles todavía no tenían notificación): si aparece un caso con otra
redacción, agregar el término en TERMINOS_CONTESTACION / TERMINOS_LEVANTAMIENTO.
"""

import datetime as _dt
import json
import re

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
TERMINOS_LEVANTAMIENTO = ("DESIST", "LEVANT", "RETIRA")
TERMINOS_PODER = ("PODER", "RATIFICA")

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
    """Los nombres de campo de VISTAS Y NOTIFICACIONES (CONTESTACIÓN, FECHA,
    FEC NOTIF, TIPO) no están confirmados con un caso real, así que se leen
    por patrón en vez de por nombre exacto."""
    fecha = notif = contestacion = None
    tipo = None
    for k, val in v.items():
        kl = k.lower()
        if "contest" in kl:
            f = _fecha_valida(val)
            contestacion = f or (val.strip() if isinstance(val, str) and val.strip() else contestacion)
        elif "notif" in kl:
            notif = _fecha_valida(val) or notif
        elif kl.startswith("fec") and fecha is None:
            fecha = _fecha_valida(val)
        elif "tipo" in kl and isinstance(val, str):
            tipo = val.strip() or tipo
    return {"fecha": fecha, "notificacion": notif, "contestacion": contestacion, "tipo": tipo}


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


def _fmt(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else "—"


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

    base = {"estado": "sin_oposicion", "sirve": None, "detalle": "", "presentacion": None,
            "notificacion": None, "vencimiento": None, "levantamiento": None,
            "agente_oponente": None, "posible_apoderado": False, "representacion_confirmada": False}
    if not opos and not vistas and not fila_grilla:
        return base

    desde = min([o["presentacion"] for o in opos if o["presentacion"]] or
                [_fecha_valida((fila_grilla or {}).get("Fecha") or "") or fecha_publicacion or ""]) or None

    # ¿Ya contestó? Tabla de vistas/contestaciones o fila de Grilla posterior.
    contestada = any(v["contestacion"] for v in vistas) or bool(
        _filas_grilla_posteriores(archivos, TERMINOS_CONTESTACION, desde))
    levantada_grilla = bool(_filas_grilla_posteriores(archivos, TERMINOS_LEVANTAMIENTO, desde))

    def estado_de(o):
        if o["levantamiento"] or levantada_grilla:
            return "levantada"
        if contestada:
            return "contestada"
        if o["vencimiento"]:
            return "plazo_vencido" if o["vencimiento"] < hoy_iso else "notificada_en_plazo"
        if o["notificacion"]:
            return "notificada_en_plazo"
        return "sin_notificar"

    orden = ("notificada_en_plazo", "sin_notificar", "plazo_vencido", "contestada", "levantada")
    if opos:
        pares = sorted(((estado_de(o), o) for o in opos), key=lambda p: orden.index(p[0]))
        estado, o = pares[0]
    elif fila_grilla and "OPO" in f"{fila_grilla.get('Indice') or ''} {fila_grilla.get('Referencia') or ''}".upper():
        estado, o = ("contestada" if contestada else "levantada" if levantada_grilla
                     else "oposicion_sin_detalle"), None
    else:  # solo una vista de oficio
        estado, o = ("contestada" if contestada else "vista_pendiente"), None

    # Representación del titular: lo definitivo es GESTION DEL TRAMITE.
    confirmada = titular_con_representante(exp)
    posible = False
    if not confirmada:
        posible = bool(_filas_grilla_posteriores(archivos, TERMINOS_PODER, desde))
    if confirmada:
        estado = "con_apoderado"

    res = dict(base)
    res.update({
        "estado": estado,
        "sirve": estado in ESTADOS_QUE_SIRVEN,
        "presentacion": o["presentacion"] if o else None,
        "notificacion": o["notificacion"] if o else None,
        "vencimiento": o["vencimiento"] if o else None,
        "levantamiento": o["levantamiento"] if o else None,
        "agente_oponente": o["agente_oponente"] if o else None,
        "posible_apoderado": posible,
        "representacion_confirmada": confirmada,
    })
    res["detalle"] = _detalle_legible(res, exp)
    return res


def _detalle_legible(r: dict, exp: dict) -> str:
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
        t = (f"El titular ya tiene representante ({(exp.get('titular_caracter') or 'agente').strip()}"
             f"{', matrícula ' + exp['titular_matricula'] if exp.get('titular_matricula') else ''}).")
    elif e == "vista_pendiente":
        t = "Vista de oficio de INPI sin contestación."
    elif e == "oposicion_sin_detalle":
        t = "Oposición detectada en Grilla Digital; el expediente todavía no la lista en OPOSICIONES."
    else:
        t = ""
    if r["posible_apoderado"]:
        t += " Ojo: aparece un poder en la Grilla Digital, pero el titular figura sin agente (puede ser del oponente)."
    if r["agente_oponente"]:
        t += f" Agente del oponente: {r['agente_oponente']}."
    return t
