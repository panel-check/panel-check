"""
Programador de procesos del panel.

Por qué existe: los horarios (`schedule:`) de GitHub Actions no son confiables. Desde el 25/10/2026
GitHub dispara los de este repo con 3 a 6 horas de atraso, o los saltea (el escaneo de las 18:00 de
ese día no arrancó, y Oposiciones, Cartera y los Avisos de la mañana salían con 5 horas de demora).
El panel (Railway) está siempre prendido y es puntual —el mail de la agenda de las 20:00 ya se manda así—,
así que desde acá se le pide a GitHub que arranque cada workflow a su hora con `workflow_dispatch`, que
no se atrasa. Los workflows programados dejaron de tener `schedule:` (excepto verificar_despliegue.yml,
que solo controla el deploy).

Cómo funciona (un hilo, una pasada cada 30 segundos):
  1. Para cada proceso de PROGRAMADOS calcula su última hora programada (el cron está en UTC, igual que
     en GitHub). Si esa hora ya pasó, está dentro de la ventana de gracia y todavía no se disparó, es
     un disparo pendiente. La ventana de gracia cubre un reinicio del panel o un carril de INPI ocupado.
  2. Antes de disparar mira en GitHub si ese workflow ya corrió desde esa hora (por ejemplo, porque se
     lo lanzó a mano): si ya corrió, no lo dispara de nuevo.
  3. Los procesos del carril de INPI (los que tienen `concurrency: inpi-lane`) se disparan de a uno y
     solo cuando el carril está libre. Así nunca se acumulan varios pendientes (GitHub se queda con el
     último y cancela los demás: pasó con Cartera, Sin verificar y Oposiciones) y el orden es el de la
     hora programada.
  4. Cada disparo queda en la tabla `programador_disparos` (una fila por proceso y hora programada), que
     además evita que dos instancias del panel lo disparen dos veces. Si GitHub rechaza el disparo se
     reintenta cada 2 minutos; si no hay forma, se avisa por mail una sola vez.

Sin FastAPI: lo usan el panel (app.py) y las pruebas.
"""

import datetime as _dt
import os
import threading
import time
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "panel-check/panel-check")

PASADA_SEGUNDOS = 30
REINTENTO_MINUTOS = 2
MAX_INTENTOS = 8
ESPERA_ENTRE_DISPAROS_DEL_CARRIL = 120   # segundos: lo que tarda GitHub en mostrar la corrida como «en cola»

# Workflows que comparten el carril de INPI (`concurrency: group: inpi-lane`). Un test comprueba que esta lista
# sea la de los .yml del repo.
CARRIL_INPI = frozenset({
    "agregar_marca_manual.yml", "backfill_denominacion.yml",
    "backfill_detalle_oposicion.yml", "backfill_fecha_presentacion.yml", "backfill_fecha_publicacion.yml",
    "backfill_reparsear_marca_oponente.yml", "corregir_oposiciones_viejas.yml", "corregir_titular_escaneo.yml",
    "escanear_actas.yml", "estimar_demora_publicacion.yml", "importar_cartera.yml", "inspeccionar_acta.yml",
    "pipeline.yml", "probar_verificador.yml", "probar_vistas_preboletin.yml", "reintentar_sin_email.yml",
    "reintentar_sin_verificar.yml", "repasar_vistas.yml", "revisar_cartera.yml", "revisar_estado.yml",
    "revisar_oposiciones.yml", "verificar_orden_publicacion.yml",
})

