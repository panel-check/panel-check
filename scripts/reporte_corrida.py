"""
Reporte de corridas por Telegram (08/11/2026).

Cada vez que termina un workflow del repo (programado por el panel, encadenado o lanzado a mano), el
workflow «Reporte de corridas» (reporte_corridas.yml, evento `workflow_run`) corre este script, que
analiza esa corrida y manda el resultado a un chat de Telegram:

  - si terminó bien, falló o se canceló; si falló, en qué paso y las últimas líneas de error del log;
  - de dónde salió: programada por el panel (y con cuánta demora arrancó), encadenada o manual;
  - cuánto esperó en cola (carril de INPI) y cuánto tardó, comparado con la mediana de las últimas
    corridas exitosas del mismo workflow (aviso si tardó bastante más, o mucho menos, de lo normal);
  - lo que anotó la corrida en `registro_corridas` (leads, emails, revisadas, bloqueos…), cada número
    comparado con la mediana de sus corridas anteriores;
  - lo que cambió en la base mientras corría: marcas y leads cargados, oposiciones y vistas detectadas
    (en total y en leads), marcas revisadas. Es por ventana de tiempo: si otro proceso corrió a la vez,
    también entra lo suyo (se avisa).

Cada reporte queda en la tabla `reportes_corridas` (una fila por corrida e intento): sirve de historial
para comparar y evita mandar dos veces el mismo. «Panel actualizado» corre cada hora: solo se reporta si
falla. El análisis es por reglas, sin IA.

El mensaje va al tema «🔄 Corridas» del grupo de Telegram (ver telegram_avisos.py).

Configuración (secrets de GitHub): TELEGRAM_BOT_TOKEN (el token que da @BotFather). TELEGRAM_CHAT_ID es
opcional: si no está, el script toma el chat que le escribió /start al bot (puede ser un grupo) y lo
guarda en `reportes_config`. Sin token no manda nada, pero guarda el reporte igual.

No imprime nombres ni datos de personas en el log (el repo es público): solo números y estados.

Uso: python3 reporte_corrida.py                   (lee el evento workflow_run de GITHUB_EVENT_PATH)
     python3 reporte_corrida.py --run-id 123456   (reporta esa corrida; para probar o repetir)
     python3 reporte_corrida.py --prueba          (crea los temas del grupo y deja un mensaje en cada uno)
"""

import argparse
import ast
import datetime as _dt
import html
import json
import os
import re
import statistics
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import telegram_avisos
from telegram_avisos import chat_de_actualizaciones, limpiar_token  # noqa: F401 (las usan las pruebas)

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
REPO = os.environ.get("GITHUB_REPOSITORY", "panel-check/panel-check")
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
RAIZ = Path(__file__).resolve().parent.parent

# Corren muy seguido: solo se reportan si no terminan bien.
SOLO_SI_FALLA = {"verificar_despliegue.yml", "avisos_telegram.yml"}
# Este mismo workflow (nunca se reporta a sí mismo).
PROPIO = "reporte_corridas.yml"

# Umbrales del análisis.
MIN_HISTORIAL = 3            # corridas anteriores necesarias para comparar
LENTO_FACTOR = 1.5           # «tardó más de lo normal» si supera 1,5 veces la mediana…
LENTO_MINIMO_S = 5 * 60      # …y además por lo menos 5 minutos más
RAPIDO_FACTOR = 0.4          # «terminó demasiado rápido» si dura menos del 40 % de la mediana…
RAPIDO_MEDIANA_MIN_S = 5 * 60  # …en procesos que normalmente duran 5 minutos o más
ESPERA_AVISO_S = 15 * 60     # esperó en cola más de 15 minutos
DEMORA_AVISO_S = 20 * 60     # arrancó más de 20 minutos después de su hora programada
CAIDA_FACTOR = 0.4           # un número cae a menos del 40 % de su mediana
SUBA_FACTOR = 2.5            # un número sube a más de 2,5 veces su mediana
CAIDA_MEDIANA_MIN = 3        # solo se compara si la mediana es de 3 o más

