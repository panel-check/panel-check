"""
Panel propio de leads — API + frontend estático.

Backend chiquito (FastAPI) que se conecta a la misma Postgres del pipeline
(Railway) para mostrar la tabla `marcas` con filtros/orden pensados para
prospección, y para marcar leads como "contactado" (el pipeline nunca toca
esa columna, es exclusiva del panel).

Login: HTTP Basic simple, con uno o varios usuarios. No es para datos súper
sensibles, es para no dejar el link completamente abierto.

Variables de entorno requeridas:
    DATABASE_URL    - la misma que usa cargar_db.py
    PANEL_USER      - usuario para el login (modo de un solo usuario)
    PANEL_PASSWORD  - clave para el login (modo de un solo usuario)
    PANEL_USERS     - opcional, para varios usuarios a la vez: pares
                      "usuario:clave" separados por coma, ej.
                      "pamela:pame20@26,tomasbott:Coderhouse21@"
                      (se suma a PANEL_USER/PANEL_PASSWORD si también están)
    GITHUB_TOKEN    - opcional, para la sección "Automatizaciones" (/crons):
                      un Personal Access Token (fine-grained) con permiso
                      "Actions: Read-only" sobre este repo. Sin esto, esa
                      sección muestra el horario pero no el estado de la
                      última corrida.

Correr local:
    DATABASE_URL=... PANEL_USER=admin PANEL_PASSWORD=... uvicorn app:app --reload
"""

import os
import re
import secrets
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras
import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import inpi_lead

DATABASE_URL = os.environ.get("DATABASE_URL")
PANEL_USER = os.environ.get("PANEL_USER")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD")
PANEL_USERS_RAW = os.environ.get("PANEL_USERS", "")

if not DATABASE_URL:
    raise RuntimeError("Falta la variable de entorno DATABASE_URL")

# Usuarios habilitados para entrar al panel: {usuario: clave}. Se puede definir
# un solo usuario (PANEL_USER/PANEL_PASSWORD) y/o varios a la vez (PANEL_USERS,
# pares "usuario:clave" separados por coma) — ambos se combinan.
USUARIOS_PANEL: dict[str, str] = {}
if PANEL_USER and PANEL_PASSWORD:
    USUARIOS_PANEL[PANEL_USER] = PANEL_PASSWORD
for par in PANEL_USERS_RAW.split(","):
    par = par.strip()
    if not par:
        continue
    usuario, _, clave = par.partition(":")
    if usuario and clave:
        USUARIOS_PANEL[usuario] = clave

if not USUARIOS_PANEL:
    raise RuntimeError(
        "No hay ningún usuario configurado: definí PANEL_USER + PANEL_PASSWORD "
        "y/o PANEL_USERS"
    )

app = FastAPI(title="Panel de leads — Kom Marcas Inpi")
security = HTTPBasic()


@app.on_event("startup")
def migrar_columnas_panel():
    """Asegura que existan las columnas que usa el panel (contactado/contactado_en).

    El pipeline las agrega vía schema.sql, pero sólo la próxima vez que corra.
    El panel no puede esperar a eso, así que se asegura de tenerlas ni bien
    arranca (idempotente: no rompe nada si ya existen).

    Bug real (2026-09-29): un ALTER TABLE ADD COLUMN IF NOT EXISTS pide lock
    ACCESS EXCLUSIVE sobre `marcas` aunque sea un no-op — y ese lock se pone
    en cola detrás de cualquier transacción que ya esté escribiendo en la
    tabla (ej. reintentar_sin_verificar.py corriendo con un backlog grande).
    Si el deploy nuevo arranca justo mientras esa corrida está activa, el
    ALTER se queda esperando el lock y el startup entero de FastAPI se
    cuelga indefinidamente ("Waiting for application startup" sin pasar de
    ahí) — el proceso ni siquiera llega a abrir el puerto, así que Railway
    devuelve "connection refused" para TODO, no solo para esta consulta.
    Fix: lock_timeout corto + no fatal — si no consigue el lock rápido, la
    migración se salta esta vez (las columnas ya existen en producción de
    sobra) en vez de trabar el arranque del panel entero."""
    try:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SET lock_timeout = '3s'")
                _correr_alters_panel(cur)
            conn.commit()
    except Exception as e:
        print(f"[startup] migrar_columnas_panel salteada (no bloqueante): {e}")

    # Tablas de comentarios internos: van en una transacción aparte porque no
    # tocan `marcas` (no compiten por su lock) — si el bloque de arriba se
    # saltea por lock_timeout, esto igual tiene que quedar creado.
    try:
        with conexion() as conn:
            with conn.cursor() as cur:
                _crear_tablas_comentarios(cur)
            conn.commit()
    except Exception as e:
        print(f"[startup] tablas de comentarios salteadas (no bloqueante): {e}")