# Qué corre y cuándo. `cron`: 5 campos en UTC (minuto hora * * días-de-la-semana, 0 = domingo), como en GitHub.
# `gracia`: minutos después de la hora programada en los que todavía se dispara si no pudo salir antes.
# `inputs`: lo que se le pasa al workflow (en un disparo manual GitHub usa los valores por defecto de cada input).
# La hora en Argentina va al lado; si se cambia una, cambiar la otra y la tarjeta de Automatizaciones
# (que lee los horarios de acá: cron_de()).
PROGRAMADOS = [
    # 18:00  Solicitudes nuevas del día (después dispara sola Vigilancia, que no usa el carril)
    {"workflow": "escanear_actas.yml", "cron": "0 21 * * *", "gracia": 360},
    # 18:05  Revisión diaria de vistas: espera a que el escaneo libere el carril y se corta sola a las 22:40
    {"workflow": "probar_vistas_preboletin.yml", "cron": "5 21 * * *", "gracia": 270,
     "inputs": {"limite": "3000", "max_minutes": "100", "hasta_hora": "22:40", "guardar": "true"}},
    # 22:35  Seguimiento de la cartera
    {"workflow": "revisar_cartera.yml", "cron": "35 1 * * *", "gracia": 360},
    # 23:05  Sin verificar (no los martes a la noche)
    {"workflow": "reintentar_sin_verificar.yml", "cron": "5 2 * * 0,1,2,4,5,6", "gracia": 360},
    # 23:10  Oposiciones: rotación nocturna de 5 boletines
    {"workflow": "revisar_oposiciones.yml", "cron": "10 2 * * *", "gracia": 480},
    # 04:20 lunes  Concesiones y vencimientos
    {"workflow": "revisar_estado.yml", "cron": "20 7 * * 1", "gracia": 480},
    # 05:10  Sin email (no los miércoles de madrugada)
    {"workflow": "reintentar_sin_email.yml", "cron": "10 8 * * 0,1,2,4,5,6", "gracia": 480},
    # 05:50  Sin nombre; la corrida programada hace un tope de 150 marcas
    {"workflow": "backfill_denominacion.yml", "cron": "50 8 * * 0,1,2,4,5,6", "gracia": 480,
     "inputs": {"limit": "150"}},
    # 06:15  Feriados
    {"workflow": "feriados.yml", "cron": "15 9 * * *", "gracia": 240},
    # 07:30  Avisos por mail de la mañana
    {"workflow": "avisos_manana.yml", "cron": "30 10 * * *", "gracia": 240},
    # 14:00 miércoles  Repaso de vistas
    {"workflow": "repasar_vistas.yml", "cron": "0 17 * * 3", "gracia": 360},
]


def cron_de(workflow: str):
    """El cron (UTC) de un workflow programado desde acá; None si no lo programa el panel."""
    return next((p["cron"] for p in PROGRAMADOS if p["workflow"] == workflow), None)


# ── Cron ────────────────────────────────────────────────────────────────
def _campo(texto: str, minimo: int, maximo: int) -> set:
    """Valores de un campo de cron: *, a, a,b,c, a-b, */n. El domingo puede ser 0 o 7."""
    valores = set()
    for parte in texto.split(","):
        paso = 1
        if "/" in parte:
            parte, p = parte.split("/", 1)
            paso = int(p)
        if parte in ("*", ""):
            desde, hasta = minimo, maximo
        elif "-" in parte:
            a, b = parte.split("-", 1)
            desde, hasta = int(a), int(b)
        else:
            desde = hasta = int(parte)
        valores.update(range(desde, hasta + 1, paso))
    return valores


def coincide(expr: str, momento: _dt.datetime) -> bool:
    """¿El minuto de `momento` (en UTC) es uno de los que dice el cron? Solo minuto, hora y día de la semana."""
    minuto, hora, dia_mes, mes, dia_semana = expr.split()
    if dia_mes != "*" or mes != "*":
        raise ValueError(f"cron no soportado (día del mes o mes): {expr}")
    dow = {d % 7 for d in _campo(dia_semana, 0, 7)}
    return (momento.minute in _campo(minuto, 0, 59) and momento.hour in _campo(hora, 0, 23)
            and (momento.isoweekday() % 7) in dow)


def ultima_programada(expr: str, ahora: _dt.datetime, gracia_min: int):
    """El instante (UTC, a la hora en punto del minuto) más reciente <= ahora en que el cron tocaba, dentro de
    los últimos `gracia_min` minutos; None si no hay."""
    ahora = ahora.astimezone(_dt.timezone.utc).replace(second=0, microsecond=0)
    for atras in range(0, gracia_min + 1):
        t = ahora - _dt.timedelta(minutes=atras)
        if coincide(expr, t):
            return t
    return None


def proxima(expr: str, ahora: _dt.datetime, dias: int = 8):
    """El próximo instante (UTC) > ahora en que toca el cron, o None dentro de `dias` días."""
    t = ahora.astimezone(_dt.timezone.utc).replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)
    for _ in range(dias * 1440):
        if coincide(expr, t):
            return t
        t += _dt.timedelta(minutes=1)
    return None


