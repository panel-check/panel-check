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
        conn.commit()

COLUMNAS_ORDENABLES = {
    "lead_score", "acta", "boletin", "clase", "titular", "fecha_presentacion",
    "fecha_publicacion", "creado_en", "actualizado_en",
}

COLUMNAS_MARCA = """
    acta, boletin, clase, tipo, denominacion, denominacion_inpi,
    fecha_presentacion, fecha_publicacion, titular, pais, cuit, matricula_agente,
    caracter, es_lead, email, email_apoderado, lead_score, link,
    contactado, contactado_en, motivo_sin_email,
    tuvo_oposicion, detalle_oposicion, revisado_oposicion_en
"""

RE_CUIT_VALIDO = re.compile(r"^\d{10,11}$")


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
                    actualizado_en = now()
                WHERE acta = %s
                """,
                (
                    info["caracter"], info["es_lead"], info["email"] or None,
                    info["email_apoderado"] or None, info["motivo_sin_email"] or None,
                    nuevo_score, info.get("cuit"), info.get("fecha_publicacion"), acta,
                ),
            )
        conn.commit()

    return {"acta": acta, **info, "lead_score": nuevo_score}


@app.get("/")
def index(_: str = Depends(verificar_login)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/titular/{clave}")
def pagina_titular(clave: str, _: str = Depends(verificar_login)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "titular.html"))


@app.get("/ayuda")
def pagina_ayuda(_: str = Depends(verificar_login)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "ayuda.html"))


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
