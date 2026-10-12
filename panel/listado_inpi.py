"""
Listado de boletines "MARCAS NUEVAS" que publica INPI, guardado en la base del panel
(tablas boletines_inpi y boletines_inpi_estado) para la sección /boletines.

Por qué existe: el historial no cambia, así que no se vuelve a pedir a INPI en cada
deploy ni cada tantos minutos. INPI publica los boletines nuevos los MIÉRCOLES entre
las 7 y las 10 hs (hora Argentina). El panel:

  - arma el historial una sola vez (desde inpi_lead.BOLETINES_DESDE, mes por mes);
  - después solo revisa los últimos 2 meses: cada 15 minutos entre las 7 y las 11 del
    miércoles, y una última vez cuando cierra esa franja (11 hs). Si el panel estuvo
    apagado en esa franja, lo recupera la próxima vez que alguien abre la pantalla;
  - la pantalla nunca espera a INPI: lee de la base y, si hace falta actualizar, lo
    hace en un hilo aparte (más un botón «Actualizar» para forzarlo a mano).

Recibe la función `conexion` del panel (context manager que da una conexión psycopg2),
igual que los otros módulos, para no importar app.py.
"""

import re
import threading
from datetime import date, datetime, time as hora, timedelta, timezone

import requests

import inpi_lead

ART = timezone(timedelta(hours=-3))
DIA_PUBLICACION = 2          # miércoles (lunes = 0)
VENTANA_DESDE = hora(7, 0)   # INPI publica entre las 7 y las 10
VENTANA_HASTA = hora(11, 0)  # se deja una hora de margen
REVISAR_CADA = timedelta(minutes=15)   # dentro de la franja
REINTENTAR_ERROR = timedelta(minutes=5)  # si INPI falla, no insistir antes

_mutex = threading.Lock()
_en_curso = False
_ultimo_error: dict = {"texto": None, "cuando": None}