def pendientes(ahora: _dt.datetime, hechos: set, programados=None) -> list:
    """Disparos que tocan ahora y todavía no salieron: [{workflow, programada (UTC), inputs}], ordenados por
    hora programada (y, a igual hora, por el orden de PROGRAMADOS). `hechos`: {(workflow, programada)}."""
    out = []
    for orden, p in enumerate(programados if programados is not None else PROGRAMADOS):
        t = ultima_programada(p["cron"], ahora, p.get("gracia", 360))
        if t is not None and (p["workflow"], t) not in hechos:
            out.append({"workflow": p["workflow"], "programada": t, "inputs": dict(p.get("inputs") or {}), "_orden": orden})
    out.sort(key=lambda d: (d["programada"], d["_orden"]))
    for d in out:
        del d["_orden"]
    return out


# ── GitHub ──────────────────────────────────────────────────────────────
class GitHub:
    """Las tres llamadas que hace el programador. Se cambia por uno falso en las pruebas."""

    def __init__(self, token: str, repo: str = GITHUB_REPO):
        self.token, self.repo = token, repo

    def _headers(self):
        return {"Authorization": f"token {self.token}", "Accept": "application/vnd.github+json"}

    def disparar(self, workflow: str, inputs: dict):
        """(ok, detalle)"""
        cuerpo = {"ref": "main"}
        if inputs:
            cuerpo["inputs"] = inputs
        r = requests.post(f"https://api.github.com/repos/{self.repo}/actions/workflows/{workflow}/dispatches",
                          headers=self._headers(), json=cuerpo, timeout=20)
        if r.status_code == 204:
            return True, "disparado"
        return False, f"GitHub devolvió {r.status_code}: {r.text[:200]}"

    def corrio_desde(self, workflow: str, desde: _dt.datetime) -> bool:
        """¿Hay una corrida de ese workflow (de cualquier tipo, también cancelada) creada desde `desde`?"""
        r = requests.get(f"https://api.github.com/repos/{self.repo}/actions/workflows/{workflow}/runs",
                         headers=self._headers(), params={"per_page": 10}, timeout=20)
        r.raise_for_status()
        for run in r.json().get("workflow_runs", []):
            creada = _dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
            if creada >= desde:
                return True
        return False

    def carril_ocupado(self) -> bool:
        """¿Hay en marcha, en cola o esperando un workflow del carril de INPI? Una corrida «en cola» de hace más de
        12 horas se ignora: un trabajo colgado no puede dejar el carril cerrado para siempre."""
        limite = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=12)
        for estado in ("in_progress", "queued", "waiting", "pending"):
            r = requests.get(f"https://api.github.com/repos/{self.repo}/actions/runs",
                             headers=self._headers(), params={"status": estado, "per_page": 100}, timeout=20)
            r.raise_for_status()
            for run in r.json().get("workflow_runs", []):
                creada = _dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                if creada >= limite and os.path.basename((run.get("path") or "").split("@")[0]) in CARRIL_INPI:
                    return True
        return False


# ── Base ────────────────────────────────────────────────────────────────
CREAR_TABLA = """
CREATE TABLE IF NOT EXISTS programador_disparos (
    workflow        TEXT NOT NULL,
    programada      TIMESTAMPTZ NOT NULL,
    estado          TEXT NOT NULL,           -- enviando | ok | omitido (ya había corrido) | error
    intentos        INTEGER NOT NULL DEFAULT 1,
    detalle         TEXT,
    ultimo_intento  TIMESTAMPTZ NOT NULL DEFAULT now(),
    disparado_en    TIMESTAMPTZ,
    avisado_en      TIMESTAMPTZ,
    PRIMARY KEY (workflow, programada)
)
"""


def crear_tabla(cur):
    cur.execute(CREAR_TABLA)


def hechos(cur, desde: _dt.datetime) -> set:
    """Disparos que ya no hay que intentar: salieron, se omitieron o se agotaron los reintentos."""
    cur.execute("SELECT workflow, programada FROM programador_disparos WHERE programada >= %s "
                "AND (estado IN ('ok', 'omitido') OR (estado = 'error' AND intentos >= %s))", (desde, MAX_INTENTOS))
    return {(w, p.astimezone(_dt.timezone.utc)) for w, p in cur.fetchall()}


