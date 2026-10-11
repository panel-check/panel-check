"""
«¿Quién se opuso?» de la página /oposicion/ de la web: la marca del oponente (denominación y
clases), SIN los fundamentos (son largos y los explica una persona del estudio).

De dónde sale:
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

_fallos = {}              # acta -> instante (monotonic) del último fallo
_fallos_lock = threading.Lock()


def actas_de(texto) -> list:
    """['4094053', ...] a partir de «4094053,4099176» (sin repetidas, máximo MAX_ACTAS)."""
    out = []
    for a in re.findall(r"\d{5,8}", str(texto or "")):
        if a not in out:
            out.append(a)
    return out[:MAX_ACTAS]


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
                      presupuesto=PRESUPUESTO_SEG, reloj=time.monotonic, pausa=PAUSA_SEG) -> list:
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
            c = inpi_lead.marcas_opuestas_citadas(fila["fundamento_oposicion"], fila.get("acta"))
            actas = actas_de(",".join(c["actas"]))
            if not actas and c["citada"]:
                nombre = re.sub(r"\s*\(Registro.*$", "", c["citada"]).strip()
        except Exception:  # noqa: BLE001
            log.warning("No se pudo leer la marca del oponente en el fundamento", exc_info=True)
    if not actas:
        return agrupar([], nombre)
    try:
        guardadas = leer(actas)
    except Exception:  # noqa: BLE001 — la página no puede fallar por esto
        log.warning("No se pudo leer el caché de marcas del oponente", exc_info=True)
        guardadas = {}
    inicio, pedidos = reloj(), 0
    for a in actas:
        if a in guardadas:
            continue
        ahora = reloj()
        with _fallos_lock:
            ultimo = _fallos.get(a)
        if (ultimo is not None and ahora - ultimo < REINTENTO_SEG) or ahora - inicio >= presupuesto:
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


def precalentar(acta, conexion, rcur):
    """Para el mail: deja guardadas las marcas del oponente de esa marca para cuando abran la
    página. Se corre en segundo plano; no levanta."""
    try:
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("SELECT acta, actas_marca_oponente, marca_oponente_denominacion, fundamento_oposicion "
                        "FROM marcas WHERE acta = %s", (acta,))
            f = cur.fetchone()
        if f:
            marcas_opositoras(dict(f), conexion, rcur)
    except Exception:  # noqa: BLE001
        log.warning("No se pudo precalentar la marca del oponente (acta %s)", acta, exc_info=True)