def crear_tablas(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS boletines_inpi (
            numero TEXT PRIMARY KEY,
            fecha  DATE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS boletines_inpi_estado (
            id               INTEGER PRIMARY KEY DEFAULT 1,
            actualizado_en   TIMESTAMPTZ,   -- último chequeo exitoso con INPI
            historial_desde  DATE           -- hasta dónde llega el historial ya guardado
        )
    """)
    cur.execute("INSERT INTO boletines_inpi_estado (id) VALUES (1) ON CONFLICT DO NOTHING")


# --- Cuándo hay que volver a preguntarle a INPI ---------------------------------------

def ultima_franja(ahora: datetime):
    """(inicio, fin) de la franja de publicación más reciente (miércoles 7 a 11 hs ART)
    que ya empezó. Antes de las 7 del miércoles es la de la semana anterior."""
    ahora = ahora.astimezone(ART)
    atras = (ahora.weekday() - DIA_PUBLICACION) % 7
    dia = (ahora - timedelta(days=atras)).date()
    inicio = datetime.combine(dia, VENTANA_DESDE, tzinfo=ART)
    if inicio > ahora:
        inicio -= timedelta(days=7)
    fin = datetime.combine(inicio.date(), VENTANA_HASTA, tzinfo=ART)
    return inicio, fin


def necesita_actualizar(ahora: datetime, ultimo) -> bool:
    """¿Toca volver a consultar a INPI? `ultimo` es el último chequeo exitoso (o None)."""
    if ultimo is None:
        return True
    _, fin = ultima_franja(ahora)
    if ahora < fin:                      # dentro de la franja: cada 15 minutos
        return ahora - ultimo >= REVISAR_CADA
    return ultimo < fin                  # franja cerrada: falta el chequeo final


# --- Base -----------------------------------------------------------------------------

def _leer(conexion):
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT numero, fecha FROM boletines_inpi")
            filas = [{"numero": n, "fecha": f} for n, f in cur.fetchall()]
            cur.execute("SELECT actualizado_en, historial_desde FROM boletines_inpi_estado WHERE id = 1")
            est = cur.fetchone() or (None, None)
    return filas, est[0], est[1]


def _fecha_inpi(texto):
    """'29/07/2026' -> date(2026, 7, 29); None si no se entiende."""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", texto or "")
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _guardar(conexion, boletines, historial_desde=None):
    with conexion() as conn:
        with conn.cursor() as cur:
            for b in boletines:
                fecha = _fecha_inpi(b.get("fecha"))
                cur.execute(
                    "INSERT INTO boletines_inpi (numero, fecha) VALUES (%s, %s) "
                    "ON CONFLICT (numero) DO UPDATE SET fecha = COALESCE(EXCLUDED.fecha, boletines_inpi.fecha)",
                    (str(b["numero"]), fecha),
                )
            if historial_desde:
                cur.execute("UPDATE boletines_inpi_estado SET actualizado_en = now(), "
                            "historial_desde = %s WHERE id = 1", (historial_desde,))
            else:
                cur.execute("UPDATE boletines_inpi_estado SET actualizado_en = now() WHERE id = 1")
        conn.commit()


# --- Actualización --------------------------------------------------------------------

def _historial_completo(hist_desde) -> bool:
    return hist_desde is not None and hist_desde <= date.fromisoformat(inpi_lead.BOLETINES_DESDE)


def _trabajo(conexion, completo: bool, timeout: int = 20):
    """Consulta a INPI y guarda. completo=True arma todo el historial; si no, solo los
    últimos 2 meses (1 a 3 consultas)."""
    global _en_curso
    try:
        hoy = datetime.now(ART).date()
        if completo:
            desde = inpi_lead.BOLETINES_DESDE
        else:
            desde = (hoy.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()
        boletines = inpi_lead.traer_listado(timeout, desde, estricto=True)
        _guardar(conexion, boletines, historial_desde=desde if completo else None)
        _ultimo_error.update(texto=None, cuando=None)
    except Exception as e:  # noqa: BLE001 - el hilo no debe morir en silencio
        _ultimo_error.update(texto=str(e), cuando=datetime.now(ART))
        print(f"[listado_inpi] no se pudo actualizar el listado de INPI: {e}")
    finally:
        with _mutex:
            _en_curso = False


def _lanzar(conexion, completo: bool) -> bool:
    global _en_curso
    with _mutex:
        if _en_curso:
            return False
        _en_curso = True
    threading.Thread(target=_trabajo, args=(conexion, completo), daemon=True).start()
    return True


def listado(conexion):
    """(boletines, cargando, error, actualizado_en). Lee de la base y NUNCA espera a INPI;
    si toca actualizar (ver necesita_actualizar) lo hace en un hilo aparte.
    `cargando`: todavía no hay nada guardado y se está armando (primera vez)."""
    filas, ultimo, hist_desde = _leer(conexion)
    completo = not _historial_completo(hist_desde)
    ahora = datetime.now(ART)
    reciente_error = (_ultimo_error["cuando"] is not None
                      and ahora - _ultimo_error["cuando"] < REINTENTAR_ERROR)
    lanzado = False
    if (completo or necesita_actualizar(ahora, ultimo)) and not reciente_error:
        lanzado = _lanzar(conexion, completo)
    # Si la base estaba vacía y la carga recién se lanzó (o sigue en curso), la pantalla debe
    # reintentar aunque el hilo ya haya terminado: lo leído arriba quedó viejo.
    cargando = not filas and (lanzado or _en_curso)
    error = _ultimo_error["texto"] if not filas else None
    return filas, cargando, error, ultimo


def actualizar_ahora(conexion, timeout: int = 20) -> dict:
    """Botón «Actualizar»: revisa los últimos 2 meses ahora mismo y espera el resultado."""
    global _en_curso
    with _mutex:
        if _en_curso:
            return {"ok": False, "error": "Ya hay una actualización en curso, probá en unos segundos."}
        _en_curso = True
    _, _, hist_desde = _leer(conexion)
    _trabajo(conexion, completo=not _historial_completo(hist_desde), timeout=timeout)
    if _ultimo_error["texto"]:
        return {"ok": False, "error": f"No se pudo consultar INPI ({_ultimo_error['texto']})."}
    return {"ok": True, "error": None}