def reclamar(cur, workflow: str, programada: _dt.datetime) -> bool:
    """Se queda con el derecho a disparar este (workflow, hora): True si es la primera vez o si toca reintentar
    un error (o un intento que quedó colgado). Con dos instancias del panel, solo una lo consigue."""
    cur.execute(
        """INSERT INTO programador_disparos (workflow, programada, estado, intentos, ultimo_intento)
           VALUES (%s, %s, 'enviando', 1, now())
           ON CONFLICT (workflow, programada) DO UPDATE
              SET estado = 'enviando', intentos = programador_disparos.intentos + 1, ultimo_intento = now()
            WHERE programador_disparos.intentos < %s
              AND ((programador_disparos.estado = 'error' AND programador_disparos.ultimo_intento < now() - make_interval(mins => %s))
                   OR (programador_disparos.estado = 'enviando' AND programador_disparos.ultimo_intento < now() - interval '10 minutes'))
           RETURNING intentos""",
        (workflow, programada, MAX_INTENTOS, REINTENTO_MINUTOS))
    return cur.fetchone() is not None


def anotar(cur, workflow: str, programada: _dt.datetime, estado: str, detalle: str):
    cur.execute("UPDATE programador_disparos SET estado = %s, detalle = %s, "
                "disparado_en = CASE WHEN %s = 'ok' THEN now() ELSE disparado_en END WHERE workflow = %s AND programada = %s",
                (estado, detalle[:500], estado, workflow, programada))


# ── Una pasada ──────────────────────────────────────────────────────────
class Estado:
    """Lo que se acuerda entre pasada y pasada."""

    def __init__(self):
        self.ultimo_disparo_del_carril = 0.0


def pasada(conexion, github, estado: Estado, ahora: _dt.datetime = None, avisar=None, reloj=time.monotonic) -> list:
    """Dispara lo que toque. Devuelve lo que hizo: [(workflow, resultado)]. Nunca levanta: un fallo de GitHub o de la
    base se anota y se reintenta en la próxima pasada."""
    ahora = ahora or _dt.datetime.now(_dt.timezone.utc)
    hecho = []
    with conexion() as conn:
        with conn.cursor() as cur:
            ya = hechos(cur, ahora - _dt.timedelta(days=2))
        conn.commit()
        for d in pendientes(ahora, ya):
            wf, prog = d["workflow"], d["programada"]
            es_carril = wf in CARRIL_INPI
            try:
                if es_carril:
                    if reloj() - estado.ultimo_disparo_del_carril < ESPERA_ENTRE_DISPAROS_DEL_CARRIL:
                        continue   # recién se disparó uno: GitHub todavía no lo muestra en cola
                    if github.carril_ocupado():
                        continue   # el carril está ocupado: este espera su turno (y los de hora posterior, también)
                with conn.cursor() as cur:
                    if not reclamar(cur, wf, prog):
                        conn.commit()
                        continue
                    conn.commit()
                    if github.corrio_desde(wf, prog):
                        anotar(cur, wf, prog, "omitido", "ya había una corrida desde la hora programada")
                        conn.commit()
                        hecho.append((wf, "omitido"))
                        continue
                    ok, detalle = github.disparar(wf, d["inputs"])
                    anotar(cur, wf, prog, "ok" if ok else "error", detalle)
                    conn.commit()
                if ok:
                    if es_carril:
                        estado.ultimo_disparo_del_carril = reloj()
                    hecho.append((wf, "ok"))
                    print(f"[programador] disparado {wf} (programado {prog.astimezone(TZ):%d/%m %H:%M})", flush=True)
                else:
                    hecho.append((wf, "error"))
                    print(f"[programador] no se pudo disparar {wf}: {detalle}", flush=True)
                    _avisar_si_corresponde(conn, wf, prog, detalle, avisar)
            except Exception as e:  # noqa: BLE001 — un proceso con problemas no frena a los otros
                print(f"[programador] falló {wf}: {e}", flush=True)
                try:
                    conn.rollback()
                    with conn.cursor() as cur:   # quedó reclamado: que se reintente en 2 minutos y no en 10
                        anotar(cur, wf, prog, "error", f"{type(e).__name__}: {e}")
                    conn.commit()
                except Exception:  # noqa: BLE001
                    pass
    return hecho