# Métricas que no se comparan con el historial (son identificadores o acumulados).
NO_COMPARAR = {"ultima_acta", "boletin", "marcas_vigiladas", "pendientes_restantes", "siguen"}
# Métricas que son una alerta con solo ser mayores que cero.
ALERTA_SI_HAY = {"bloqueos"}

ETIQUETAS_BASE = {
    "marcas_creadas": "Marcas nuevas cargadas",
    "leads_nuevos": "Leads nuevos",
    "leads_con_email": "Leads nuevos con email",
    "oposiciones_nuevas": "Oposiciones detectadas",
    "oposiciones_en_leads": "Oposiciones en leads",
    "vistas_nuevas": "Vistas detectadas",
    "vistas_en_leads": "Vistas en leads",
    "revisadas_oposicion": "Marcas revisadas (oposiciones)",
    "revisadas_vista": "Marcas revisadas (vistas)",
}

CREAR_TABLAS = """
CREATE TABLE IF NOT EXISTS reportes_corridas (
    run_id        BIGINT NOT NULL,
    intento       INTEGER NOT NULL DEFAULT 1,
    workflow      TEXT NOT NULL,
    nombre        TEXT,
    conclusion    TEXT,
    origen        TEXT,
    creada        TIMESTAMPTZ,
    inicio        TIMESTAMPTZ,
    fin           TIMESTAMPTZ,
    duracion_s    INTEGER,
    espera_s      INTEGER,
    metricas      JSONB NOT NULL DEFAULT '{}'::jsonb,
    base          JSONB NOT NULL DEFAULT '{}'::jsonb,
    alertas       JSONB NOT NULL DEFAULT '[]'::jsonb,
    mensaje       TEXT,
    enviado       BOOLEAN NOT NULL DEFAULT false,
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, intento)
);
CREATE INDEX IF NOT EXISTS idx_reportes_corridas_workflow ON reportes_corridas(workflow, fin DESC);
CREATE TABLE IF NOT EXISTS reportes_config (clave TEXT PRIMARY KEY, valor TEXT);
"""


# ── Utilidades puras (sin red ni base: las prueban los tests) ─────────────────────────────────────

def parse_ts(valor):
    """'2026-11-08T23:10:05Z' → datetime con zona (UTC). None si viene vacío."""
    if not valor:
        return None
    if isinstance(valor, _dt.datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=_dt.timezone.utc)
    return _dt.datetime.fromisoformat(str(valor).replace("Z", "+00:00"))


def hora_ar(ts) -> str:
    """Hora argentina corta; con fecha si no es de hoy."""
    if ts is None:
        return "?"
    local = ts.astimezone(TZ)
    hoy = _dt.datetime.now(TZ).date()
    if local.date() == hoy:
        return local.strftime("%H:%M")
    return local.strftime("%d/%m %H:%M")


def duracion_txt(segundos) -> str:
    if segundos is None:
        return "?"
    s = int(round(segundos))
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min" if s < 30 or m >= 10 else f"{m} min {s} s"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def numero_txt(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int):
        return f"{v:,}".replace(",", ".")
    if isinstance(v, float):
        return f"{v:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(v)


def mediana(valores):
    nums = [float(v) for v in valores if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return statistics.median(nums) if nums else None


def humanizar(clave: str) -> str:
    return clave.replace("_", " ").capitalize()


def etiquetas_registro(app_py: Path = RAIZ / "panel" / "app.py") -> dict:
    """Etiquetas legibles de las métricas de registro_corridas, leídas de REPORTES_AUTOMATIZACIONES en
    panel/app.py (sin importar el panel): {workflow: {clave: etiqueta}}. Vacío si no se puede leer."""
    try:
        arbol = ast.parse(app_py.read_text(encoding="utf-8"))
    except Exception:
        return {}
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "REPORTES_AUTOMATIZACIONES" for t in nodo.targets
        ) and isinstance(nodo.value, ast.Dict):
            salida = {}
            for k, v in zip(nodo.value.keys, nodo.value.values):
                if not isinstance(k, ast.Constant) or not isinstance(v, ast.Dict):
                    continue
                for kk, vv in zip(v.keys, v.values):
                    if isinstance(kk, ast.Constant) and kk.value == "columnas":
                        try:
                            columnas = ast.literal_eval(vv)
                        except Exception:
                            continue
                        salida[k.value] = {c[0]: c[1] for c in columnas}
            return salida
    return {}


