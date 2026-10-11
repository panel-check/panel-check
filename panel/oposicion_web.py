"""
«¿Quién se opuso?» de la página /oposicion/ de la web: de CADA oposición que tiene la marca, quién la
presentó y su marca (denominación y clases), SIN los fundamentos (son largos y los explica una persona
del estudio).

Las oposiciones salen de la consulta al expediente de INPI (trae todas); se guardan en `analisis_marca`
(la misma que usa la pestaña Análisis) y se vuelven a pedir si tienen más de 12 horas. Si INPI no
responde, se usa lo último guardado o, en su defecto, la única oposición que el panel ya tenía.

La marca de cada oponente:
  * `marcas.actas_marca_oponente` (actas que cita el fundamento, separadas por coma) o, si el
    fundamento solo dio nombre y número de registro, `marcas.marca_oponente_denominacion`.
  * La clase solo figura en el expediente de INPI de esa marca. Se lee una vez y se guarda en
    `analisis_marca_opuesta` (la misma tabla que usa la pestaña Análisis), así no se vuelve a pedir.

Se intenta traer lo que falta cuando se manda el mail (en segundo plano) y, si todavía falta
cuando la persona abre la página, en ese momento, con un tope de tiempo. Si INPI no responde,
se muestra solo el nombre (si lo tenemos) y la página sigue funcionando: nunca falla por esto.
"""

import logging
import re
import threading
import time

log = logging.getLogger("oposicion_web")

MAX_ACTAS = 3             # marcas del oponente que se consultan por oposición
PRESUPUESTO_SEG = 12.0    # tiempo máximo (total) para consultar INPI mientras la persona espera
REINTENTO_SEG = 600       # tras un fallo con INPI no se vuelve a probar esa acta por este tiempo
PAUSA_SEG = 1.0           # entre un pedido a INPI y el siguiente
MAX_DENOMINACION = 80
MAX_OPOSICIONES = 5       # oposiciones que se muestran
SNAPSHOT_VIGENTE_SEG = 12 * 3600   # una consulta a INPI guardada vale 12 horas

_fallos = {}              # acta -> instante (monotonic) del último fallo
_fallos_lock = threading.Lock()


def actas_de(texto) -> list:
    """['4094053', ...] a partir de «4094053,4099176» (sin repetidas, máximo MAX_ACTAS)."""
    out = []
    for a in re.findall(r"\d{5,8}", str(texto or "")):
        if a not in out:
            out.append(a)
    return out[:MAX_ACTAS]


# «“BRANDLIVE”, Reg. N° 3788090» / «"LUNA" Registro 123456»: marca citada por nombre y registro (sin acta).
_RE_MARCA_REGISTRO = re.compile(
    r'["“]([^"”]{2,60})["”][^"“”0-9]{0,40}?\bReg(?:istros?)?\b\.?\s*(?:N[°ºo]|Nro?\.?)?\s*\.?\s*\d[\d.]{3,}', re.IGNORECASE)


def _sin_puntos_de_miles(texto) -> str:
    """«Acta N° 4.745.291» -> «Acta N° 4745291» (los fundamentos escriben los números con puntos)."""
    return re.sub(r"(?<=\d)\.(?=\d{3}(?!\d))", "", str(texto or ""))


def nombre_oponente(fila) -> str:
    """Quién presentó la oposición (nombre tal cual figura; nunca documento ni CUIT)."""
    return _limpia((fila or {}).get("oponente_nombre"))


def _limpia(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:MAX_DENOMINACION]


def _clave_clase(c: str):
    return (0, int(c)) if c.isdigit() else (1, c)


def agrupar(entradas, nombre_sin_acta=None) -> list:
    """[{denominacion, clases: ['25', '35']}]: una entrada por marca (varias actas de la misma
    marca, una por clase, se juntan). `entradas`: dicts con denominacion y clase."""
    por_marca = {}
    for e in entradas:
        den = _limpia(e.get("denominacion"))
        if not den:
            continue
        item = por_marca.setdefault(den.upper(), {"denominacion": den, "clases": []})
        clase = _limpia(e.get("clase"))
        if clase and clase not in item["clases"]:
            item["clases"].append(clase)
    out = list(por_marca.values())
    for i in out:
        i["clases"].sort(key=_clave_clase)
    if not out and _limpia(nombre_sin_acta):
        out = [{"denominacion": _limpia(nombre_sin_acta), "clases": []}]
    return out