def _crear_tablas_comentarios(cur):
    """Comentarios internos del equipo (ver sección /comentarios).

    - acta: opcional, vincula el comentario a una marca. No es FK a `marcas`
      a propósito: se puede comentar un acta que todavía no está cargada
      (ej. la marca que invoca un oponente).
    - destinatario: NULL = para todo el equipo; si no, el usuario del panel
      (mismo nombre que en PANEL_USERS) al que va dirigido.
    - resuelto: para "cerrar" un pendiente sin borrarlo.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comentarios (
            id            SERIAL PRIMARY KEY,
            autor         TEXT NOT NULL,
            destinatario  TEXT,
            acta          TEXT,
            texto         TEXT NOT NULL,
            creado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
            resuelto      BOOLEAN NOT NULL DEFAULT false,
            resuelto_por  TEXT,
            resuelto_en   TIMESTAMPTZ
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_comentarios_acta ON comentarios(acta)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_comentarios_creado ON comentarios(creado_en DESC)")
    # Hasta cuándo vio cada usuario la sección de comentarios -> contador de
    # "nuevos" en el encabezado del panel.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comentarios_visto (
            usuario      TEXT PRIMARY KEY,
            visto_hasta  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def _correr_alters_panel(cur):
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS contactado BOOLEAN DEFAULT false"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS contactado_en TIMESTAMPTZ"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_marcas_contactado ON marcas(contactado)"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS motivo_sin_email TEXT"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS fecha_publicacion DATE"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS tuvo_oposicion BOOLEAN"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS detalle_oposicion TEXT"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS revisado_oposicion_en TIMESTAMPTZ"
    )
    # Estado del trámite (Concedida/Denegada/etc.) y fechas de la sección
    # RESOLUCIÓN del expediente — ver revisar_estado.py.
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS estado_tramite TEXT"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS fecha_concesion DATE"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS numero_disposicion TEXT"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS fecha_vencimiento_marca DATE"
    )
    # Detalle real de la oposición de tercero, parseado del PDF "Formulario"
    # de la SOLICITUD DE OPOSICION (no del "Recibo de Ingreso", que es solo
    # una constancia administrativa) — ver descargar_formulario_oposicion en
    # scripts/validar_leads.py. Quedan NULL para vistas de oficio de INPI
    # (no las presenta un tercero, no tienen este formulario) y para
    # oposiciones donde no se pudo bajar/parsear el PDF.
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oponente_nombre TEXT"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oponente_tipo_doc TEXT"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oponente_numero_doc TEXT"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oponente_cuit TEXT"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS fundamento_oposicion TEXT"
    )
    # Marca propia del oponente citada en el fundamento (ver
    # parsear_marca_oponente en validar_leads.py): si el fundamento da un
    # ACTA concreta, actas_marca_oponente permite linkear directo (mismo
    # abrirActa() que ya usa el panel); si solo da un número de registro,
    # queda en marca_oponente_denominacion/numero_registro para buscarla
    # por nombre desde el popup de la oposición.
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS actas_marca_oponente TEXT"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS marca_oponente_denominacion TEXT"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS marca_oponente_numero_registro TEXT"
    )
    # Si después de detectar la oposición aparece en Grilla Digital una
    # presentación de "Acompaña Poder" o "Ratifica" (alguien se sumó como
    # apoderado/gestor para responder), lo marcamos acá -- ver
    # revisar_oposiciones.py. Es una señal fuerte de que el titular ya está
    # trabajando con alguien para la oposición, así que deja de ser
    # prioritario como lead frío.
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS representacion_posterior_oposicion BOOLEAN"
    )
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS detalle_representacion_posterior TEXT"
    )
    # Escaneo directo de números de acta secuenciales (adelanta el contacto
    # semanas antes del boletín) -- ver scripts/escanear_actas_nuevas.py.
    cur.execute(
        "ALTER TABLE marcas ADD COLUMN IF NOT EXISTS fuente TEXT DEFAULT 'boletin'"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_marcas_fuente ON marcas(fuente)"
    )
    # Limpieza del bug del titular en el escaneo directo (30/09/2026): la
    # regex vieja del Formulario guardaba "NOMBRE DOMICILIO LEGAL: ... 3 de 3".
    # Se corta en " DOMICILIO LEGAL" y, si lo que queda es solo un CUIT/número,
    # se deja como "a confirmar" (corregir_titular_escaneo.py lo completa
    # desde INPI). Idempotente: después de la primera vez no matchea nada.
    cur.execute(
        r"""
        UPDATE marcas
        SET titular = CASE
                WHEN btrim(regexp_replace(titular, '\s*DOMICILIO LEGAL.*$', '')) ~ '^[0-9.\- ]*$'
                    THEN '(titular a confirmar manualmente)'
                ELSE btrim(regexp_replace(titular, '\s*DOMICILIO LEGAL.*$', ''))
            END,
            actualizado_en = now()
        WHERE fuente = 'escaneo_directo' AND titular ~ 'DOMICILIO LEGAL'
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS escaneo_actas (
            id                      SMALLINT PRIMARY KEY DEFAULT 1,
            ultima_acta_confirmada  BIGINT NOT NULL,
            actualizado_en          TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT escaneo_actas_una_fila CHECK (id = 1)
        )
        """
    )

COLUMNAS_ORDENABLES = {
    "lead_score", "acta", "boletin", "clase", "titular", "fecha_presentacion",
    "fecha_publicacion", "fecha_concesion", "creado_en", "actualizado_en",
}

COLUMNAS_MARCA = """
    acta, boletin, clase, tipo, denominacion, denominacion_inpi,
    fecha_presentacion, fecha_publicacion, titular, pais, cuit, matricula_agente,
    caracter, es_lead, email, email_apoderado, lead_score, link,
    contactado, contactado_en, motivo_sin_email,
    tuvo_oposicion, detalle_oposicion, revisado_oposicion_en,
    oponente_nombre, oponente_tipo_doc, oponente_numero_doc, oponente_cuit,
    fundamento_oposicion, actas_marca_oponente, marca_oponente_denominacion,
    marca_oponente_numero_registro,
    representacion_posterior_oposicion, detalle_representacion_posterior,
    estado_tramite, fecha_concesion, numero_disposicion, fecha_vencimiento_marca,
    fuente