def origen_de(run: dict, disparo) -> str:
    """De dónde salió la corrida. `disparo` = hora programada (datetime) si la lanzó el programador del
    panel, None si no."""
    actor = ((run.get("triggering_actor") or run.get("actor") or {}).get("login") or "").lower()
    evento = run.get("event") or ""
    if disparo is not None:
        return f"programada ({hora_ar(disparo)})"
    if evento == "schedule":
        return "programada (horario de GitHub)"
    if evento in ("push", "pull_request"):
        return f"por un {evento}"
    if "github-actions" in actor or evento == "workflow_run":
        return "encadenada (la lanzó otro proceso)"
    return "manual"


def comparar_metricas(actuales: dict, historial: list, etiquetas: dict | None = None) -> tuple[list, list]:
    """Compara cada número con la mediana de las corridas anteriores.
    Devuelve (líneas [(etiqueta, valor, mediana|None)], alertas [texto])."""
    etiquetas = etiquetas or {}
    lineas, alertas = [], []
    for clave, valor in actuales.items():
        if isinstance(valor, bool) or valor is None:
            continue
        etiqueta = etiquetas.get(clave) or ETIQUETAS_BASE.get(clave) or humanizar(clave)
        if not isinstance(valor, (int, float)) or clave in NO_COMPARAR:
            lineas.append((etiqueta, valor, None))
            continue
        previos = [h.get(clave) for h in historial if isinstance(h, dict)]
        previos = [p for p in previos if isinstance(p, (int, float)) and not isinstance(p, bool)]
        med = mediana(previos) if len(previos) >= MIN_HISTORIAL else None
        lineas.append((etiqueta, valor, med))
        if clave in ALERTA_SI_HAY:
            if valor > 0:
                alertas.append(f"{etiqueta}: {numero_txt(valor)} (el INPI frenó consultas)")
            continue
        if med is None or med < CAIDA_MEDIANA_MIN:
            continue
        if valor == 0:
            alertas.append(f"{etiqueta}: 0, cuando lo normal es ~{numero_txt(round(med))}")
        elif valor < med * CAIDA_FACTOR:
            alertas.append(f"{etiqueta}: {numero_txt(valor)}, bastante menos que lo normal (~{numero_txt(round(med))})")
        elif valor > med * SUBA_FACTOR:
            alertas.append(f"{etiqueta}: {numero_txt(valor)}, bastante más que lo normal (~{numero_txt(round(med))})")
    return lineas, alertas


def analizar_tiempos(duracion_s, espera_s, demora_s, duraciones_previas: list) -> tuple[str | None, list]:
    """Comparación de la duración con la mediana. Devuelve (texto de lo normal, alertas)."""
    alertas = []
    normal_txt = None
    med = mediana(duraciones_previas) if len(duraciones_previas) >= MIN_HISTORIAL else None
    if med and duracion_s is not None:
        relacion = duracion_s / med if med else None
        normal_txt = f"lo normal es ~{duracion_txt(med)}"
        if duracion_s > med * LENTO_FACTOR and duracion_s - med >= LENTO_MINIMO_S:
            alertas.append(f"Tardó {numero_txt(round(relacion, 1))} veces lo normal "
                           f"({duracion_txt(duracion_s)} contra ~{duracion_txt(med)})")
        elif med >= RAPIDO_MEDIANA_MIN_S and duracion_s < med * RAPIDO_FACTOR:
            alertas.append(f"Terminó mucho más rápido que lo normal ({duracion_txt(duracion_s)} contra "
                           f"~{duracion_txt(med)}): puede que no haya tenido nada para hacer o que se haya cortado")
        else:
            pct = round((relacion - 1) * 100)
            if abs(pct) >= 5:
                normal_txt += f" ({'+' if pct > 0 else ''}{pct} %)"
    elif duracion_s is not None:
        normal_txt = "todavía no hay historial para comparar"
    if espera_s is not None and espera_s > ESPERA_AVISO_S:
        alertas.append(f"Esperó {duracion_txt(espera_s)} en cola antes de arrancar (carril ocupado)")
    if demora_s is not None and demora_s > DEMORA_AVISO_S:
        alertas.append(f"Arrancó {duracion_txt(demora_s)} después de su hora programada")
    return normal_txt, alertas