def _avisar_si_corresponde(conn, workflow, programada, detalle, avisar):
    """Un mail por proceso y hora cuando ya fallaron 3 intentos seguidos."""
    if avisar is None:
        return
    with conn.cursor() as cur:
        cur.execute("SELECT intentos, avisado_en FROM programador_disparos WHERE workflow = %s AND programada = %s",
                    (workflow, programada))
        fila = cur.fetchone()
        if not fila or fila[0] < 3 or fila[1] is not None:
            conn.commit()
            return
        try:
            avisar(workflow, programada, detalle)
            cur.execute("UPDATE programador_disparos SET avisado_en = now() WHERE workflow = %s AND programada = %s",
                        (workflow, programada))
        except Exception as e:  # noqa: BLE001
            print(f"[programador] no se pudo mandar el aviso de {workflow}: {e}", flush=True)
    conn.commit()


def avisar_por_mail(workflow, programada, detalle):
    """Aviso interno de que un proceso no pudo arrancar (Resend, mismas variables que el resto de los avisos)."""
    import formularios_core
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("falta RESEND_API_KEY")
    remitente = os.environ.get("RESEND_FROM") or "Avisos Panel <avisos@quieroregistrarmimarca.com.ar>"
    destinatarios = [d.strip() for d in (os.environ.get("NOTIFICAR_A") or "marcas@komunikacion.com.ar").split(",") if d.strip()]
    hora = programada.astimezone(TZ).strftime("%d/%m %H:%M")
    cuerpo = (f"<p><b>El panel no pudo arrancar «{workflow}»</b> (programado para el {hora}, hora de Argentina) después de "
              f"varios intentos.</p><p>{detalle}</p><p>Lo más común: el <code>GITHUB_TOKEN</code> del panel venció o no tiene "
              f"permiso «Actions: Read and write». Mientras no se arregle, ese proceso hay que lanzarlo a mano desde "
              f"GitHub → Actions o con «Correr ahora» en Automatizaciones.</p>")
    formularios_core.enviar_resend(api_key, remitente, destinatarios, f"⚠ No arrancó {workflow}", cuerpo)


def iniciar(conexion, token: str = None):
    """Prende el hilo del programador (una pasada cada 30 s). Sin GITHUB_TOKEN no hace nada."""
    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[programador] sin GITHUB_TOKEN: los procesos programados NO se van a disparar desde el panel")
        return None
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '3s'")
            crear_tabla(cur)
        conn.commit()
    github, estado = GitHub(token), Estado()

    def bucle():
        time.sleep(20)
        while True:
            try:
                pasada(conexion, github, estado, avisar=avisar_por_mail)
            except Exception as e:  # noqa: BLE001
                print(f"[programador] falló la pasada: {e}", flush=True)
            time.sleep(PASADA_SEGUNDOS)

    hilo = threading.Thread(target=bucle, name="programador", daemon=True)
    hilo.start()
    print("[programador] prendido: dispara los procesos programados desde el panel", flush=True)
    return hilo


def estado_para_panel(cur, ahora: _dt.datetime = None) -> dict:
    """Para mostrar en el panel: los disparos de los últimos 3 días y la próxima hora de cada proceso."""
    ahora = ahora or _dt.datetime.now(_dt.timezone.utc)
    cur.execute("SELECT workflow, programada, estado, intentos, detalle, disparado_en FROM programador_disparos "
                "WHERE programada >= %s ORDER BY programada DESC", (ahora - _dt.timedelta(days=3),))
    ultimos = [{"workflow": w, "programada": p.astimezone(TZ).isoformat(), "estado": e, "intentos": i, "detalle": d,
                "disparado_en": de.astimezone(TZ).isoformat() if de else None} for w, p, e, i, d, de in cur.fetchall()]
    prox = []
    for p in PROGRAMADOS:
        t = proxima(p["cron"], ahora)
        prox.append({"workflow": p["workflow"], "proxima": t.astimezone(TZ).isoformat() if t else None})
    return {"ultimos": ultimos, "proximos": prox}