"""

RE_CUIT_VALIDO = re.compile(r"^\d{10,11}$")

# --- Sección "Automatizaciones" (/crons) -----------------------------------
# GitHub reporta esta org/repo con mayúscula/guiones distintos según la API
# que se use; funciona igual para leer workflows.
GITHUB_REPO = os.environ.get("GITHUB_REPO", "panel-check/panel-check")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# Los workflows que corren solos (on: schedule). El de backfill es manual
# (workflow_dispatch únicamente), no tiene horario fijo, así que no entra
# acá — si en el futuro se agregan más crons, van en esta lista.
CRONS_DEFINIDOS = [
    {
        "nombre": "Pipeline de boletines",
        "descripcion": "Busca el último boletín \"MARCAS NUEVAS\" publicado y corre "
                        "todo el proceso: extraer el PDF, parsear, completar mixtas/figurativas, "
                        "validar leads contra INPI y cargar todo a la base.",
        "workflow_file": "pipeline.yml",
        "cron": "0 9 * * 1,3,5,6",
    },
    {
        "nombre": "Revisión de oposiciones/vistas",
        "descripcion": "Para los leads reales ya publicados hace 33 días o más, "
                        "vuelve a mirar la Grilla Digital del expediente buscando si "
                        "apareció una oposición de tercero o una vista de INPI.",
        "workflow_file": "revisar_oposiciones.yml",
        "cron": "0 10 * * *",
        "pendientes_sql": "SELECT COUNT(*) FROM marcas WHERE es_lead = true "
                           "AND fecha_publicacion IS NOT NULL AND revisado_oposicion_en IS NULL",
        "pendientes_etiqueta": "leads publicados esperando el plazo de 33 días",
    },
    {
        "nombre": "Revisión de estado del trámite",
        "descripcion": "Para las marcas que todavía no tienen una resolución firme "
                        "(ni Concedida ni Denegada), vuelve a mirar el expediente y guarda "
                        "el estado, la fecha de concesión y el vencimiento apenas INPI resuelve.",
        "workflow_file": "revisar_estado.yml",
        "cron": "0 11 * * 1",
        "pendientes_sql": "SELECT COUNT(*) FROM marcas WHERE estado_tramite IS NULL "
                           "OR estado_tramite NOT IN ('Concedida', 'Denegada')",
        "pendientes_etiqueta": "marcas sin resolución firme todavía",
    },
    {
        "nombre": "Reintento de marcas sin verificar",
        "descripcion": "Para las marcas donde no se pudo confirmar si tienen agente/apoderado "
                        "(casi siempre por un bloqueo puntual del WAF de INPI durante la corrida "
                        "del boletín), vuelve a consultar el expediente para resolverlas sin "
                        "tener que usar \"Reintentar\" a mano una por una.",
        "workflow_file": "reintentar_sin_verificar.yml",
        "cron": "0,30 * * * *",
        "pendientes_sql": "SELECT COUNT(*) FROM marcas WHERE es_lead IS NULL",
        "pendientes_etiqueta": "marcas sin verificar todavía",
    },
    {
        "nombre": "Reintento de emails faltantes",
        "descripcion": "Para los leads ya confirmados que quedaron sin email "
                        "(casi siempre por un bloqueo puntual del WAF de INPI al abrir la "
                        "Grilla Digital o descargar el Formulario), vuelve a consultar el "
                        "expediente para conseguirlo sin tener que usar \"Reintentar\" a "
                        "mano una por una.",
        "workflow_file": "reintentar_sin_email.yml",
        "cron": "0 1,3,5,7,9,11,13,15,17,19,21,23 * * *",
        "pendientes_sql": "SELECT COUNT(*) FROM marcas WHERE es_lead = true "
                           "AND (email IS NULL OR email = '')",
        "pendientes_etiqueta": "leads sin email todavía",
    },
    {
        "nombre": "Escaneo de actas nuevas",
        "descripcion": "El acta se asigna al depositar la solicitud, semanas antes de "
                        "publicarse en el boletín. Prueba los números de acta siguientes al "
                        "último confirmado y, si encuentra un lead nuevo (sin agente/apoderado), "
                        "lo carga con los mismos datos que hoy vienen del boletín -- para poder "
                        "contactarlo el mismo día que se registra en vez de esperar semanas.",
        "workflow_file": "escanear_actas.yml",
        "cron": "0 7,13,19 * * *",
        "pendientes_sql": "SELECT COUNT(*) FROM marcas WHERE fuente = 'escaneo_directo' "
                           "AND boletin IS NULL",
        "pendientes_etiqueta": "leads detectados antes del boletín, esperando que los alcance",
    },
    {
        "nombre": "Verificación del despliegue",
        "descripcion": "Compara qué commit tiene desplegado el panel contra el último "
                        "commit de main. Si Railway se queda pegado (deploy atascado en cola, "
                        "como pasó por un incidente de la plataforma), esta corrida falla y "
                        "esta misma tarjeta se pone en rojo — no hace falta que te enteres por "
                        "una captura del panel viejo.",
        "workflow_file": "verificar_despliegue.yml",
        "cron": "0,15,30,45 * * * *",
    },
]


def _proxima_ejecucion(expresion_cron: str) -> str:
    """Próxima vez que corre ese cron (UTC, ISO 8601) a partir de ahora.

    Implementado a mano (sin librería) porque solo necesitamos soportar los
    crons fijos de este repo: "minuto hora * * dias_semana", con listas
    separadas por coma o "*". Búsqueda por fuerza bruta, minuto a minuto,
    hasta 8 días adelante — de sobra para cualquier cron real de este repo.
    """
    import datetime as _dt

    minuto_s, hora_s, dia_mes_s, mes_s, dia_semana_s = expresion_cron.split()

    def _set_o_none(valor: str):
        return None if valor == "*" else {int(x) for x in valor.split(",")}

    minutos = _set_o_none(minuto_s)
    horas = _set_o_none(hora_s)
    dias_semana = _set_o_none(dia_semana_s)  # cron: domingo=0 ... sábado=6

    candidato = _dt.datetime.now(_dt.timezone.utc).replace(second=0, microsecond=0) \
        + _dt.timedelta(minutes=1)
    limite = candidato + _dt.timedelta(days=8)
    while candidato < limite:
        dia_semana_cron = (candidato.weekday() + 1) % 7  # lunes=0 -> domingo=0
        if (minutos is None or candidato.minute in minutos) \
                and (horas is None or candidato.hour in horas) \
                and (dias_semana is None or dia_semana_cron in dias_semana):
            return candidato.isoformat()
        candidato += _dt.timedelta(minutes=1)
    return None  # no debería pasar con los crons de este repo


def _ultima_corrida_workflow(workflow_file: str) -> dict:
    """Consulta la API de GitHub por la corrida más reciente de un workflow.
    Devuelve algo usable por el frontend aunque falte el token o falle la
    consulta — nunca tira una excepción hacia afuera."""
    if not GITHUB_TOKEN:
        return {"estado": None, "aviso": "Falta configurar GITHUB_TOKEN en el panel"}

    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/runs",
            headers=headers, params={"per_page": 1}, timeout=15,
        )
        r.raise_for_status()
        runs = r.json().get("workflow_runs", [])
        if not runs:
            return {"estado": None, "aviso": "Todavía no corrió nunca"}
        run = runs[0]
    except requests.RequestException as e:
        return {"estado": None, "aviso": f"No se pudo consultar GitHub: {e}"}

    resultado = {
        "estado": run.get("status"),
        "conclusion": run.get("conclusion"),
        "fecha": run.get("run_started_at") or run.get("created_at"),
        "url": run.get("html_url"),
    }

    if run.get("conclusion") == "failure":
        # Buscamos el motivo puntual: las líneas "::error::" que haya
        # impreso el script quedan como "annotation" del check run,
        # legibles por la API normal de GitHub (no hace falta bajar el
        # log completo, que se sirve desde blob storage).
        try:
            head_sha = run["head_sha"]
            r_checks = requests.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/commits/{head_sha}/check-runs",
                headers=headers, timeout=15,
            )
            r_checks.raise_for_status()
            mensajes = []
            for cr in r_checks.json().get("check_runs", []):
                if cr.get("conclusion") != "failure":
                    continue
                r_ann = requests.get(
                    f"https://api.github.com/repos/{GITHUB_REPO}/check-runs/{cr['id']}/annotations",
                    headers=headers, timeout=15,
                )
                r_ann.raise_for_status()
                for a in r_ann.json():
                    if a.get("annotation_level") == "failure" and a.get("message"):
                        mensajes.append(a["message"])
            if mensajes:
                resultado["error"] = " / ".join(dict.fromkeys(mensajes))  # sin duplicados
        except requests.RequestException:
            pass  # nos quedamos sin el detalle, pero ya tenemos "failure"

    return resultado


def verificar_login(credenciales: HTTPBasicCredentials = Depends(security)) -> str:
    clave_esperada = USUARIOS_PANEL.get(credenciales.username)
    # comparación en tiempo constante incluso cuando el usuario no existe,
    # para no filtrar por timing qué usuarios son válidos
    clave_ok = secrets.compare_digest(credenciales.password, clave_esperada or "")
    if clave_esperada is None or not clave_ok:
        raise HTTPException(
            status_code=401,
            detail="Usuario o clave incorrectos",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credenciales.username


@contextmanager
def conexion():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


# ── Cruce "misma marca en varias clases" ──────────────────────────────────
# Cada solicitud es un acta con UNA clase, así que una marca pedida en 2
# clases son 2 filas. Para cruzarlas se compara el nombre normalizado:
# denominación de INPI (la recuperada para mixtas/figurativas) o, si no hay,
# la del boletín; en mayúsculas, sin acentos y sin espacios ni signos
# ("Café Luna" = "CAFE LUNA" = "CAFE-LUNA", "Ñandú" = "NANDU"). Las mixtas/figurativas sin
# nombre recuperado no se pueden cruzar y quedan afuera.
# El cruce mira TODA la base (todos los boletines y el pre-boletín), no solo
# lo que dejan pasar los otros filtros: los otros filtros deciden qué filas
# se muestran, el cruce decide si la marca está en varias clases.
NOMBRE_NORMALIZADO_SQL = (
    "regexp_replace(translate(upper(COALESCE(NULLIF(trim(denominacion_inpi), ''), denominacion)),"
    " 'ÁÉÍÓÚÜÀÈÌÒÙÂÊÎÔÛÑ', 'AEIOUUAEIOUAEIOUN'), '[^A-Z0-9]', '', 'g')"
)
TITULAR_NORMALIZADO_SQL = "upper(regexp_replace(trim(titular), '\\s+', ' ', 'g'))"


def _cruce_clases_sql(minimo, clase_a, clase_b, titular):
    """Devuelve (join_sql, valores_join, condicion_extra) o None si el filtro
    no está activo. condicion_extra es (sql, valores) o None."""
    par = clase_a is not None and clase_b is not None and clase_a != clase_b
    if not par and minimo is None:
        return None

    mismo_titular = titular == "mismo"
    agrupar_por = "nom, tit" if mismo_titular else "nom"
    having = []
    valores = []
    if par:
        having.append("bool_or(clase = %s) AND bool_or(clase = %s)")
        valores.extend([clase_a, clase_b])
    else:
        having.append("COUNT(DISTINCT clase) >= %s")
        valores.append(minimo)
    if titular == "distinto":
        having.append("COUNT(DISTINCT tit) >= 2")

    subconsulta = f"""
        SELECT {agrupar_por}, array_agg(DISTINCT clase ORDER BY clase) AS clases
        FROM (
            SELECT {NOMBRE_NORMALIZADO_SQL} AS nom, {TITULAR_NORMALIZADO_SQL} AS tit, clase
            FROM marcas
            WHERE clase IS NOT NULL
        ) base
        WHERE nom IS NOT NULL AND nom <> ''
        GROUP BY {agrupar_por}
        HAVING {' AND '.join(having)}
    """
    on = f"cruce.nom = {NOMBRE_NORMALIZADO_SQL}"
    if mismo_titular:
        on += f" AND cruce.tit = {TITULAR_NORMALIZADO_SQL}"
    join_sql = f"JOIN ({subconsulta}) cruce ON {on}"

    # Con un par de clases puntuales se muestran solo las filas de esas dos
    # clases (no las otras clases en las que también esté la marca).
    condicion_extra = ("clase IN (%s, %s)", [clase_a, clase_b]) if par else None
    return join_sql, valores, condicion_extra


@app.get("/api/marcas")
def listar_marcas(
    _: str = Depends(verificar_login),
    boletin: Optional[str] = None,
    clase: Optional[int] = None,
    es_lead: Optional[bool] = None,
    contactado: Optional[bool] = None,
    tuvo_oposicion: Optional[bool] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    tiene_email: Optional[bool] = None,
    tiene_titular: Optional[bool] = None,
    estado_marca: Optional[str] = None,  # "pendiente" | "Concedida" | "Denegada"
    # Filtro avanzado "Misma marca en varias clases" (ver _cruce_clases_sql):
    # o bien un mínimo de clases (multiclase_min), o bien un par de clases
    # puntuales (multiclase_a + multiclase_b).
    multiclase_min: Optional[int] = Query(None, ge=2, le=45),
    multiclase_a: Optional[int] = Query(None, ge=1, le=45),
    multiclase_b: Optional[int] = Query(None, ge=1, le=45),
    multiclase_titular: Optional[str] = None,  # "" (cualquiera) | "mismo" | "distinto"
    q: Optional[str] = None,
    sort: str = "lead_score",
    order: str = "desc",
    limit: int = Query(200, le=1000),
    offset: int = 0,
):
    if sort not in COLUMNAS_ORDENABLES:
        sort = "lead_score"
    order_sql = "DESC" if order.lower() != "asc" else "ASC"

    condiciones = []
    valores = []
    if boletin == "pre":
        # Marcas detectadas por el escaneo directo de actas que todavía no
        # salieron en ningún boletín (ver scripts/escanear_actas_nuevas.py).
        condiciones.append("boletin IS NULL")
    elif boletin:
        condiciones.append("boletin = %s")
        valores.append(boletin)
    if clase is not None:
        condiciones.append("clase = %s")
        valores.append(clase)
    if es_lead is not None:
        condiciones.append("es_lead = %s")
        valores.append(es_lead)
    if contactado is not None:
        condiciones.append("contactado = %s")
        valores.append(contactado)
    if tuvo_oposicion is not None:
        condiciones.append("tuvo_oposicion = %s")
        valores.append(tuvo_oposicion)
    if fecha_desde or fecha_hasta:
        # Mismo criterio que muestra el panel como "Fecha Boletín"
        # (fechaPublicacionOFallback en comun.js): fecha_publicacion cuando
        # ya se verificó, si no fecha_presentacion — filtrar solo por
        # fecha_publicacion dejaría afuera filas que el panel sí muestra
        # como dentro del rango, por el fallback.
        if fecha_desde:
            condiciones.append("COALESCE(fecha_publicacion, fecha_presentacion) >= %s")
            valores.append(fecha_desde)
        if fecha_hasta:
            condiciones.append("COALESCE(fecha_publicacion, fecha_presentacion) <= %s")
            valores.append(fecha_hasta)
    if tiene_email is not None:
        # Filtro "Avanzado": marcas a las que todavía no se les encontró
        # ningún email (ni del titular ni del apoderado) — útil para ver
        # a quién le falta ese dato antes de poder contactarlo.
        condicion_email = "(email IS NOT NULL AND email <> '') OR (email_apoderado IS NOT NULL AND email_apoderado <> '')"
        condiciones.append(condicion_email if tiene_email else f"NOT ({condicion_email})")
    if tiene_titular is not None:
        condicion_titular = "titular IS NOT NULL AND titular <> ''"
        condiciones.append(condicion_titular if tiene_titular else f"NOT ({condicion_titular})")
    if estado_marca:
        # Mismo criterio que ESTADOS_FINALES en scripts/revisar_estado.py:
        # "pendiente" = todavía sin una resolución firme.
        if estado_marca == "pendiente":
            condiciones.append("(estado_tramite IS NULL OR estado_tramite NOT IN ('Concedida', 'Denegada'))")
        else:
            condiciones.append("estado_tramite = %s")
            valores.append(estado_marca)
    if q:
        condiciones.append(
            """(
                titular ILIKE %s OR denominacion ILIKE %s OR denominacion_inpi ILIKE %s
                OR email ILIKE %s OR email_apoderado ILIKE %s
                OR cuit ILIKE %s OR acta ILIKE %s
            )"""
        )
        patron = f"%{q}%"
        valores.extend([patron, patron, patron, patron, patron, patron, patron])

    join_sql = ""
    columnas_extra = ""
    orden_previo = ""
    valores_join = []
    cruce = _cruce_clases_sql(multiclase_min, multiclase_a, multiclase_b, multiclase_titular)
    if cruce:
        join_sql, valores_join, condicion_extra = cruce
        if condicion_extra:
            condiciones.append(condicion_extra[0])
            valores.extend(condicion_extra[1])
        columnas_extra = ", cruce.clases AS clases_misma_marca"
        # Las filas de una misma marca van juntas (y dentro de cada marca,
        # el orden que eligió el usuario).
        orden_previo = "cruce.nom, "

    where_sql = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    sql = f"""
        SELECT {COLUMNAS_MARCA}{columnas_extra}
        FROM marcas
        {join_sql}
        {where_sql}
        ORDER BY {orden_previo}{sort} {order_sql} NULLS LAST, acta DESC
        LIMIT %s OFFSET %s
    """
    valores_paginado = valores_join + valores + [limit, offset]

    sql_total = f"SELECT count(*) FROM marcas {join_sql} {where_sql}"
    valores = valores_join + valores

    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, valores_paginado)
            filas = cur.fetchall()
            cur.execute(sql_total, valores)
            total = cur.fetchone()["count"]

    return {"total": total, "rows": filas}


@app.get("/api/boletines")
def listar_boletines(_: str = Depends(verificar_login)):
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT numero, fecha, total_marcas FROM boletines ORDER BY numero DESC"
            )
            return cur.fetchall()


@app.get("/api/pre-boletin/total")
def total_pre_boletin(_: str = Depends(verificar_login)):
    """Cantidad de marcas sin boletín todavía (escaneo directo), para la
    opción "Pre-boletín" del filtro Boletín."""
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM marcas WHERE boletin IS NULL")
            return {"total": cur.fetchone()[0]}


@app.get("/api/boletines/completo")
def listar_boletines_completo(_: str = Depends(verificar_login)):
    """Para la sección /boletines: cruza el listado completo de boletines
    "MARCAS NUEVAS" que tiene INPI publicados (consultado en vivo, ver
    inpi_lead.listar_boletines_marcas_nuevas) contra lo que ya tenemos
    importado en nuestra tabla `boletines`, para poder mostrar cuáles
    faltan y ofrecer importarlos desde acá (útil para el backlog
    histórico) — antes solo se podía ver esto mirando GitHub Actions a
    mano o adivinando por tanteo qué número forzar.

    Si INPI no responde (WAF, caído, etc.), devolvemos igual lo que ya
    tenemos importado en nuestra base, con un aviso, en vez de romper la
    página entera."""
    aviso = None
    try:
        de_inpi = inpi_lead.listar_boletines_marcas_nuevas()
    except requests.RequestException as e:
        de_inpi = []
        aviso = f"No se pudo consultar el listado de INPI ({e}) — se muestra solo lo ya importado."

    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT numero, fecha, total_marcas, estado, procesado_en FROM boletines")
            importados = {f["numero"]: f for f in cur.fetchall()}

    numeros = {b["numero"] for b in de_inpi} | set(importados.keys())
    filas = []
    for numero in numeros:
        de_inpi_fila = next((b for b in de_inpi if b["numero"] == numero), None)
        importado_fila = importados.get(numero)
        filas.append({
            "numero": numero,
            "fecha": (importado_fila or {}).get("fecha") or (de_inpi_fila or {}).get("fecha"),
            "importado": bool(importado_fila) and importado_fila["estado"] == "procesado",
            "estado": (importado_fila or {}).get("estado"),
            "total_marcas": (importado_fila or {}).get("total_marcas"),
            "procesado_en": (importado_fila or {}).get("procesado_en"),
        })
    filas.sort(key=lambda f: int(f["numero"]), reverse=True)
    return {"boletines": filas, "aviso": aviso}


@app.post("/api/boletines/{numero}/importar")
def importar_boletin(numero: str, _: str = Depends(verificar_login)):
    """Dispara pipeline.yml a mano forzando este número puntual (mismo
    mecanismo que /api/crons/correr, pero con el input `boletin` seteado)
    — pensado para importar boletines del backlog histórico desde
    /boletines, sin tener que ir a GitHub y tipear el número a mano."""
    if not numero.isdigit():
        raise HTTPException(status_code=400, detail="Número de boletín inválido")
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=400, detail="Falta configurar GITHUB_TOKEN en el panel")

    r = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/pipeline.yml/dispatches",
        headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
        json={"ref": "main", "inputs": {"boletin": numero}},
        timeout=15,
    )
    if r.status_code == 204:
        return {"ok": True}
    if r.status_code in (403, 404):
        raise HTTPException(
            status_code=502,
            detail="GitHub rechazó el disparo manual — el GITHUB_TOKEN del panel necesita permiso "
                   "\"Actions: Read and write\".",
        )
    raise HTTPException(status_code=502, detail=f"GitHub devolvió {r.status_code}: {r.text[:200]}")


@app.get("/api/clases")
def listar_clases(_: str = Depends(verificar_login)):
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT clase FROM marcas WHERE clase IS NOT NULL ORDER BY clase"
            )
            return [r[0] for r in cur.fetchall()]


@app.get("/api/titular/{clave}")
def ver_titular(clave: str, _: str = Depends(verificar_login)):
    """Todas las marcas de un mismo titular, en cualquier boletín.
    `clave` es el CUIT cuando ya se pudo leer del expediente (la clave
    real), o el nombre del titular normalizado como respaldo (mismo
    criterio que agruparPorTitular en el frontend)."""
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if RE_CUIT_VALIDO.match(clave):
                cur.execute(
                    f"SELECT {COLUMNAS_MARCA} FROM marcas WHERE cuit = %s "
                    f"ORDER BY fecha_presentacion DESC",
                    (clave,),
                )
            else:
                cur.execute(
                    f"""
                    SELECT {COLUMNAS_MARCA} FROM marcas
                    WHERE upper(regexp_replace(trim(titular), '\\s+', ' ', 'g')) = %s
                    ORDER BY fecha_presentacion DESC
                    """,
                    (clave.strip().upper(),),
                )
            filas = cur.fetchall()

    if not filas:
        raise HTTPException(status_code=404, detail="No se encontraron marcas para ese titular")

    return {
        "clave": clave,
        "titular": filas[0]["titular"],
        "cuit": filas[0]["cuit"],
        "rows": filas,
    }


@app.post("/api/marcas/{acta}/contactado")
def marcar_contactado(acta: str, valor: bool = True, _: str = Depends(verificar_login)):
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE marcas
                SET contactado = %s,
                    contactado_en = CASE WHEN %s THEN now() ELSE NULL END
                WHERE acta = %s
                """,
                (valor, valor, acta),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"No existe el acta {acta}")
        conn.commit()
    return {"acta": acta, "contactado": valor}