def extraer_error(log: str, max_lineas: int = 12, max_chars: int = 900) -> str:
    """Las líneas útiles del final de un log de GitHub Actions: errores, tracebacks y el final."""
    if not log:
        return ""
    lineas = []
    for linea in log.splitlines():
        linea = re.sub(r"^\d{4}-\d\d-\d\dT[\d:.]+Z\s?", "", linea).rstrip()
        if not linea or linea.startswith("##[group]") or linea.startswith("##[endgroup]"):
            continue
        lineas.append(linea)
    utiles = [i for i, l in enumerate(lineas)
              if re.search(r"(##\[error\]|::error|Traceback|Error|Exception|exit code|Errno)", l)]
    if utiles:
        desde = max(0, utiles[0] - 1)
        hasta = utiles[-1] + 1
        recorte = lineas[desde:hasta]
        if len(recorte) > max_lineas:
            recorte = recorte[:3] + ["…"] + recorte[-(max_lineas - 4):]
    else:
        recorte = lineas[-max_lineas:]
    texto = "\n".join(recorte)
    return texto if len(texto) <= max_chars else "…" + texto[-max_chars:]


ESTADOS = {
    "success": ("✅", "terminó bien"),
    "failure": ("❌", "FALLÓ"),
    "cancelled": ("⚪️", "se canceló"),
    "timed_out": ("⏰", "se cortó por tiempo"),
    "startup_failure": ("❌", "no pudo arrancar"),
    "action_required": ("✋", "quedó esperando aprobación"),
    "neutral": ("➖", "terminó sin resultado"),
    "stale": ("➖", "quedó vieja"),
}


def armar_mensaje(d: dict) -> str:
    """Texto del mensaje de Telegram (HTML). `d` trae todo lo calculado."""
    e = html.escape
    icono, estado = ESTADOS.get(d["conclusion"], ("❔", d["conclusion"] or "terminó"))
    partes = [f"{icono} <b>{e(d['nombre'])}</b> — {estado}"]
    if d.get("intento", 1) > 1:
        partes[0] += f" (reintento {d['intento']})"

    t = [f"Origen: {e(d['origen'])}"]
    if d.get("actor") and d["origen"] == "manual":
        t[0] += f" por {e(d['actor'])}"
    t.append(f"Arrancó {hora_ar(d['inicio'])} · terminó {hora_ar(d['fin'])}")
    dur = f"Duró {duracion_txt(d['duracion_s'])}"
    if d.get("normal_txt"):
        dur += f" — {e(d['normal_txt'])}"
    t.append(dur)
    if d.get("espera_s") and d["espera_s"] >= 60:
        t.append(f"Esperó en cola {duracion_txt(d['espera_s'])}")
    partes.append("\n".join(t))

    if d.get("falla"):
        partes.append(f"<b>Dónde falló:</b> {e(d['falla'])}")
    if d.get("error"):
        partes.append(f"<pre>{e(d['error'])}</pre>")

    if d.get("lineas_registro"):
        filas = ["<b>Lo que hizo</b>"]
        for etiqueta, valor, med in d["lineas_registro"]:
            fila = f"• {e(etiqueta)}: {e(numero_txt(valor))}"
            if med is not None:
                fila += f" <i>(normal ~{e(numero_txt(round(med)))})</i>"
            filas.append(fila)
        partes.append("\n".join(filas))

    if d.get("lineas_base"):
        filas = ["<b>En la base mientras corría</b>"]
        for etiqueta, valor, med in d["lineas_base"]:
            fila = f"• {e(etiqueta)}: {e(numero_txt(valor))}"
            if med is not None:
                fila += f" <i>(normal ~{e(numero_txt(round(med)))})</i>"
            filas.append(fila)
        if d.get("solapadas"):
            filas.append(f"<i>Corrieron a la vez: {e(', '.join(d['solapadas']))} (los números incluyen lo suyo)</i>")
        partes.append("\n".join(filas))
    elif d.get("sin_base"):
        partes.append("<i>Sin cambios en marcas, leads, oposiciones ni vistas.</i>")

    alertas = d.get("alertas") or []
    if alertas:
        partes.append("<b>⚠️ Para mirar</b>\n" + "\n".join(f"• {e(a)}" for a in alertas))
    elif d["conclusion"] == "success":
        partes.append("👍 Todo dentro de lo normal.")

    partes.append(f'<a href="{e(d["url"])}">Ver la corrida en GitHub</a>')
    texto = "\n\n".join(partes)
    if len(texto) > 4000:   # límite de Telegram: 4096
        texto = texto[:3990] + "…"
    return texto