def _leer(conexion, rcur, actas) -> dict:
    import analisis_marca as am
    with conexion() as conn, rcur(conn) as cur:
        return am.leer_opuestas(cur, actas)


def _guardar(conexion, rcur, acta, estado, datos, logo):
    import analisis_marca as am
    with conexion() as conn, rcur(conn) as cur:
        am.guardar_opuesta(cur, acta, estado, datos, logo)
        conn.commit()


def _consultar_inpi(acta, sesion=None):
    import inpi_lead
    return inpi_lead.consultar_marca_opuesta(acta, sesion, timeout=8)


def marcas_opositoras(fila, conexion, rcur, *, leer=None, guardar=None, consultar=None,
                      presupuesto=PRESUPUESTO_SEG, reloj=time.monotonic, pausa=PAUSA_SEG, limite=None) -> list:
    """Marcas del oponente de la oposición `fila` (dict con actas_marca_oponente y
    marca_oponente_denominacion): [{denominacion, clases}]. Trae de INPI las que no estén
    guardadas (con tope de tiempo). Nunca levanta: ante cualquier problema devuelve lo que haya."""
    leer = leer or (lambda actas: _leer(conexion, rcur, actas))
    guardar = guardar or (lambda a, e, d, lg: _guardar(conexion, rcur, a, e, d, lg))
    consultar = consultar or _consultar_inpi
    fila = fila or {}
    nombre = fila.get("marca_oponente_denominacion")
    actas = actas_de(fila.get("actas_marca_oponente"))
    if not actas and not nombre and fila.get("fundamento_oposicion"):
        # La oposición no tiene guardada la marca del oponente (columnas vacías): se la busca en el texto del fundamento.
        try:
            import inpi_lead
            fundamento = _sin_puntos_de_miles(fila["fundamento_oposicion"])
            c = inpi_lead.marcas_opuestas_citadas(fundamento, fila.get("acta"))
            actas = actas_de(",".join(c["actas"]))
            if not actas and c["citada"]:
                nombre = re.sub(r"\s*\(Registro.*$", "", c["citada"]).strip()
            if not actas and not nombre:
                m = _RE_MARCA_REGISTRO.search(fundamento)
                if m:
                    nombre = m.group(1)
        except Exception:  # noqa: BLE001
            log.warning("No se pudo leer la marca del oponente en el fundamento", exc_info=True)
    if not actas:
        return agrupar([], nombre)
    try:
        guardadas = leer(actas)
    except Exception:  # noqa: BLE001 — la página no puede fallar por esto
        log.warning("No se pudo leer el caché de marcas del oponente", exc_info=True)
        guardadas = {}
    limite = limite if limite is not None else reloj() + presupuesto
    pedidos = 0
    for a in actas:
        if a in guardadas:
            continue
        ahora = reloj()
        with _fallos_lock:
            ultimo = _fallos.get(a)
        if (ultimo is not None and ahora - ultimo < REINTENTO_SEG) or ahora >= limite:
            continue
        if pedidos:
            time.sleep(pausa)
        pedidos += 1
        try:
            r = consultar(a)
            if r["estado_consulta"] == "ok":
                guardar(a, "ok", r["datos"], r.get("logo"))
                guardadas[a] = {"estado": "ok", **(r["datos"] or {})}
            elif r["estado_consulta"] == "no_existe":
                guardar(a, "no_existe", {}, None)
                guardadas[a] = {"estado": "no_existe"}
            else:
                raise RuntimeError(r.get("error") or r["estado_consulta"])
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo leer la marca del oponente %s: %s", a, e)
            with _fallos_lock:
                _fallos[a] = reloj()
            break   # si INPI falla o bloquea, insistir con las demás solo demora y empeora: se reintenta más tarde
    entradas = [guardadas[a] for a in actas if a in guardadas and guardadas[a].get("estado") == "ok"]
    return agrupar(entradas, None if entradas else nombre)


def _fecha_vigente(iso) -> bool:
    import datetime as dt
    try:
        d = dt.datetime.fromisoformat(str(iso))
    except ValueError:
        return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - d).total_seconds() < SNAPSHOT_VIGENTE_SEG