@app.post("/api/marcas/{acta}/reintentar-email")
def reintentar_email(acta: str, _: str = Depends(verificar_login)):
    """Vuelve a consultar el expediente en INPI para esta acta puntual y
    actualiza caracter/es_lead/email/email_apoderado/motivo_sin_email/lead_score.
    Tarda unos segundos (varias requests contra INPI en serie)."""
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT matricula_agente FROM marcas WHERE acta = %s", (acta,))
            fila = cur.fetchone()
            if not fila:
                raise HTTPException(status_code=404, detail=f"No existe el acta {acta}")

        info = inpi_lead.revisar_acta(acta)
        nuevo_score = inpi_lead.calcular_lead_score(
            fila["matricula_agente"], info["es_lead"], bool(info["email"])
        )

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE marcas
                SET caracter = %s, es_lead = %s, email = %s, email_apoderado = %s,
                    motivo_sin_email = %s, lead_score = %s,
                    cuit = COALESCE(%s, cuit),
                    fecha_publicacion = COALESCE(%s, fecha_publicacion),
                    estado_tramite = COALESCE(%s, estado_tramite),
                    fecha_concesion = COALESCE(%s, fecha_concesion),
                    numero_disposicion = COALESCE(%s, numero_disposicion),
                    fecha_vencimiento_marca = COALESCE(%s, fecha_vencimiento_marca),
                    actualizado_en = now()
                WHERE acta = %s
                """,
                (
                    info["caracter"], info["es_lead"], info["email"] or None,
                    info["email_apoderado"] or None, info["motivo_sin_email"] or None,
                    nuevo_score, info.get("cuit"), info.get("fecha_publicacion"),
                    info.get("estado_tramite"), info.get("fecha_concesion"),
                    info.get("numero_disposicion"), info.get("fecha_vencimiento_marca"),
                    acta,
                ),
            )
        conn.commit()

    return {"acta": acta, **info, "lead_score": nuevo_score}


@app.get("/api/marcas/buscar-marca")
def buscar_marca(
    denominacion: str = Query(..., min_length=2),
    clase: str = Query(""),
    _: str = Depends(verificar_login),
):
    """Busca marcas por denominación en INPI — usado por el popup de
    oposición cuando el fundamento solo dio un número de registro (sin ACTA
    directa para linkear). Devuelve una lista de candidatas (mejor
    esfuerzo, ver inpi_lead.buscar_marca_por_denominacion), para que la
    persona elija cuál abrir — no adivinamos un único match.

    Si la búsqueda tal cual no encuentra nada, reintenta sin espacios: la
    denominación citada en el fundamento legal puede venir separada
    ("BALI STONE") mientras que en INPI está cargada pegada ("BALISTONE")
    — el backfill automático (resolver_marca_oponente, en validar_leads.py)
    ya hacía este mismo reintento, pero este endpoint (el que usa el botón
    del panel) no lo tenía, así que la persona veía "no se encontró ninguna
    marca" aunque sí existiera. Caso real detectado el 29/09/2026: acta de
    BALISTONE citando "BALI STONE"."""
    filas = inpi_lead.buscar_marca_por_denominacion(denominacion, clase)
    denominacion_usada = denominacion
    if not filas:
        sin_espacios = re.sub(r"\s+", "", denominacion)
        if sin_espacios and sin_espacios != denominacion:
            filas_sin_espacios = inpi_lead.buscar_marca_por_denominacion(sin_espacios, clase)
            if filas_sin_espacios:
                filas = filas_sin_espacios
                denominacion_usada = sin_espacios
    return {"denominacion": denominacion, "denominacion_usada": denominacion_usada, "clase": clase, "resultados": filas}


@app.get("/api/crons")
def listar_crons(_: str = Depends(verificar_login)):
    resultado = []
    for c in CRONS_DEFINIDOS:
        item = {
            "nombre": c["nombre"],
            "descripcion": c["descripcion"],
            "cron": c["cron"],
            "workflow_file": c["workflow_file"],
            "proxima_ejecucion": _proxima_ejecucion(c["cron"]),
            **_ultima_corrida_workflow(c["workflow_file"]),
        }
        # "Progreso" real del trabajo pendiente (no solo si la última corrida
        # anduvo bien) — cuántas marcas todavía están esperando este proceso,
        # calculado en vivo contra la base, no contra el log de la corrida.
        if c.get("pendientes_sql"):
            try:
                with conexion() as conn:
                    with conn.cursor() as cur:
                        cur.execute(c["pendientes_sql"])
                        item["pendientes"] = cur.fetchone()[0]
                        item["pendientes_etiqueta"] = c.get("pendientes_etiqueta", "pendientes")
            except Exception as e:
                item["pendientes"] = None
                item["pendientes_error"] = str(e)
        resultado.append(item)
    return resultado


_WORKFLOWS_DISPARABLES = {c["workflow_file"] for c in CRONS_DEFINIDOS}


@app.post("/api/crons/correr")
def correr_cron(workflow_file: str = Query(...), _: str = Depends(verificar_login)):
    """Dispara a mano (workflow_dispatch) uno de los workflows que ya
    aparecen en /crons, en vez de esperar a su próximo horario o tener que
    ir a GitHub Actions. Solo permite disparar los workflows conocidos de
    CRONS_DEFINIDOS (nunca un nombre arbitrario que venga del frontend).

    Requiere que GITHUB_TOKEN tenga permiso "Actions: Read AND write" sobre
    el repo (el resto del panel solo necesita "Read-only") — si falta ese
    permiso, GitHub devuelve 403/404 acá y se lo mostramos tal cual al
    usuario en vez de fallar en silencio."""
    if workflow_file not in _WORKFLOWS_DISPARABLES:
        raise HTTPException(status_code=400, detail=f"Workflow desconocido: {workflow_file}")
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=400, detail="Falta configurar GITHUB_TOKEN en el panel")

    r = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/dispatches",
        headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
        json={"ref": "main"},
        timeout=15,
    )
    if r.status_code == 204:
        return {"ok": True}
    if r.status_code in (403, 404):
        raise HTTPException(
            status_code=502,
            detail="GitHub rechazó el disparo manual — el GITHUB_TOKEN del panel necesita permiso "
                   "\"Actions: Read and write\" (hoy alcanza con Read-only para ver el estado, pero "
                   "no para dispararlos).",
        )
    raise HTTPException(status_code=502, detail=f"GitHub devolvió {r.status_code}: {r.text[:200]}")


# --- Comentarios internos ---------------------------------------------------
# Notas entre las personas del equipo, opcionalmente vinculadas a un acta y/o
# dirigidas a una persona puntual. El autor sale siempre del login (nunca del
# body), así nadie puede escribir "en nombre de" otro.

class ComentarioNuevo(BaseModel):
    texto: str
    acta: Optional[str] = None
    destinatario: Optional[str] = None


def _normalizar_acta(acta: Optional[str]) -> Optional[str]:
    if acta is None:
        return None
    acta = re.sub(r"\D", "", acta)  # acepta "4.797.001", " 4797001 ", etc.
    return acta or None


COLUMNAS_COMENTARIO = """
    c.id, c.autor, c.destinatario, c.acta, c.texto, c.creado_en,
    c.resuelto, c.resuelto_por, c.resuelto_en,
    m.denominacion_inpi, m.denominacion, m.titular, m.cuit