def aviso_sistema(d: dict) -> str | None:
    """Aviso corto para el tema «🚨 Sistema» cuando una corrida no termina bien (el detalle queda en
    «🔄 Corridas»). El bloqueo masivo de INPI (monitor_bloqueo.py deja la corrida en rojo) va destacado."""
    if d["conclusion"] in ("success", "skipped", None):
        return None
    texto_error = f"{d.get('falla') or ''}\n{d.get('error') or ''}"
    url = html.escape(d.get("url") or "")
    if "BLOQUEO MASIVO" in texto_error.upper():
        m = re.search(r"BLOQUEO MASIVO DE INPI[^:]*:\s*(.+)", texto_error)
        detalle = f"\n{html.escape(m.group(1).strip()[:200])}" if m else ""
        return (f"🛑 <b>INPI está bloqueando las consultas en forma masiva</b>\n"
                f"Proceso: {html.escape(d['nombre'])}{detalle}\n"
                f"Lo ya hecho quedó guardado; el resto se reintenta en la próxima corrida.\n"
                f'<a href="{url}">Ver la corrida</a>')
    icono, estado = ESTADOS.get(d["conclusion"], ("❔", d["conclusion"]))
    donde = f"\nDónde: {html.escape(d['falla'])}" if d.get("falla") else ""
    return (f"{icono} <b>{html.escape(d['nombre'])}</b> {estado}{donde}\n"
            f'El detalle está en 🔄 Corridas · <a href="{url}">ver la corrida</a>')


# ── GitHub ─────────────────────────────────────────────────────────────────────────────────────────

def _gh(ruta, **params):
    token = os.environ.get("GITHUB_TOKEN")
    cab = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        cab["Authorization"] = f"Bearer {token}"
    r = requests.get(f"{API}{ruta}", headers=cab, params=params or None, timeout=30)
    r.raise_for_status()
    return r


def duraciones_previas(workflow_id, run_id_actual, maximo=20) -> list:
    """Duraciones (s) de las últimas corridas exitosas del mismo workflow, sin la actual."""
    try:
        datos = _gh(f"/repos/{REPO}/actions/workflows/{workflow_id}/runs",
                    status="success", per_page=40).json()
    except Exception as ex:
        print(f"::warning::No se pudo leer el historial de duraciones ({type(ex).__name__})")
        return []
    salida = []
    for r in datos.get("workflow_runs", []):
        if r.get("id") == run_id_actual:
            continue
        ini, fin = parse_ts(r.get("run_started_at")), parse_ts(r.get("updated_at"))
        if ini and fin and fin > ini:
            salida.append((fin - ini).total_seconds())
        if len(salida) >= maximo:
            break
    return salida


def detalle_falla(run: dict) -> tuple[str | None, str | None]:
    """(«job › paso», últimas líneas de error) de la primera parte que falló."""
    try:
        jobs = _gh(f"/repos/{REPO}/actions/runs/{run['id']}/attempts/{run.get('run_attempt', 1)}/jobs",
                   per_page=50).json().get("jobs", [])
    except Exception as ex:
        print(f"::warning::No se pudieron leer los pasos de la corrida ({type(ex).__name__})")
        return None, None
    for job in jobs:
        if job.get("conclusion") in ("failure", "timed_out", "cancelled", "startup_failure"):
            pasos = [s["name"] for s in job.get("steps", [])
                     if s.get("conclusion") in ("failure", "timed_out", "cancelled")]
            donde = job.get("name", "?") + (f" › {pasos[0]}" if pasos else "")
            if job.get("conclusion") == "cancelled" and not pasos:
                donde += " (cancelado antes de terminar)"
            error = None
            try:
                error = extraer_error(_gh(f"/repos/{REPO}/actions/jobs/{job['id']}/logs").text)
            except Exception:
                pass
            return donde, error
    return None, None


