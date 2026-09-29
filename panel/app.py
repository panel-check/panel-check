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
    """
    with conexion() as conn:
        with conn.cursor() as cur:
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
            # Estado del trámite (Concedida/Denegada/etc.) y fechas de la
            # sección RESOLUCIÓN del expediente — ver revisar_estado.py.
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
        conn.commit()

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
    estado_tramite, fecha_concesion, numero_disposicion, fecha_vencimiento_marca
"""

RE_CUIT_VALIDO = re.compile(r"^\d{10,11}$")

# --- Sección "Automatizaciones" (/crons) -----------------------------------
# GitHub reporta esta org/repo con mayúscula/guiones distintos según la API
# que se use; funciona igual para leer workflows.
GITHUB_REPO = os.environ.get("GITHUB_REPO", "marcaskom-lgtm/kom-marcas-inpi")
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
    },
    {
        "nombre": "Revisión de estado del trámite",
        "descripcion": "Para las marcas que todavía no tienen una resolución firme "
                        "(ni Concedida ni Denegada), vuelve a mirar el expediente y guarda "
                        "el estado, la fecha de concesión y el vencimiento apenas INPI resuelve.",
        "workflow_file": "revisar_estado.yml",
        "cron": "0 11 * * 1",
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


@app.get("/api/marcas")
def listar_marcas(
    _: str = Depends(verificar_login),
    boletin: Optional[str] = None,
    clase: Optional[int] = None,
    es_lead: Optional[bool] = None,
    contactado: Optional[bool] = None,
    tuvo_oposicion: Optional[bool] = None,
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
    if boletin:
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

    where_sql = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    sql = f"""
        SELECT {COLUMNAS_MARCA}
        FROM marcas
        {where_sql}
        ORDER BY {sort} {order_sql} NULLS LAST, acta DESC
        LIMIT %s OFFSET %s
    """
    valores_paginado = valores + [limit, offset]

    sql_total = f"SELECT count(*) FROM marcas {where_sql}"

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


@app.get("/api/crons")
def listar_crons(_: str = Depends(verificar_login)):
    resultado = []
    for c in CRONS_DEFINIDOS:
        resultado.append({
            "nombre": c["nombre"],
            "descripcion": c["descripcion"],
            "cron": c["cron"],
            "proxima_ejecucion": _proxima_ejecucion(c["cron"]),
            **_ultima_corrida_workflow(c["workflow_file"]),
        })
    return resultado


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