"""


@app.get("/api/yo")
def quien_soy(usuario: str = Depends(verificar_login)):
    """Usuario logueado + lista de usuarios del panel (para el selector
    "Para:" de los comentarios). No expone claves."""
    return {"usuario": usuario, "usuarios": sorted(USUARIOS_PANEL.keys())}


@app.get("/api/comentarios")
def listar_comentarios(
    usuario: str = Depends(verificar_login),
    acta: Optional[str] = None,
    estado: str = "abiertos",  # "abiertos" | "resueltos" | "todos"
    para_mi: bool = False,
    limit: int = Query(200, le=500),
):
    condiciones, valores = [], []
    if acta is not None:
        condiciones.append("c.acta = %s")
        valores.append(_normalizar_acta(acta))
    if estado == "abiertos":
        condiciones.append("NOT c.resuelto")
    elif estado == "resueltos":
        condiciones.append("c.resuelto")
    if para_mi:
        condiciones.append("(c.destinatario = %s OR c.destinatario IS NULL) AND c.autor <> %s")
        valores.extend([usuario, usuario])
    where_sql = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT {COLUMNAS_COMENTARIO}
                FROM comentarios c
                LEFT JOIN marcas m ON m.acta = c.acta
                {where_sql}
                ORDER BY c.creado_en DESC
                LIMIT %s
                """,
                valores + [limit],
            )
            return cur.fetchall()