def solapadas(run: dict, inicio, fin) -> list:
    """Nombres de otras corridas que estuvieron en marcha a la vez (afectan la ventana de la base)."""
    if not inicio or not fin:
        return []
    try:
        desde = (inicio - _dt.timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
        datos = _gh(f"/repos/{REPO}/actions/runs", created=f">={desde}", per_page=100).json()
    except Exception:
        return []
    nombres = []
    for r in datos.get("workflow_runs", []):
        archivo = Path(r.get("path") or "").name
        if r.get("id") == run.get("id") or archivo in (PROPIO, "verificar_despliegue.yml"):
            continue
        ri, rf = parse_ts(r.get("run_started_at")), parse_ts(r.get("updated_at"))
        if r.get("status") != "completed":
            rf = _dt.datetime.now(_dt.timezone.utc)
        if ri and rf and ri < fin and rf > inicio and r.get("name") not in nombres:
            nombres.append(r.get("name"))
    return nombres


# ── Base ───────────────────────────────────────────────────────────────────────────────────────────

def conectar():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        return None
    import psycopg2
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(CREAR_TABLAS)
    return conn


def hora_programada(conn, workflow, creada):
    """Si el programador del panel disparó este workflow justo antes de que se creara la corrida,
    la hora programada (si no, None)."""
    if conn is None or creada is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT programada FROM programador_disparos
                   WHERE workflow = %s AND disparado_en BETWEEN %s AND %s
                   ORDER BY disparado_en DESC LIMIT 1""",
                (workflow, creada - _dt.timedelta(minutes=10), creada + _dt.timedelta(minutes=2)),
            )
            fila = cur.fetchone()
            return fila[0] if fila else None
    except Exception:
        return None


def metricas_registro(conn, workflow, run_id) -> tuple[dict, list]:
    """(métricas de esta corrida sumadas, métricas de las 15 corridas anteriores)."""
    if conn is None:
        return {}, []
    actuales: dict = {}
    historial = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT metricas FROM registro_corridas WHERE proceso = %s AND run_id = %s",
                        (workflow, str(run_id)))
            for (m,) in cur.fetchall():
                for k, v in (m or {}).items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in NO_COMPARAR:
                        actuales[k] = actuales.get(k, 0) + v
                    else:
                        actuales[k] = v
            cur.execute(
                """SELECT run_id, metricas FROM registro_corridas
                   WHERE proceso = %s AND COALESCE(run_id, '') <> %s
                   ORDER BY creado_en DESC LIMIT 60""",
                (workflow, str(run_id)),
            )
            por_run: dict = {}
            for rid, m in cur.fetchall():
                acc = por_run.setdefault(rid or f"sin-{len(por_run)}", {})
                for k, v in (m or {}).items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        acc[k] = acc.get(k, 0) + v
            historial = list(por_run.values())[:15]
    except Exception as ex:
        print(f"::warning::No se pudo leer registro_corridas ({type(ex).__name__})")
    return actuales, historial


SQL_BASE = """
SELECT
  COUNT(*) FILTER (WHERE creado_en >= %(i)s AND creado_en < %(f)s)                                   AS marcas_creadas,
  COUNT(*) FILTER (WHERE creado_en >= %(i)s AND creado_en < %(f)s AND es_lead)                       AS leads_nuevos,
  COUNT(*) FILTER (WHERE creado_en >= %(i)s AND creado_en < %(f)s AND es_lead
                   AND COALESCE(email, '') <> '')                                                    AS leads_con_email,
  COUNT(*) FILTER (WHERE oposicion_detectada_en >= %(i)s AND oposicion_detectada_en < %(f)s)         AS oposiciones_nuevas,
  COUNT(*) FILTER (WHERE oposicion_detectada_en >= %(i)s AND oposicion_detectada_en < %(f)s AND es_lead) AS oposiciones_en_leads,
  COUNT(*) FILTER (WHERE vista_detectada_en >= %(i)s AND vista_detectada_en < %(f)s)                 AS vistas_nuevas,
  COUNT(*) FILTER (WHERE vista_detectada_en >= %(i)s AND vista_detectada_en < %(f)s AND es_lead)     AS vistas_en_leads,
  COUNT(*) FILTER (WHERE revisado_oposicion_en >= %(i)s AND revisado_oposicion_en < %(f)s)           AS revisadas_oposicion,
  COUNT(*) FILTER (WHERE vista_revisada_en >= %(i)s AND vista_revisada_en < %(f)s)                   AS revisadas_vista
FROM marcas
WHERE creado_en >= %(i)s OR oposicion_detectada_en >= %(i)s OR vista_detectada_en >= %(i)s
   OR revisado_oposicion_en >= %(i)s OR vista_revisada_en >= %(i)s
"""


def efectos_en_base(conn, inicio, fin) -> dict:
    if conn is None or not inicio or not fin:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_BASE, {"i": inicio, "f": fin + _dt.timedelta(seconds=30)})
            fila = cur.fetchone()
            nombres = [c[0] for c in cur.description]
            return {k: int(v or 0) for k, v in zip(nombres, fila)}
    except Exception as ex:
        print(f"::warning::No se pudieron contar los cambios en la base ({type(ex).__name__})")
        return {}


def historial_base(conn, workflow, run_id) -> list:
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT base FROM reportes_corridas WHERE workflow = %s AND run_id <> %s
                           AND conclusion = 'success' ORDER BY fin DESC LIMIT 15""", (workflow, run_id))
            return [b for (b,) in cur.fetchall() if isinstance(b, dict)]
    except Exception:
        return []