def _snapshot(conexion, rcur, acta):
    """(items, vigente): las oposiciones de la última consulta a INPI guardada (puede ser None)."""
    import analisis_marca as am
    with conexion() as conn, rcur(conn) as cur:
        g = am.leer_analisis(cur, acta)
    snap = g.get("oposiciones")
    if isinstance(snap, str):
        try:
            import json
            snap = json.loads(snap)
        except ValueError:
            snap = None
    if not isinstance(snap, dict) or not isinstance(snap.get("items"), list):
        return None, False
    return snap["items"], _fecha_vigente(snap.get("consultado_en"))


def _guardar_snapshot(conexion, rcur, acta, items):
    import analisis_marca as am
    with conexion() as conn, rcur(conn) as cur:
        am.guardar_oposiciones(cur, acta, items)
        conn.commit()


def _oposiciones_inpi(acta):
    import inpi_lead
    return inpi_lead.consultar_oposiciones(acta, None, timeout=8)


def oposiciones(fila, conexion, rcur, *, snapshot=None, guardar_snapshot=None, consultar=None, consultar_marca=None,
                presupuesto=PRESUPUESTO_SEG, reloj=time.monotonic, **kw) -> list:
    """[{oponente, marcas: [{denominacion, clases}]}]: una entrada por cada oposición de la marca.
    Nunca levanta; devuelve lo que haya (puede ser [])."""
    fila = fila or {}
    acta = str(fila.get("acta") or "").strip()
    snapshot = snapshot or (lambda a: _snapshot(conexion, rcur, a))
    guardar_snapshot = guardar_snapshot or (lambda a, it: _guardar_snapshot(conexion, rcur, a, it))
    consultar = consultar or _oposiciones_inpi
    items, vigente = None, False
    if acta:
        try:
            items, vigente = snapshot(acta)
        except Exception:  # noqa: BLE001
            log.warning("No se pudo leer las oposiciones guardadas", exc_info=True)
        if not vigente:
            clave = "op:" + acta
            with _fallos_lock:
                ultimo = _fallos.get(clave)
            if ultimo is None or reloj() - ultimo >= REINTENTO_SEG:
                try:
                    r = consultar(acta)
                    if r["estado_consulta"] == "ok" and r.get("items"):
                        items = [dict(i) for i in r["items"]]
                        guardar_snapshot(acta, items)
                    elif r["estado_consulta"] != "ok":
                        raise RuntimeError(r.get("error") or r["estado_consulta"])
                except Exception as e:  # noqa: BLE001
                    log.warning("No se pudieron leer las oposiciones de INPI (acta %s): %s", acta, e)
                    with _fallos_lock:
                        _fallos[clave] = reloj()
    items = [i for i in (items or []) if isinstance(i, dict)][:MAX_OPOSICIONES]
    unica = len(items) <= 1
    if not items:   # lo que el panel ya sabía de la (única) oposición
        items = [{"oponente": nombre_oponente(fila), "fundamento": fila.get("fundamento_oposicion")}]
    limite = reloj() + presupuesto
    out = []
    for it in items:
        # Las columnas guardadas en la marca corresponden a una sola oposición: solo valen si hay una.
        base = dict(fila) if unica else {"acta": acta}
        base["fundamento_oposicion"] = it.get("fundamento") or (base.get("fundamento_oposicion") if unica else None)
        marcas = marcas_opositoras(base, conexion, rcur, limite=limite, reloj=reloj, consultar=consultar_marca, **kw)
        quien = _limpia(it.get("oponente")) or (nombre_oponente(fila) if unica else "")
        if quien or marcas:
            out.append({"oponente": quien, "marcas": marcas})
    return out


def precalentar(acta, conexion, rcur):
    """Para el mail: deja guardadas las marcas del oponente de esa marca para cuando abran la
    página. Se corre en segundo plano; no levanta."""
    try:
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("SELECT acta, actas_marca_oponente, marca_oponente_denominacion, fundamento_oposicion, oponente_nombre "
                        "FROM marcas WHERE acta = %s", (acta,))
            f = cur.fetchone()
        if f:
            oposiciones(dict(f), conexion, rcur)
    except Exception:  # noqa: BLE001
        log.warning("No se pudo precalentar la marca del oponente (acta %s)", acta, exc_info=True)