@app.post("/api/comentarios")
def crear_comentario(body: ComentarioNuevo, usuario: str = Depends(verificar_login)):
    texto = (body.texto or "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="El comentario está vacío")
    if len(texto) > 5000:
        raise HTTPException(status_code=400, detail="El comentario es demasiado largo (máx. 5000 caracteres)")
    destinatario = (body.destinatario or "").strip() or None
    if destinatario and destinatario not in USUARIOS_PANEL:
        raise HTTPException(status_code=400, detail=f"No existe el usuario {destinatario}")
    acta = _normalizar_acta(body.acta)
    if body.acta and not acta:
        raise HTTPException(status_code=400, detail="Número de acta inválido")
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO comentarios (autor, destinatario, acta, texto)
                VALUES (%s, %s, %s, %s) RETURNING id
                """,
                (usuario, destinatario, acta, texto),
            )
            nuevo = cur.fetchone()
        conn.commit()
    return {"ok": True, "id": nuevo["id"]}


@app.post("/api/comentarios/{comentario_id}/resolver")
def resolver_comentario(comentario_id: int, valor: bool = True, usuario: str = Depends(verificar_login)):
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE comentarios
                SET resuelto = %s,
                    resuelto_por = CASE WHEN %s THEN %s ELSE NULL END,
                    resuelto_en  = CASE WHEN %s THEN now() ELSE NULL END
                WHERE id = %s
                """,
                (valor, valor, usuario, valor, comentario_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="No existe ese comentario")
        conn.commit()
    return {"ok": True}


@app.delete("/api/comentarios/{comentario_id}")
def borrar_comentario(comentario_id: int, usuario: str = Depends(verificar_login)):
    """Solo quien lo escribió lo puede borrar (para corregir un error);
    para dar por cerrado un tema está "Resolver"."""
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT autor FROM comentarios WHERE id = %s", (comentario_id,))
            fila = cur.fetchone()
            if not fila:
                raise HTTPException(status_code=404, detail="No existe ese comentario")
            if fila[0] != usuario:
                raise HTTPException(status_code=403, detail="Solo quien escribió el comentario lo puede borrar")
            cur.execute("DELETE FROM comentarios WHERE id = %s", (comentario_id,))
        conn.commit()
    return {"ok": True}


@app.get("/api/comentarios/resumen")
def resumen_comentarios(usuario: str = Depends(verificar_login)):
    """Contador para el encabezado: comentarios de otros (para mí o para
    todos) escritos después de la última vez que abrí /comentarios, y
    cuántos abiertos van dirigidos a mí."""
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  COUNT(*) FILTER (WHERE c.creado_en > COALESCE(v.visto_hasta, '-infinity')),
                  COUNT(*) FILTER (WHERE NOT c.resuelto AND c.destinatario = %s)
                FROM comentarios c
                LEFT JOIN comentarios_visto v ON v.usuario = %s
                WHERE c.autor <> %s AND (c.destinatario = %s OR c.destinatario IS NULL)
                """,
                (usuario, usuario, usuario, usuario),
            )
            nuevos, abiertos_para_mi = cur.fetchone()
    return {"nuevos": nuevos, "abiertos_para_mi": abiertos_para_mi}


@app.post("/api/comentarios/visto")
def marcar_comentarios_vistos(usuario: str = Depends(verificar_login)):
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO comentarios_visto (usuario, visto_hasta) VALUES (%s, now())
                ON CONFLICT (usuario) DO UPDATE SET visto_hasta = now()
                """,
                (usuario,),
            )
        conn.commit()
    return {"ok": True}