def ya_enviado(conn, run_id, intento) -> bool:
    if conn is None:
        return False
    with conn.cursor() as cur:
        cur.execute("SELECT enviado FROM reportes_corridas WHERE run_id = %s AND intento = %s", (run_id, intento))
        fila = cur.fetchone()
        return bool(fila and fila[0])


def guardar(conn, d: dict, enviado: bool):
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO reportes_corridas (run_id, intento, workflow, nombre, conclusion, origen, creada,
                       inicio, fin, duracion_s, espera_s, metricas, base, alertas, mensaje, enviado)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (run_id, intento) DO UPDATE SET conclusion = EXCLUDED.conclusion,
                       metricas = EXCLUDED.metricas, base = EXCLUDED.base, alertas = EXCLUDED.alertas,
                       mensaje = EXCLUDED.mensaje, fin = EXCLUDED.fin, duracion_s = EXCLUDED.duracion_s,
                       enviado = reportes_corridas.enviado OR EXCLUDED.enviado""",
                (d["run_id"], d["intento"], d["workflow"], d["nombre"], d["conclusion"], d["origen"], d["creada"],
                 d["inicio"], d["fin"], int(d["duracion_s"]) if d["duracion_s"] is not None else None,
                 int(d["espera_s"]) if d["espera_s"] is not None else None,
                 json.dumps(d["metricas"]), json.dumps(d["base"]), json.dumps(d["alertas"]), d["mensaje"], enviado),
            )
    except Exception as ex:
        print(f"::warning::No se pudo guardar el reporte ({type(ex).__name__})")


# ── Telegram ───────────────────────────────────────────────────────────────────────────────────────
# El envío y los temas del grupo están en telegram_avisos.py (lo usan también otros avisos).

def enviar(conn, texto) -> bool:
    return telegram_avisos.enviar(texto, tema="corridas", conn=conn)


# ── Principal ──────────────────────────────────────────────────────────────────────────────────────

def reportar(run: dict, forzar=False) -> int:
    workflow = Path(run.get("path") or "").name
    if workflow == PROPIO:
        return 0
    conclusion = run.get("conclusion")
    if conclusion == "skipped":
        print("Corrida salteada: no se reporta.")
        return 0
    if workflow in SOLO_SI_FALLA and conclusion == "success" and not forzar:
        print(f"{workflow} terminó bien: solo se reporta si falla.")
        return 0

    run_id, intento = int(run["id"]), int(run.get("run_attempt") or 1)
    creada, inicio, fin = parse_ts(run.get("created_at")), parse_ts(run.get("run_started_at")), parse_ts(run.get("updated_at"))
    duracion = (fin - inicio).total_seconds() if inicio and fin else None
    espera = (inicio - creada).total_seconds() if inicio and creada and intento == 1 else None

    try:
        conn = conectar()
    except Exception as ex:
        print(f"::warning::No se pudo conectar a la base ({type(ex).__name__}): el reporte sale sin números.")
        conn = None
    if not forzar and ya_enviado(conn, run_id, intento):
        print("Esta corrida ya se reportó.")
        return 0

    programada = hora_programada(conn, workflow, creada)
    demora = (inicio - programada).total_seconds() if programada and inicio else None
    origen = origen_de(run, programada)

    normal_txt, alertas = analizar_tiempos(duracion, espera, demora,
                                           duraciones_previas(run.get("workflow_id"), run_id))

    etiquetas = etiquetas_registro().get(workflow, {})
    m_actual, m_hist = metricas_registro(conn, workflow, run_id)
    lineas_reg, alertas_reg = comparar_metricas(m_actual, m_hist, etiquetas)

    base = efectos_en_base(conn, inicio, fin)
    base_no_cero = {k: v for k, v in base.items() if v}
    lineas_base, alertas_base = comparar_metricas(base_no_cero, historial_base(conn, workflow, run_id))
    # En la base solo se avisa por caídas/subas si el proceso no anota sus propios números.
    if lineas_reg:
        alertas_base = []

    falla = error = None
    if conclusion != "success":
        falla, error = detalle_falla(run)
        if conclusion == "cancelled" and not falla:
            alertas.append("GitHub la canceló: puede que otra corrida del mismo carril la haya reemplazado")
    if conclusion == "failure" and not m_actual and not base_no_cero:
        alertas.append("No llegó a anotar resultados: revisar si hay que relanzarla")

    d = {
        "run_id": run_id, "intento": intento, "workflow": workflow,
        "nombre": run.get("name") or workflow, "conclusion": conclusion, "origen": origen,
        "actor": (run.get("triggering_actor") or run.get("actor") or {}).get("login"),
        "creada": creada, "inicio": inicio, "fin": fin, "duracion_s": duracion, "espera_s": espera,
        "normal_txt": normal_txt, "falla": falla, "error": error,
        "lineas_registro": lineas_reg, "lineas_base": lineas_base,
        "sin_base": conn is not None and not base_no_cero,
        "solapadas": solapadas(run, inicio, fin) if base_no_cero else [],
        "alertas": alertas + alertas_reg + alertas_base,
        "metricas": m_actual, "base": base, "url": run.get("html_url") or "",
    }
    d["mensaje"] = armar_mensaje(d)
    print(f"Reporte de {workflow} (corrida {run_id}, intento {intento}): {conclusion}, "
          f"{duracion_txt(duracion)}, {len(d['alertas'])} alerta(s).")
    enviado = enviar(conn, d["mensaje"])
    aviso = aviso_sistema(d)
    if aviso:
        telegram_avisos.enviar(aviso, tema="sistema", conn=conn)
    guardar(conn, d, enviado)
    if conn is not None:
        conn.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-id", help="reportar esta corrida (en vez de la del evento)")
    ap.add_argument("--prueba", action="store_true", help="mandar un mensaje de prueba al chat")
    args = ap.parse_args()

    if args.prueba:
        conn = None
        try:
            conn = conectar()
        except Exception:
            pass
        ok = telegram_avisos.preparar_temas(conn)
        return 0 if ok else 1

    if args.run_id:
        run = _gh(f"/repos/{REPO}/actions/runs/{args.run_id}").json()
        return reportar(run, forzar=True)

    evento = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    run = evento.get("workflow_run")
    if not run:
        print("El evento no trae workflow_run: nada para reportar.")
        return 0
    return reportar(run)


if __name__ == "__main__":
    sys.exit(main())