@app.get("/api/comentarios/por-acta")
def comentarios_por_acta(actas: str = Query(""), _: str = Depends(verificar_login)):
    """Cantidad de comentarios (total y abiertos) por acta, para mostrar el
    globito 💬 en cada fila de la tabla. `actas` = lista separada por coma."""
    lista = [a for a in (_normalizar_acta(x) for x in actas.split(",")) if a][:1000]
    if not lista:
        return {}
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT acta, COUNT(*), COUNT(*) FILTER (WHERE NOT resuelto)
                FROM comentarios WHERE acta = ANY(%s) GROUP BY acta
                """,
                (lista,),
            )
            return {a: {"total": t, "abiertos": ab} for a, t, ab in cur.fetchall()}


@app.get("/api/version")
def version():
    """Qué commit está corriendo este proceso ahora mismo — lo usa el workflow
    verificar_despliegue.yml para detectar un deploy de Railway atascado
    (compara esto contra el último commit de main; ver CRONS_DEFINIDOS).
    Sin login: no expone nada sensible, y así el workflow no necesita
    guardar la clave del panel como secret aparte."""
    return {
        "commit": (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "")[:7] or None,
        "rama": os.environ.get("RAILWAY_GIT_BRANCH"),
    }


@app.get("/")
def index(_: str = Depends(verificar_login)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/titular/{clave}")
def pagina_titular(clave: str, _: str = Depends(verificar_login)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "titular.html"))


@app.get("/ayuda")
def pagina_ayuda(_: str = Depends(verificar_login)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "ayuda.html"))


@app.get("/crons")
def pagina_crons(_: str = Depends(verificar_login)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "crons.html"))


@app.get("/boletines")
def pagina_boletines(_: str = Depends(verificar_login)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "boletines.html"))


@app.get("/comentarios")
def pagina_comentarios(_: str = Depends(verificar_login)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "comentarios.html"))


class ArchivosSinCache(StaticFiles):
    """Los archivos de /static (comun.js, comun.css) cambian seguido y no
    tienen versión en el nombre. Sin esto, el navegador puede quedarse con
    una versión vieja cacheada (ej. un comun.js sin alguna función nueva)
    aunque el HTML que lo referencia ya sea el nuevo — como pasó con
    badgeOposicion. Cache-Control: no-cache obliga a revalidar en cada carga."""

    def file_response(self, *args, **kwargs):
        respuesta = super().file_response(*args, **kwargs)
        respuesta.headers["Cache-Control"] = "no-cache"
        return respuesta


app.mount("/static", ArchivosSinCache(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
