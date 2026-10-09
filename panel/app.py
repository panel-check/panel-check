"""
Panel propio de leads — API + frontend estático.

Backend chiquito (FastAPI) que se conecta a la misma Postgres del pipeline
(Railway) para mostrar la tabla `marcas` con filtros/orden pensados para
prospección, y para marcar leads como "contactado" (el pipeline nunca toca
esa columna, es exclusiva del panel).

Login: usuarios con clave hasheada en Postgres y sesión con cookie (con
"Cerrar sesión"), ver auth.py / auth_api.py. Los usuarios se administran desde
el panel (Mi cuenta → Usuarios).

Variables de entorno:
    DATABASE_URL    - la misma que usa cargar_db.py
    PANEL_RECUPERAR - opcional y temporal, "usuario:clave": al arrancar deja
                      ese usuario activo, administrador y con esa clave (para
                      cuando nadie puede entrar). Borrarla después de usarla.
    PANEL_USER / PANEL_PASSWORD / PANEL_USERS
                    - formato viejo (HTTP Basic). Solo se leen una vez, para
                      crear los usuarios si la tabla usuarios_panel está vacía.
                      Después se pueden (y conviene) borrar de Railway.
    GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_CALENDAR_ID
                    - opcionales, para la sección Calendario (sincronización
                      con Google Calendar): el contenido completo del .json de
                      la cuenta de servicio y el ID del calendario. Sin esto la
                      sección muestra cómo conectarlo. Ver calendario_core.py.
    KAN_URL         - opcional, dirección de Kan (tablero de tareas) a la que
                      lleva la pestaña "Tareas" del menú (/tareas).
    GITHUB_TOKEN    - opcional, para la sección "Automatizaciones" (/crons):
                      un Personal Access Token (fine-grained) con permiso
                      "Actions: Read-only" sobre este repo. Sin esto, esa
                      sección muestra el horario pero no el estado de la
                      última corrida.

Correr local:
    DATABASE_URL=... PANEL_RECUPERAR=admin:unaClaveLarga uvicorn app:app --reload
"""

import inspect
import os
import re
import secrets
from contextlib import contextmanager
from typing import Optional
from urllib.parse import parse_qsl

import psycopg2
import psycopg2.extras
import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import accesos_api
import analisis_api
import analisis_marca
import auth
import auth_api
import calendario_api
import calendario_core
import cartera
import cartera_api
import formularios_api
import mails_api
import mails_core
import formularios_core
import inpi_lead

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("Falta la variable de entorno DATABASE_URL")

# Sin /docs ni /openapi.json públicos: no hace falta mostrarle a cualquiera el
# mapa completo de la API.
app = FastAPI(title="Panel de leads", docs_url=None, redoc_url=None, openapi_url=None)


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
    # Usuarios y sesiones del login (ver auth.py). Va primero y aparte: no
    # toca `marcas`, y sin esto nadie puede entrar.
    try:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SET lock_timeout = '5s'")
                auth.inicializar(cur)
            conn.commit()
    except Exception as e:
        print(f"[startup] inicialización del login falló: {e}")

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

    # Tablas del CRM de leads (sección /crm): tampoco tocan `marcas`.
    try:
        with conexion() as conn:
            with conn.cursor() as cur:
                _crear_tablas_crm(cur)
            conn.commit()
    except Exception as e:
        print(f"[startup] tablas del CRM salteadas (no bloqueante): {e}")

    # Tablas de Clientes / vigilancia marcaria (sección /clientes): tampoco
    # tocan `marcas`.
    try:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SET lock_timeout = '3s'")
                cartera.crear_tablas(cur)
                formularios_core.crear_tablas(cur)
            conn.commit()
    except Exception as e:
        print(f"[startup] tablas de clientes/vigilancia salteadas (no bloqueante): {e}")

    # Configuración de los mails (pestaña /mails): no toca `marcas`.
    try:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SET lock_timeout = '3s'")
                mails_core.crear_tablas(cur)
            conn.commit()
    except Exception as e:
        print(f"[startup] tablas de mails salteadas (no bloqueante): {e}")

    # Accesos rápidos (el ⚡ del menú): búsquedas guardadas, no tocan `marcas`.
    try:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SET lock_timeout = '3s'")
                accesos_api.crear_tablas(cur)
            conn.commit()
    except Exception as e:
        print(f"[startup] tabla de accesos rápidos salteada (no bloqueante): {e}")

    # Análisis de marca (pestaña de la ficha del titular): no toca `marcas`.
    try:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SET lock_timeout = '3s'")
                analisis_marca.crear_tablas(cur)
            conn.commit()
    except Exception as e:
        print(f"[startup] tabla de análisis de marca salteada (no bloqueante): {e}")

    # Calendario sincronizado con Google Calendar (sección /calendario): no toca
    # `marcas`. El sondeo (un hilo que pregunta a Google cada 2 minutos qué
    # cambió) solo se prende si están GOOGLE_SERVICE_ACCOUNT_JSON y GOOGLE_CALENDAR_ID.
    try:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SET lock_timeout = '3s'")
                calendario_core.crear_tablas(cur)
            conn.commit()
    except Exception as e:
        print(f"[startup] tablas del calendario salteadas (no bloqueante): {e}")
    try:
        calendario_core.iniciar_sondeo(conexion)
    except Exception as e:
        print(f"[startup] sondeo del calendario no arrancó (no bloqueante): {e}")
    # Mail diario «AGENDA» de las 20 hs (reuniones y llamadas de mañana); necesita el calendario conectado.
    try:
        calendario_core.iniciar_agenda_diaria(conexion)
    except Exception as e:
        print(f"[startup] la agenda diaria no arrancó (no bloqueante): {e}")


def _crear_tablas_crm(cur):
    """CRM de leads (ver sección "CRM de leads" más abajo y /crm).

    - crm_leads: una fila por TITULAR (no por acta), con la etapa comercial,
      a quién está asignado, teléfono, próximo seguimiento y, si se cerró,
      servicio contratado y honorarios. `clave` es la misma clave de titular
      que usa todo el panel: el CUIT, o el nombre normalizado si todavía no
      hay CUIT (ver _cte_marcas_con_clave). Un titular sin fila acá está en
      la etapa "nuevo".
    - crm_actividad: historial de gestiones (llamadas, mails, notas...) y
      registros automáticos ("sistema") de cambios de etapa/asignación.
      No es FK a crm_leads a propósito: se puede registrar actividad de un
      lead que todavía está en "nuevo" (sin fila en crm_leads).
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_leads (
            clave                TEXT PRIMARY KEY,
            etapa                TEXT NOT NULL DEFAULT 'nuevo',
            asignado             TEXT,
            telefono             TEXT,
            proximo_seguimiento  DATE,
            motivo_descarte      TEXT,
            servicio             TEXT,
            monto                NUMERIC(14, 2),
            moneda               TEXT DEFAULT 'ARS',
            etapa_cambiada_en    TIMESTAMPTZ,
            alta_en              TIMESTAMPTZ NOT NULL DEFAULT now(),
            modificado_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
            modificado_por       TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crm_leads_etapa ON crm_leads(etapa)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_actividad (
            id         SERIAL PRIMARY KEY,
            clave      TEXT NOT NULL,
            autor      TEXT NOT NULL,
            tipo       TEXT NOT NULL,
            texto      TEXT NOT NULL,
            creado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_crm_actividad_clave ON crm_actividad(clave, creado_en DESC)"
    )
    # Clave de titular precalculada por acta (ver _asegurar_claves): la
    # recalcula el propio panel cuando cambian titulares/CUIT en `marcas`.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_claves (
            acta   TEXT PRIMARY KEY,
            nom    TEXT,
            clave  TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crm_claves_clave ON crm_claves(clave)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crm_claves_nom ON crm_claves(nom)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_claves_estado (
            id              SMALLINT PRIMARY KEY DEFAULT 1,
            firma           TEXT,
            actualizado_en  TIMESTAMPTZ,
            CONSTRAINT crm_claves_estado_una_fila CHECK (id = 1)
        )
        """
    )


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
    # Registro de corridas de los procesos automáticos (scripts/registro.py)
    # -> reporte por día del "+" de cada tarjeta en Automatizaciones.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS registro_corridas (
            id          BIGSERIAL PRIMARY KEY,
            proceso     TEXT NOT NULL,
            creado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
            metricas    JSONB NOT NULL DEFAULT '{}'::jsonb,
            run_id      TEXT,
            run_url     TEXT
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_registro_corridas_proceso ON registro_corridas(proceso, creado_en DESC)"
    )
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
    # Esquema de revisión a los 10/23/33 días (ver revisar_oposiciones.py)
    cur.execute("ALTER TABLE marcas ADD COLUMN IF NOT EXISTS opo_chequeos INTEGER")
    cur.execute("ALTER TABLE marcas ADD COLUMN IF NOT EXISTS opo_ultimo_chequeo_en TIMESTAMPTZ")
    cur.execute("ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_detectada_en TIMESTAMPTZ")
    cur.execute("ALTER TABLE marcas ADD COLUMN IF NOT EXISTS reintentos_n INTEGER DEFAULT 0")
    cur.execute("ALTER TABLE marcas ADD COLUMN IF NOT EXISTS reintento_ultimo_en TIMESTAMPTZ")
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
    # Oposición marcada como "ya atendida" a mano desde la ficha del lead: deja
    # de mostrarse como pendiente de ofrecer ayuda, de avisarse por mail y de
    # revisarse de nuevo.
    cur.execute("ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_atendida BOOLEAN")
    cur.execute("ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_atendida_en TIMESTAMPTZ")
    cur.execute("ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_atendida_por TEXT")
    # Estado de la oposición leído del expediente de INPI (scripts/oposiciones_expediente.py):
    # sin_notificar / notificada_en_plazo / plazo_vencido / vista_pendiente /
    # oposicion_sin_detalle (sirven) y contestada / levantada / con_apoderado (no sirven).
    for _col, _tipo in (("estado_oposicion", "TEXT"), ("estado_oposicion_detalle", "TEXT"),
                        ("estado_oposicion_en", "TIMESTAMPTZ"), ("oposicion_sirve", "BOOLEAN"),
                        ("oposicion_fecha_presentacion", "DATE"), ("oposicion_fecha_notificacion", "DATE"),
                        ("oposicion_fecha_vencimiento", "DATE"), ("oposicion_fecha_levantamiento", "DATE"),
                        ("oposicion_agente_oponente", "TEXT"), ("oposicion_posible_apoderado", "BOOLEAN"),
                        ("estado_oposicion_version", "INTEGER")):
        cur.execute(f"ALTER TABLE marcas ADD COLUMN IF NOT EXISTS {_col} {_tipo}")
    # Lead marcado a mano como "tiene gestor/apoderado" desde la ficha: sale de los
    # leads (es_lead = false) y guarda lo anterior para poder deshacerlo.
    for _col, _tipo in (("con_gestor_manual", "BOOLEAN"), ("con_gestor_manual_en", "TIMESTAMPTZ"),
                        ("con_gestor_manual_por", "TEXT"), ("gestor_nombre", "TEXT"),
                        ("caracter_previo", "TEXT"), ("lead_score_previo", "INTEGER")):
        cur.execute(f"ALTER TABLE marcas ADD COLUMN IF NOT EXISTS {_col} {_tipo}")
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
    tuvo_oposicion, detalle_oposicion, revisado_oposicion_en, oposicion_detectada_en,
    opo_chequeos, opo_ultimo_chequeo_en,
    oponente_nombre, oponente_tipo_doc, oponente_numero_doc, oponente_cuit,
    fundamento_oposicion, actas_marca_oponente, marca_oponente_denominacion,
    marca_oponente_numero_registro,
    representacion_posterior_oposicion, detalle_representacion_posterior,
    oposicion_atendida, oposicion_atendida_en, oposicion_atendida_por,
    estado_oposicion, estado_oposicion_detalle, oposicion_sirve,
    oposicion_fecha_presentacion, oposicion_fecha_notificacion,
    oposicion_fecha_vencimiento, oposicion_fecha_levantamiento,
    oposicion_agente_oponente, oposicion_posible_apoderado,
    con_gestor_manual, con_gestor_manual_en, con_gestor_manual_por, gestor_nombre,
    estado_tramite, fecha_concesion, numero_disposicion, fecha_vencimiento_marca,
    fuente
"""

RE_CUIT_VALIDO = re.compile(r"^\d{10,11}$")

KAN_URL_DEFECTO = "https://fulfilling-achievement-production-7586.up.railway.app"

# --- Sección "Automatizaciones" (/crons) -----------------------------------
# GitHub reporta esta org/repo con mayúscula/guiones distintos según la API
# que se use; funciona igual para leer workflows.
GITHUB_REPO = os.environ.get("GITHUB_REPO", "panel-check/panel-check")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# Sección "Automatizaciones", organizada por lo que cada proceso le da al
# equipo (no por cómo está hecho por dentro). Pedido del usuario el
# 30/09/2026: "hay muchas automatizaciones y me pierdo".
#
#   GRUPOS_AUTOMATIZACIONES -> grupos (secciones de la página)
#     -> tarjetas (lo que ve el usuario; una tarjeta puede juntar varios
#        workflows, p.ej. los 3 reintentos en "Completar datos faltantes")
#        -> procesos (un workflow de GitHub Actions cada uno, con su propio
#           botón "Correr ahora" -- nunca se disparan juntos)
#
# "metricas": consultas que devuelven UN valor (número o texto) con el dato
# útil de la tarjeta ("leads nuevos hoy"), no el detalle técnico.
# "cron" tiene que coincidir con el schedule del .yml (UTC); "horario" es el
# mismo horario en hora Argentina, escrito para humanos.
# Leads publicados que todavía tienen pendiente alguno de sus hitos de revisión
# (10, 23 y 33 días). Los ya revisados con el esquema viejo cuentan como hechos.
_SQL_OPOSICIONES_PENDIENTES = (
    "SELECT COUNT(*) FROM marcas WHERE es_lead = true AND fecha_publicacion IS NOT NULL "
    "AND fecha_publicacion <= current_date - 10 AND ("
    "COALESCE(opo_chequeos, CASE WHEN revisado_oposicion_en IS NOT NULL THEN 3 ELSE 0 END) < 1 "
    "OR (COALESCE(opo_chequeos, CASE WHEN revisado_oposicion_en IS NOT NULL THEN 3 ELSE 0 END) < 2 "
    "AND fecha_publicacion <= current_date - 23) "
    "OR (COALESCE(opo_chequeos, CASE WHEN revisado_oposicion_en IS NOT NULL THEN 3 ELSE 0 END) < 3 "
    "AND fecha_publicacion <= current_date - 33))"
)

GRUPOS_AUTOMATIZACIONES = [
    {
        "id": "leads",
        "titulo": "Conseguir leads",
        "subtitulo": "Lo que trae clientes nuevos al panel.",
        "plegado": False,
        "tarjetas": [
            {
                "nombre": "Solicitudes nuevas del día",
                "descripcion": "Busca en INPI las solicitudes presentadas en los últimos días, antes de que "
                               "salgan en el boletín, para poder contactar al titular semanas antes que nadie.",
                "horario": "Todos los días: 19:35 y 1:40 hs (escaneo largo) y 10:05 y 16:05 hs (sondeo corto)",
                "metricas": [
                    {"sql": "SELECT COUNT(*) FROM marcas WHERE fuente = 'escaneo_directo' "
                            "AND creado_en >= date_trunc('day', now() AT TIME ZONE 'America/Argentina/Buenos_Aires') "
                            "AT TIME ZONE 'America/Argentina/Buenos_Aires'",
                     "etiqueta": "leads nuevos hoy", "destacada": True},
                    {"sql": "SELECT COUNT(*) FROM marcas WHERE fuente = 'escaneo_directo' "
                            "AND creado_en >= now() - interval '7 days'",
                     "etiqueta": "en los últimos 7 días"},
                    # Última acta que INPI ya tenía cargada (lead o no): hasta
                    # dónde llegó el escaneo. Es el puntero de escaneo_actas.
                    {"sql": "SELECT replace(to_char(ultima_acta_confirmada, 'FM9,999,999'), ',', '.') "
                            "FROM escaneo_actas WHERE id = 1",
                     "etiqueta": "última acta encontrada en INPI"},
                ],
                "procesos": [
                    {"workflow_file": "escanear_actas.yml",
                     "cron": ["35 22 * * *", "40 4 * * 0,1,2,4,5,6", "5 13 * * *", "5 19 * * *"]},
                ],
            },
            {
                "nombre": "Boletín publicado",
                "descripcion": "Cuando INPI publica un boletín de Marcas Nuevas, lo importa entero y marca "
                               "cuáles son leads (sin abogado). Procesa los boletines nuevos de a uno, desde "
                               "las 0:05 del miércoles (INPI los publica a las 0:00); cada uno tarda ~1 h 30.",
                "horario": "Madrugada del miércoles, desde las 0:05 hs (y red de seguridad 1:35 hs los demás días)",
                "metricas": [
                    {"sql": "SELECT boletin || ' (' || COUNT(*) FILTER (WHERE es_lead) || ' leads)' "
                            "FROM marcas WHERE boletin IS NOT NULL "
                            "GROUP BY boletin ORDER BY MAX(creado_en) DESC LIMIT 1",
                     "etiqueta": "último boletín importado", "destacada": True},
                ],
                "procesos": [
                    {"workflow_file": "pipeline.yml", "cron": ["5 3 * * 3", "35 4 * * 0,1,2,4,5,6"]},
                ],
            },
        ],
    },
    {
        "id": "seguimiento",
        "titulo": "Seguir a los leads",
        "subtitulo": "Novedades que dan motivo para contactar.",
        "plegado": False,
        "tarjetas": [
            {
                "nombre": "Oposiciones y vistas",
                "descripcion": "Revisa a cada lead a los 10, 23 y 33 días de su publicación en el boletín "
                               "(muchas oposiciones llegan antes de los 30 días) y vuelve a mirar seguido los que "
                               "ya tienen una oposición. El mail de aviso sale a las 7:30.",
                "horario": "Todos los días, 22 hs",
                "metricas": [
                    {"sql": "SELECT COUNT(*) FROM marcas WHERE es_lead = true AND tuvo_oposicion = true "
                            "AND COALESCE(oposicion_detectada_en, revisado_oposicion_en) >= now() - interval '7 days'",
                     "etiqueta": "detectadas en los últimos 7 días", "destacada": True},
                ],
                "procesos": [
                    {"etiqueta": "Diario", "workflow_file": "revisar_oposiciones.yml", "cron": "0 1 * * *",
                     "horario": "todos los días, 22 hs",
                     "pendientes_sql": _SQL_OPOSICIONES_PENDIENTES},
                    {"etiqueta": "Puesta al día (manual)", "workflow_file": "oposiciones_puesta_al_dia.yml",
                     "cron": None, "horario": "solo a mano, una vez (2 a 4 hs)",
                     "pendientes_sql": _SQL_OPOSICIONES_PENDIENTES},
                ],
            },
            {
                "nombre": "Avisos por mail de la mañana",
                "descripcion": "Manda a las 7:30, en un solo horario, los mails de oposiciones nuevas, de "
                               "alertas/plazos/novedades de la cartera y de formularios de clientes pendientes.",
                "horario": "Todos los días, 7:30 hs",
                "metricas": [],
                "procesos": [
                    {"workflow_file": "avisos_manana.yml", "cron": "30 10 * * *"},
                ],
            },
            {
                "nombre": "Concesiones y vencimientos",
                "descripcion": "Detecta cuándo INPI concede o deniega una marca y guarda la fecha de "
                               "concesión y el vencimiento (para DDJJ y renovaciones).",
                "horario": "Lunes, 4:20 hs",
                "metricas": [
                    {"sql": "SELECT COUNT(*) FROM marcas WHERE es_lead = true "
                            "AND fecha_concesion >= current_date - 30",
                     "etiqueta": "leads concedidos en los últimos 30 días", "destacada": True},
                ],
                "procesos": [
                    {"workflow_file": "revisar_estado.yml", "cron": "20 7 * * 1"},
                ],
            },
        ],
    },
    {
        "id": "clientes",
        "titulo": "Clientes y vigilancia",
        "subtitulo": "Cuida las marcas de la cartera de clientes.",
        "plegado": False,
        "tarjetas": [
            {
                "nombre": "Vigilancia marcaria",
                "descripcion": "Compara las solicitudes nuevas (boletines y escaneo de actas) con las marcas de los "
                               "clientes, crea alertas por parecidos y por clientes que presentan con otro agente, "
                               "suma por matrícula. El mail con alertas, plazos y novedades sale a las 7:30.",
                "horario": "Todos los días, 0:20, 6:20 y 12:20 hs",
                "metricas": [
                    {"sql": "SELECT COUNT(*) FROM vigilancia_alertas WHERE estado IN ('nueva','monitorear','oponer')",
                     "etiqueta": "alertas abiertas", "destacada": True},
                    {"sql": "SELECT COUNT(*) FROM vigilancia_alertas WHERE creada_en >= now() - interval '7 days'",
                     "etiqueta": "alertas nuevas en los últimos 7 días"},
                    {"sql": "SELECT COUNT(*) FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id "
                            "WHERE cm.vigilar AND c.activo",
                     "etiqueta": "marcas vigiladas"},
                ],
                "procesos": [
                    {"workflow_file": "vigilancia.yml", "cron": "20 3,9,15 * * *"},
                ],
            },
            {
                "nombre": "Seguimiento de la cartera",
                "descripcion": "Vuelve a leer en INPI los expedientes de las marcas de los clientes (estado, "
                               "concesión, vencimiento, oposiciones, movimientos nuevos) y guarda las novedades.",
                "horario": "Todos los días, 22:35 hs",
                "metricas": [
                    {"sql": "SELECT COUNT(*) FROM cartera_novedades WHERE detectado_en >= now() - interval '7 days'",
                     "etiqueta": "novedades en los últimos 7 días", "destacada": True},
                    {"sql": "SELECT COUNT(*) FROM cartera_marcas WHERE consultado_en IS NULL",
                     "etiqueta": "marcas pendientes de consultar"},
                ],
                "procesos": [
                    {"workflow_file": "revisar_cartera.yml", "cron": "35 1 * * *",
                     "pendientes_sql": "SELECT COUNT(*) FROM cartera_marcas WHERE consultado_en IS NULL"},
                ],
            },
        ],
    },
    {
        "id": "mantenimiento",
        "titulo": "Mantenimiento",
        "subtitulo": "Corre solo y casi nunca hace falta mirarlo. Se abre solo si algo falla.",
        "plegado": True,
        "tarjetas": [
            {
                "nombre": "Completar datos faltantes",
                "descripcion": "Vuelve a consultar INPI para las marcas que quedaron a medias (casi siempre "
                               "por un bloqueo puntual): sin verificar, sin email o sin nombre.",
                "horario": "Una vez por noche, cada uno por separado",
                "metricas": [],
                "procesos": [
                    {"etiqueta": "Sin verificar", "workflow_file": "reintentar_sin_verificar.yml",
                     "cron": "5 2 * * 0,1,2,4,5,6",
                     "horario": "todas las noches menos la del martes, 23:05 hs",
                     "pendientes_sql": "SELECT COUNT(*) FROM marcas WHERE es_lead IS NULL AND TRIM(COALESCE(matricula_agente, '')) IN ('', 'Part.')"},
                    {"etiqueta": "Sin email", "workflow_file": "reintentar_sin_email.yml",
                     "cron": "10 8 * * 0,1,2,4,5,6",
                     "horario": "madrugadas menos la del miércoles, 5:10 hs",
                     "pendientes_sql": "SELECT COUNT(*) FROM marcas WHERE es_lead = true "
                                       "AND (email IS NULL OR email = '')"},
                    {"etiqueta": "Sin nombre", "workflow_file": "backfill_denominacion.yml",
                     "cron": "50 8 * * 0,1,2,4,5,6",
                     "horario": "madrugadas menos la del miércoles, 5:50 hs (y al terminar cada escaneo y boletín)",
                     "pendientes_sql": "SELECT COUNT(*) FROM marcas WHERE es_lead IS NOT FALSE "
                                       "AND COALESCE(NULLIF(TRIM(denominacion), ''), "
                                       "NULLIF(TRIM(denominacion_inpi), '')) IS NULL "
                                       "AND COALESCE(tipo, '') <> 'F'"},
                ],
            },
            {
                "nombre": "Panel actualizado",
                "descripcion": "Controla que el panel en línea tenga la última versión del sistema. "
                               "Si falla, la versión nueva quedó trabada en Railway.",
                "horario": "Cada hora",
                "metricas": [],
                "procesos": [
                    {"workflow_file": "verificar_despliegue.yml", "cron": "0 * * * *"},
                ],
            },
        ],
    },
]

# Lista plana de workflows (para validar qué se puede disparar a mano).
CRONS_DEFINIDOS = [
    p for g in GRUPOS_AUTOMATIZACIONES for t in g["tarjetas"] for p in t["procesos"]
]

# Reporte por día (botón "+" de cada tarjeta). Por proceso: qué columnas
# mostrar (las claves son las que anota scripts/registro.py al terminar
# cada corrida) y cómo juntarlas en el día:
#   "suma"   -> se suman todas las corridas del día (leads nuevos, emails...)
#   "ultimo" -> foto al final del día (lo que "sigue" pendiente)
#   "lista"  -> texto, se juntan (números de boletín)
# "historico_sql": para los días ANTERIORES a que existiera el registro, lo
# que se puede reconstruir desde la base (marcado como "estimado" en el
# reporte). Devuelve (dia, jsonb con las mismas claves). %(desde)s = inicio.
_TZ = "America/Argentina/Buenos_Aires"
REPORTES_AUTOMATIZACIONES = {
    "escanear_actas.yml": {
        "columnas": [
            ("leads_nuevos", "Leads nuevos", "suma"),
            ("leads_con_email", "con email", "suma"),
            ("con_agente", "Con abogado (descartadas)", "suma"),
            ("actas_nuevas", "Actas nuevas en INPI", "suma"),
            ("bloqueos", "Bloqueos de INPI", "suma"),
            ("ultima_acta", "Última acta del día", "ultimo"),
        ],
        "historico_sql": f"""
            SELECT (creado_en AT TIME ZONE '{_TZ}')::date AS dia,
                   jsonb_build_object(
                     'leads_nuevos', COUNT(*),
                     'leads_con_email', COUNT(*) FILTER (WHERE COALESCE(email, '') <> ''),
                     'ultima_acta', MAX(acta)
                   )
            FROM marcas WHERE fuente = 'escaneo_directo' AND creado_en >= %(desde)s
            GROUP BY 1""",
    },
    "pipeline.yml": {
        "columnas": [
            ("boletin", "Boletín", "lista"),
            ("leads", "Leads", "suma"),
            ("leads_con_email", "con email", "suma"),
            ("ya_detectados_por_escaneo", "Ya los teníamos por el escaneo", "suma"),
            ("marcas_cargadas", "Marcas del boletín", "suma"),
            ("sin_verificar", "Sin verificar", "suma"),
        ],
        "historico_sql": f"""
            SELECT dia, jsonb_build_object(
                     'boletines', COUNT(*), 'boletin', string_agg(numero, ', ' ORDER BY numero),
                     'marcas_cargadas', SUM(total), 'leads', SUM(leads),
                     'leads_con_email', SUM(con_email), 'ya_detectados_por_escaneo', SUM(escaneo),
                     'sin_verificar', SUM(sin_verif))
            FROM (
              SELECT (b.procesado_en AT TIME ZONE '{_TZ}')::date AS dia, b.numero,
                     COUNT(m.acta) AS total,
                     COUNT(*) FILTER (WHERE m.es_lead) AS leads,
                     COUNT(*) FILTER (WHERE m.es_lead AND COALESCE(m.email, '') <> '') AS con_email,
                     COUNT(*) FILTER (WHERE m.fuente = 'escaneo_directo') AS escaneo,
                     COUNT(*) FILTER (WHERE m.es_lead IS NULL) AS sin_verif
              FROM boletines b JOIN marcas m ON m.boletin = b.numero
              WHERE b.procesado_en >= %(desde)s
              GROUP BY 1, 2
            ) x GROUP BY dia""",
    },
    "revisar_oposiciones.yml": {
        "columnas": [
            ("con_oposicion", "Con oposición o vista", "suma"),
            ("sin_oposicion", "Sin oposición", "suma"),
            ("revisadas", "Leads revisados", "suma"),
        ],
        "historico_sql": f"""
            SELECT (revisado_oposicion_en AT TIME ZONE '{_TZ}')::date AS dia,
                   jsonb_build_object(
                     'revisadas', COUNT(*),
                     'con_oposicion', COUNT(*) FILTER (WHERE tuvo_oposicion),
                     'sin_oposicion', COUNT(*) FILTER (WHERE tuvo_oposicion IS NOT TRUE)
                   )
            FROM marcas WHERE es_lead = true AND revisado_oposicion_en >= %(desde)s
            GROUP BY 1""",
    },
    "vigilancia.yml": {
        "columnas": [
            ("alertas_parecido", "Alertas por parecido", "suma"),
            ("alertas_otro_agente", "Clientes con otro agente", "suma"),
            ("solicitudes_comparadas", "Solicitudes comparadas", "suma"),
            ("sumadas_por_matricula", "Sumadas por matrícula", "suma"),
            ("mails_enviados", "Mails enviados", "suma"),
            ("marcas_vigiladas", "Marcas vigiladas", "ultimo"),
        ],
    },
    "revisar_cartera.yml": {
        "columnas": [
            ("novedades", "Novedades", "suma"),
            ("actualizadas", "Marcas actualizadas", "suma"),
            ("revisadas", "Revisadas", "suma"),
            ("bloqueos", "Bloqueos de INPI", "suma"),
            ("pendientes_restantes", "Pendientes al final del día", "ultimo"),
        ],
    },
    "revisar_estado.yml": {
        "columnas": [
            ("concedidas", "Concedidas", "suma"),
            ("denegadas", "Denegadas", "suma"),
            ("sin_resolucion", "Sin resolución todavía", "suma"),
            ("revisadas", "Revisadas", "suma"),
            ("bloqueos", "Bloqueos de INPI", "suma"),
        ],
    },
    "reintentar_sin_verificar.yml": {
        "columnas": [
            ("resueltas", "Resueltas", "suma"),
            ("resultaron_lead", "resultaron lead", "suma"),
            ("resultaron_con_agente", "tenían abogado", "suma"),
            ("reintentadas", "Reintentadas", "suma"),
            ("siguen", "Pendientes al final del día", "ultimo"),
        ],
    },
    "reintentar_sin_email.yml": {
        "columnas": [
            ("emails_conseguidos", "Emails conseguidos", "suma"),
            ("reintentados", "Reintentados", "suma"),
            ("formularios_avisados", "Formularios de clientes avisados (respaldo)", "suma"),
            ("siguen", "Pendientes al final del día", "ultimo"),
        ],
    },
    "backfill_denominacion.yml": {
        "columnas": [
            ("nombres_recuperados", "Nombres recuperados", "suma"),
            ("figurativas_sin_texto", "Figurativas sin texto", "suma"),
            ("revisadas", "Revisadas", "suma"),
            ("siguen", "Pendientes al final del día", "ultimo"),
        ],
    },
}


def _proxima_ejecucion(expresion_cron) -> str:
    """Próxima ejecución de uno o varios crons (el más cercano). None si el
    proceso no tiene horario (solo se dispara a mano)."""
    if not expresion_cron:
        return None
    if isinstance(expresion_cron, (list, tuple)):
        proximas = [x for x in (_proxima_cron_unico(e) for e in expresion_cron) if x]
        return min(proximas) if proximas else None
    return _proxima_cron_unico(expresion_cron)


def _proxima_cron_unico(expresion_cron: str) -> str:
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


@contextmanager
def conexion():
    # jit=off: las consultas del CRM agregan toda la tabla `marcas` y el JIT de
    # Postgres tardaba ~1 s solo en compilarlas (más que la consulta en sí).
    conn = psycopg2.connect(DATABASE_URL, options="-c jit=off")
    try:
        yield conn
    finally:
        conn.close()


# Login con sesiones (ver auth.py / auth_api.py): rutas /login, /api/login,
# /api/logout, /cuenta, /api/usuarios... + middleware de seguridad.
_auth = auth_api.instalar(app, conexion)
verificar_login = _auth.verificar_login      # API: 401 sin sesión
verificar_pagina = _auth.verificar_pagina    # pantallas: redirige a /login


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


# Filtro "Oposición": "oposición" y "vista" no tienen columna propia. Una
# OPOSICIÓN es la de un tercero (el detalle guardado dice "OPO…"), una VISTA
# es una observación de oficio de INPI. Es el mismo criterio que usa el panel
# para dibujar los badges (badgeOposicion en comun.js: /OPO/i) y que usa
# revisar_oposiciones.py al elegir qué fila guardar (si hay las dos, guarda la
# oposición).
TIPOS_OPOSICION = ("oposicion", "vista")
SQL_ES_OPOSICION_TERCERO = "(tuvo_oposicion IS TRUE AND COALESCE(detalle_oposicion, '') ~* 'OPO')"
SQL_ES_SOLO_VISTA = "(tuvo_oposicion IS TRUE AND COALESCE(detalle_oposicion, '') !~* 'OPO')"


def filtros_marcas(
    boletin: Optional[str] = None,
    clase: Optional[int] = None,
    es_lead: Optional[bool] = None,
    contactado: Optional[bool] = None,
    tuvo_oposicion: Optional[bool] = None,
    tipo_oposicion: Optional[str] = None,  # "oposicion" | "vista" (implica tuvo_oposicion)
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    # Atajo "últimos N días" de Fecha Boletín: se calcula con la fecha de HOY
    # cada vez que se usa (los accesos rápidos guardan esto, no fechas fijas).
    fecha_dias: Optional[int] = Query(None, ge=1, le=3650),
    tiene_email: Optional[bool] = None,
    tiene_titular: Optional[bool] = None,
    tiene_clase: Optional[bool] = None,
    estado_marca: Optional[str] = None,  # "pendiente" | "Concedida" | "Denegada"
    # Filtro avanzado "Misma marca en varias clases" (ver _cruce_clases_sql):
    # o bien un mínimo de clases (multiclase_min), o bien un par de clases
    # puntuales (multiclase_a + multiclase_b).
    multiclase_min: Optional[int] = Query(None, ge=2, le=45),
    multiclase_a: Optional[int] = Query(None, ge=1, le=45),
    multiclase_b: Optional[int] = Query(None, ge=1, le=45),
    multiclase_titular: Optional[str] = None,  # "" (cualquiera) | "mismo" | "distinto"
    q: Optional[str] = None,
) -> dict:
    """Los filtros de la lista de marcas, como dependencia: los comparten
    /api/marcas (la tabla) y /api/marcas/actas (seleccionar "todas las que
    coinciden"), para que las dos vean exactamente lo mismo."""
    if tipo_oposicion and tipo_oposicion not in TIPOS_OPOSICION:
        raise HTTPException(status_code=400, detail="tipo_oposicion tiene que ser 'oposicion' o 'vista'")
    return dict(locals())


def _consulta_marcas(f: dict) -> dict:
    """Arma el WHERE (y el cruce de clases, si está activo) a partir de los
    filtros de filtros_marcas()."""
    condiciones = []
    valores = []
    if f["boletin"] == "pre":
        # Marcas detectadas por el escaneo directo de actas que todavía no
        # salieron en ningún boletín (ver scripts/escanear_actas_nuevas.py).
        condiciones.append("boletin IS NULL")
    elif (f["boletin"] or "").startswith("mes:"):
        # Todos los boletines publicados en un mes ("mes:2026-10").
        condiciones.append("boletin IN (SELECT numero FROM boletines WHERE to_char(fecha, 'YYYY-MM') = %s)")
        valores.append(f["boletin"][4:])
    elif f["boletin"]:
        condiciones.append("boletin = %s")
        valores.append(f["boletin"])
    if f["clase"] is not None:
        condiciones.append("clase = %s")
        valores.append(f["clase"])
    # Cuando se busca por texto (acta, CUIT, marca, titular, mail…) se quiere
    # encontrar la marca sin importar si es lead o si ya se contactó: la
    # búsqueda ignora esos dos filtros (el resto de los filtros sí se aplican).
    hay_busqueda = bool((f["q"] or "").strip())
    if f["es_lead"] is not None and not hay_busqueda:
        condiciones.append("es_lead = %s")
        valores.append(f["es_lead"])
    if f["contactado"] is not None and not hay_busqueda:
        condiciones.append("contactado = %s")
        valores.append(f["contactado"])
    if f["tipo_oposicion"] == "oposicion":
        condiciones.append(SQL_ES_OPOSICION_TERCERO)
    elif f["tipo_oposicion"] == "vista":
        condiciones.append(SQL_ES_SOLO_VISTA)
    elif f["tuvo_oposicion"] is not None:
        condiciones.append("tuvo_oposicion = %s")
        valores.append(f["tuvo_oposicion"])
    fecha_desde, fecha_hasta, fecha_dias = f["fecha_desde"], f["fecha_hasta"], f["fecha_dias"]
    if fecha_desde or fecha_hasta or fecha_dias:
        # Mismo criterio que muestra el panel como "Fecha Boletín"
        # (fechaPublicacionOFallback en comun.js): fecha_publicacion cuando
        # ya se verificó, si no fecha_presentacion — filtrar solo por
        # fecha_publicacion dejaría afuera filas que el panel sí muestra
        # como dentro del rango, por el fallback. Las marcas pre-boletín
        # (boletin IS NULL) no tienen "Fecha Boletín" y quedan fuera del rango.
        fecha_boletin_sql = "(CASE WHEN boletin IS NULL THEN NULL ELSE COALESCE(fecha_publicacion, fecha_presentacion) END)"
        if fecha_desde:
            condiciones.append(f"{fecha_boletin_sql} >= %s")
            valores.append(fecha_desde)
        if fecha_hasta:
            condiciones.append(f"{fecha_boletin_sql} <= %s")
            valores.append(fecha_hasta)
        if fecha_dias:
            # "Últimos N días" contando hoy (hora Argentina): últimos 7 días
            # = de hace 6 días a hoy.
            condiciones.append(f"{fecha_boletin_sql} >= (now() AT TIME ZONE '{_TZ}')::date - %s")
            valores.append(fecha_dias - 1)
    if f["tiene_email"] is not None:
        # Filtro "Avanzado": marcas a las que todavía no se les encontró
        # ningún email (ni del titular ni del apoderado) — útil para ver
        # a quién le falta ese dato antes de poder contactarlo.
        condicion_email = "(email IS NOT NULL AND email <> '') OR (email_apoderado IS NOT NULL AND email_apoderado <> '')"
        condiciones.append(f"({condicion_email})" if f["tiene_email"] else f"NOT ({condicion_email})")
    if f["tiene_titular"] is not None:
        condicion_titular = "titular IS NOT NULL AND titular <> ''"
        condiciones.append(condicion_titular if f["tiene_titular"] else f"NOT ({condicion_titular})")
    if f["tiene_clase"] is not None:
        # Filtro "Avanzado": marcas a las que todavía no se les pudo leer el
        # número de clase (ni del boletín ni del expediente).
        condiciones.append("clase IS NOT NULL" if f["tiene_clase"] else "clase IS NULL")
    if f["estado_marca"]:
        # Mismo criterio que ESTADOS_FINALES en scripts/revisar_estado.py:
        # "pendiente" = todavía sin una resolución firme.
        if f["estado_marca"] == "pendiente":
            condiciones.append("(estado_tramite IS NULL OR estado_tramite NOT IN ('Concedida', 'Denegada'))")
        else:
            condiciones.append("estado_tramite = %s")
            valores.append(f["estado_marca"])
    if f["q"]:
        condiciones.append(
            """(
                titular ILIKE %s OR denominacion ILIKE %s OR denominacion_inpi ILIKE %s
                OR email ILIKE %s OR email_apoderado ILIKE %s
                OR cuit ILIKE %s OR acta ILIKE %s
            )"""
        )
        patron = f"%{f['q']}%"
        valores.extend([patron, patron, patron, patron, patron, patron, patron])

    join_sql = ""
    columnas_extra = ""
    orden_previo = ""
    valores_join = []
    cruce = _cruce_clases_sql(f["multiclase_min"], f["multiclase_a"], f["multiclase_b"], f["multiclase_titular"])
    if cruce:
        join_sql, valores_join, condicion_extra = cruce
        if condicion_extra:
            condiciones.append(condicion_extra[0])
            valores.extend(condicion_extra[1])
        columnas_extra = ", cruce.clases AS clases_misma_marca"
        # Las filas de una misma marca van juntas (y dentro de cada marca,
        # el orden que eligió el usuario).
        orden_previo = "cruce.nom, "

    return {
        "where_sql": f"WHERE {' AND '.join(condiciones)}" if condiciones else "",
        "valores": valores_join + valores,  # primero los del JOIN, después los del WHERE
        "join_sql": join_sql,
        "columnas_extra": columnas_extra,
        "orden_previo": orden_previo,
    }


@app.get("/api/marcas")
def listar_marcas(
    _: str = Depends(verificar_login),
    f: dict = Depends(filtros_marcas),
    sort: str = "lead_score",
    order: str = "desc",
    limit: int = Query(200, le=1000),
    offset: int = 0,
):
    if sort not in COLUMNAS_ORDENABLES:
        sort = "lead_score"
    order_sql = "DESC" if order.lower() != "asc" else "ASC"
    c = _consulta_marcas(f)

    sql = f"""
        SELECT {COLUMNAS_MARCA}{c['columnas_extra']}
        FROM marcas
        {c['join_sql']}
        {c['where_sql']}
        ORDER BY {c['orden_previo']}{sort} {order_sql} NULLS LAST, acta DESC
        LIMIT %s OFFSET %s
    """
    sql_total = f"SELECT count(*) FROM marcas {c['join_sql']} {c['where_sql']}"

    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, c["valores"] + [limit, offset])
            filas = cur.fetchall()
            cur.execute(sql_total, c["valores"])
            total = cur.fetchone()["count"]

    return {"total": total, "rows": filas}


# Tope de actas que se devuelven/aceptan de una vez en la selección masiva.
MAX_ACTAS_MASIVO = 5000


@app.get("/api/marcas/actas")
def actas_del_filtro(
    _: str = Depends(verificar_login),
    f: dict = Depends(filtros_marcas),
):
    """Todas las actas que coinciden con los filtros (no solo las 100 de la
    página): es lo que usa "Seleccionar las N que coinciden con el filtro"."""
    c = _consulta_marcas(f)
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT marcas.acta FROM marcas {c['join_sql']} {c['where_sql']} "
                f"ORDER BY lead_score DESC NULLS LAST, acta DESC LIMIT %s",
                c["valores"] + [MAX_ACTAS_MASIVO + 1],
            )
            actas = [fila[0] for fila in cur.fetchall()]
    truncado = len(actas) > MAX_ACTAS_MASIVO
    return {"total": min(len(actas), MAX_ACTAS_MASIVO), "actas": actas[:MAX_ACTAS_MASIVO], "truncado": truncado}


# ── Accesos rápidos: una búsqueda guardada es un querystring de filtros ─────
# Los filtros que se pueden guardar son los de filtros_marcas() más el orden.
CLAVES_ACCESO_RAPIDO = set(inspect.signature(filtros_marcas).parameters) | {"sort", "order"}
_FILTROS_BOOL = {"es_lead", "contactado", "tuvo_oposicion", "tiene_email", "tiene_titular", "tiene_clase"}
_FILTROS_INT = {"clase", "fecha_dias", "multiclase_min", "multiclase_a", "multiclase_b"}


def _filtros_desde_consulta(consulta: str) -> dict:
    """Querystring guardado → el dict que espera _consulta_marcas (lo mismo
    que arma FastAPI con los parámetros de /api/marcas)."""
    crudos = dict(parse_qsl(consulta or "", keep_blank_values=False))
    f = {}
    for nombre in inspect.signature(filtros_marcas).parameters:
        valor = (crudos.get(nombre) or "").strip()
        if not valor:
            f[nombre] = None
        elif nombre in _FILTROS_BOOL:
            f[nombre] = {"true": True, "false": False}.get(valor.lower())
        elif nombre in _FILTROS_INT:
            f[nombre] = int(valor) if valor.lstrip("-").isdigit() else None
        elif nombre == "tipo_oposicion":
            f[nombre] = valor if valor in TIPOS_OPOSICION else None
        else:
            f[nombre] = valor
    return f


def _contar_consulta(consulta: str) -> int:
    """Cuántas marcas da hoy una búsqueda guardada."""
    c = _consulta_marcas(_filtros_desde_consulta(consulta))
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM marcas {c['join_sql']} {c['where_sql']}", c["valores"])
            return cur.fetchone()[0]


_cache_fechas_inpi = {"ts": 0.0, "datos": {}}


def _fechas_boletines_inpi() -> dict:
    """{numero: date} según el listado de INPI (la fecha exacta de publicación).
    Se cachea 6 horas; si INPI no responde devuelve lo último que se tenía."""
    import time
    from datetime import date
    if time.time() - _cache_fechas_inpi["ts"] < 6 * 3600 and _cache_fechas_inpi["datos"]:
        return _cache_fechas_inpi["datos"]
    try:
        datos = {}
        for b in inpi_lead.listar_boletines_marcas_nuevas():
            m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", b.get("fecha") or "")
            if m:
                datos[str(b["numero"])] = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        _cache_fechas_inpi.update(ts=time.time(), datos=datos)
    except Exception:
        pass
    return _cache_fechas_inpi["datos"]


@app.get("/api/boletines")
def listar_boletines(_: str = Depends(verificar_login)):
    """Lista de boletines para el filtro. La fecha sale de la base; si falta
    (boletines cargados antes de que el pipeline la guardara) se completa con
    el listado de INPI y, si no, con la fecha de publicación más común de sus
    marcas, y se guarda para no volver a buscarla."""
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT b.numero, b.fecha, b.total_marcas,
                       (SELECT MODE() WITHIN GROUP (ORDER BY m.fecha_publicacion)
                          FROM marcas m WHERE m.boletin = b.numero
                           AND m.fecha_publicacion IS NOT NULL) AS fecha_marcas
                  FROM boletines b ORDER BY b.numero DESC
                """
            )
            filas = cur.fetchall()
            faltan = [f for f in filas if not f["fecha"]]
            if faltan:
                de_inpi = _fechas_boletines_inpi()
                for f in faltan:
                    nueva = de_inpi.get(str(f["numero"])) or f["fecha_marcas"]
                    if nueva:
                        f["fecha"] = nueva
                        cur.execute("UPDATE boletines SET fecha = %s WHERE numero = %s AND fecha IS NULL",
                                    (nueva, f["numero"]))
                conn.commit()
            for f in filas:
                f.pop("fecha_marcas", None)
            return filas


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
    criterio que agruparPorTitular en el frontend).

    Desde el CRM (30/09/2026) se resuelve con la misma clave de titular que
    usa el CRM (_cte_marcas_con_clave): si llega un nombre y ese nombre ya
    tiene un CUIT único en otra marca, se muestran también las marcas con
    ese CUIT (antes quedaban separadas). Si con eso no aparece nada (ej. un
    marcador compartido tipo "(titular a confirmar manualmente)"), se usa la
    búsqueda exacta de siempre."""
    clave_crm = None
    filas = []
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                _asegurar_claves(cur)
                conn.commit()
            except Exception as e:  # la ficha tiene que andar aunque falle el CRM
                conn.rollback()
                print(f"[crm] no se pudieron recalcular las claves: {e}")
            real = _resolver_clave(cur, clave)
            actas = _actas_de_clave(cur, real) if real else []
            if actas:
                cur.execute(
                    f"SELECT {COLUMNAS_MARCA} FROM marcas WHERE acta = ANY(%s) "
                    f"ORDER BY fecha_presentacion DESC",
                    (actas,),
                )
                filas = cur.fetchall()
                if filas:
                    clave_crm = real
            if not filas:
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

    titular = next((f["titular"] for f in filas if f.get("titular")), None)
    cuit = next((f["cuit"] for f in filas if f.get("cuit")), None)
    return {
        "clave": clave,
        "clave_crm": clave_crm,  # None: este titular no se puede seguir en el CRM todavía
        "titular": titular,
        "cuit": cuit,
        "rows": filas,
    }


class OposicionAtendida(BaseModel):
    actas: list[str]
    atendida: bool = True


@app.post("/api/marcas/oposicion-atendida")
def marcar_oposicion_atendida(datos: OposicionAtendida, usuario: str = Depends(verificar_login)):
    """Marca (o desmarca) como "ya atendida" la oposición/vista de esas actas.
    Una oposición atendida deja de contar como "sin apoderado": desaparece la
    alerta de ofrecer ayuda, de la agenda del CRM y del mail de avisos, y
    revisar_oposiciones.py deja de volver a mirarla. La oposición en sí sigue
    visible (es un dato real del expediente)."""
    if not datos.actas:
        raise HTTPException(status_code=400, detail="Falta indicar las actas")
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE marcas
                SET oposicion_atendida = %s,
                    oposicion_atendida_en = CASE WHEN %s THEN now() ELSE NULL END,
                    oposicion_atendida_por = CASE WHEN %s THEN %s ELSE NULL END
                WHERE acta = ANY(%s) AND tuvo_oposicion IS TRUE
                """,
                (datos.atendida, datos.atendida, datos.atendida, usuario, datos.actas),
            )
            cambiadas = cur.rowcount
        conn.commit()
    return {"ok": True, "marcas": cambiadas}


@app.post("/api/marcas/{acta}/contactado")
def marcar_contactado(acta: str, valor: bool = True, usuario: str = Depends(verificar_login)):
    """Marca/desmarca un acta como contactada. Además acompaña al CRM: si el
    titular estaba en "nuevo", pasa a "contactado"; si se desmarca la última
    acta contactada de un titular que estaba en "contactado", vuelve a "nuevo"
    (las demás etapas no se tocan)."""
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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

            cur.execute("SAVEPOINT crm_contactado")
            try:
                _asegurar_claves(cur)
                cur.execute(
                    f"WITH {_cte_marcas_con_clave(filtrar_actas=True)} SELECT clave FROM mk",
                    ([acta],),
                )
                fila_clave = cur.fetchone()
                info = None
                if fila_clave and fila_clave["clave"]:
                    actas = _actas_de_clave(cur, fila_clave["clave"])
                    cur.execute(
                        "SELECT bool_or(contactado IS TRUE) AS otras FROM marcas WHERE acta = ANY(%s) AND acta <> %s",
                        (actas, acta),
                    )
                    info = {"clave": fila_clave["clave"], "otras_contactadas": bool(cur.fetchone()["otras"])}
                if info:
                    cur.execute("SELECT etapa FROM crm_leads WHERE clave = %s", (info["clave"],))
                    fila = cur.fetchone()
                    etapa = fila["etapa"] if fila else "nuevo"
                    if valor and etapa == "nuevo":
                        _aplicar_cambios_crm(cur, info["clave"], {"etapa": "contactado"}, usuario,
                                             motivo_auto=f"marcada como contactada el acta {acta}")
                    elif not valor and etapa == "contactado" and not info["otras_contactadas"]:
                        _aplicar_cambios_crm(cur, info["clave"], {"etapa": "nuevo"}, usuario,
                                             motivo_auto=f"desmarcada como contactada el acta {acta}")
                cur.execute("RELEASE SAVEPOINT crm_contactado")
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT crm_contactado")
                print(f"[crm] no se pudo reflejar 'contactado' del acta {acta} en el CRM: {e}")
        conn.commit()
    return {"acta": acta, "contactado": valor}


# ── Selección masiva (casillas de la tabla de leads) ──────────────────────
# Todas reciben una lista de actas (las marcadas a mano, o todas las que
# coinciden con el filtro, ver /api/marcas/actas) y aplican la misma lógica
# que las acciones de a una. Lo que es del titular en el CRM (etapa,
# asignado, gestor) se aplica una sola vez por titular, aunque haya
# seleccionado varias de sus marcas.

class SeleccionActas(BaseModel):
    actas: list[str]


class ContactadoMasivo(SeleccionActas):
    contactado: bool = True


class AsignarMasivo(SeleccionActas):
    asignado: Optional[str] = None  # None / "" = dejar sin asignar


class ConGestorMasivo(SeleccionActas):
    nombre: Optional[str] = None


def _actas_masivo(actas) -> list:
    """Limpia la lista (sin repetidas ni vacías) y controla el tope."""
    vistas, limpias = set(), []
    for a in actas or []:
        a = str(a).strip()
        if a and a not in vistas:
            vistas.add(a)
            limpias.append(a)
    if not limpias:
        raise HTTPException(status_code=400, detail="No hay marcas seleccionadas")
    if len(limpias) > MAX_ACTAS_MASIVO:
        raise HTTPException(status_code=400, detail=f"Son demasiadas marcas de una vez (máximo {MAX_ACTAS_MASIVO})")
    return limpias


def _claves_de_actas(cur, actas: list) -> list:
    """Los titulares (claves del CRM) de esas actas, sin repetir."""
    _asegurar_claves(cur)
    cur.execute(
        f"WITH {_cte_marcas_con_clave(filtrar_actas=True)} SELECT DISTINCT clave FROM mk WHERE clave IS NOT NULL",
        (actas,),
    )
    return [_valor(f) for f in cur.fetchall()]


@app.get("/api/masivo/opciones")
def masivo_opciones(usuario: str = Depends(verificar_login)):
    """Usuarios a los que se puede asignar desde la barra de selección."""
    return {"usuarios": _auth.usuarios_activos(), "usuario": usuario}


@app.post("/api/masivo/contactado")
def masivo_contactado(datos: ContactadoMasivo, usuario: str = Depends(verificar_login)):
    """Marca o desmarca como contactadas todas esas actas. Igual que de a una,
    acompaña al CRM: el titular "nuevo" pasa a "contactado"; al desmarcar,
    el que estaba en "contactado" y ya no tiene ninguna marca contactada
    vuelve a "nuevo"."""
    actas = _actas_masivo(datos.actas)
    valor = datos.contactado
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE marcas
                SET contactado = %s,
                    contactado_en = CASE WHEN %s THEN COALESCE(contactado_en, now()) ELSE NULL END
                WHERE acta = ANY(%s)
                """,
                (valor, valor, actas),
            )
            marcas = cur.rowcount
            titulares = 0
            cur.execute("SAVEPOINT claves_masivo")
            try:
                claves = _claves_de_actas(cur, actas)
                cur.execute("RELEASE SAVEPOINT claves_masivo")
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT claves_masivo")
                claves = []
                print(f"[crm] no se pudieron ubicar los titulares de la selección masiva: {e}")
            for clave in claves:
                cur.execute("SAVEPOINT crm_contactado_masivo")
                try:
                    cur.execute("SELECT etapa FROM crm_leads WHERE clave = %s", (clave,))
                    fila = cur.fetchone()
                    etapa = fila["etapa"] if fila else "nuevo"
                    cambio = False
                    if valor and etapa == "nuevo":
                        _aplicar_cambios_crm(cur, clave, {"etapa": "contactado"}, usuario,
                                             motivo_auto="marcada como contactada en una selección masiva")
                        cambio = True
                    elif not valor and etapa == "contactado":
                        cur.execute(
                            "SELECT bool_or(contactado IS TRUE) AS otras FROM marcas WHERE acta = ANY(%s)",
                            (_actas_de_clave(cur, clave),),
                        )
                        if not cur.fetchone()["otras"]:
                            _aplicar_cambios_crm(cur, clave, {"etapa": "nuevo"}, usuario,
                                                 motivo_auto="desmarcada como contactada en una selección masiva")
                            cambio = True
                    cur.execute("RELEASE SAVEPOINT crm_contactado_masivo")
                    titulares += 1 if cambio else 0
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT crm_contactado_masivo")
                    print(f"[crm] no se pudo reflejar 'contactado' de {clave} en el CRM: {e}")
        conn.commit()
    return {"ok": True, "marcas": marcas, "titulares_crm": titulares}


@app.post("/api/masivo/asignar")
def masivo_asignar(datos: AsignarMasivo, usuario: str = Depends(verificar_login)):
    """Asigna (o desasigna) a una persona del equipo todos los titulares de
    esas actas, en el CRM."""
    actas = _actas_masivo(datos.actas)
    asignado = (datos.asignado or "").strip() or None
    if asignado and not _auth.usuario_existe(asignado):
        raise HTTPException(status_code=400, detail=f"No existe el usuario {asignado}")
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            claves = _claves_de_actas(cur, actas)
            for clave in claves:
                _aplicar_cambios_crm(cur, clave, {"asignado": asignado}, usuario)
        conn.commit()
    return {"ok": True, "titulares": len(claves), "asignado": asignado}


@app.post("/api/masivo/con-gestor")
def masivo_con_gestor(datos: ConGestorMasivo, usuario: str = Depends(verificar_login)):
    """Marca a mano "tiene gestor/apoderado" a los titulares de esas actas
    (salen de la lista de leads, ver crm_con_gestor). Los que no tienen marcas
    lead para marcar se saltean y se cuentan aparte."""
    actas = _actas_masivo(datos.actas)
    nombre = (datos.nombre or "").strip() or None
    if nombre and len(nombre) > 200:
        raise HTTPException(status_code=400, detail="El nombre del gestor es demasiado largo (máx. 200 caracteres)")
    marcadas = saltados = marcas = 0
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for clave in _claves_de_actas(cur, actas):
                cur.execute("SAVEPOINT con_gestor_masivo")
                try:
                    marcas += _aplicar_con_gestor(cur, clave, usuario, True, nombre)
                    cur.execute("RELEASE SAVEPOINT con_gestor_masivo")
                    marcadas += 1
                except HTTPException:
                    cur.execute("ROLLBACK TO SAVEPOINT con_gestor_masivo")
                    saltados += 1
        conn.commit()
    return {"ok": True, "titulares": marcadas, "marcas": marcas, "saltados": saltados}


def _celda_csv(valor):
    """Texto seguro para abrir en Excel: lo que empieza con = + - @ se
    interpretaría como una fórmula, así que se le antepone un apóstrofo."""
    if valor is None:
        return ""
    texto = str(valor)
    return "'" + texto if texto[:1] in ("=", "+", "-", "@") else texto


@app.post("/api/masivo/exportar")
def masivo_exportar(datos: SeleccionActas, _: str = Depends(verificar_login)):
    """CSV (para Excel) con las marcas seleccionadas."""
    import csv
    import io
    actas = _actas_masivo(datos.actas)
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT acta, boletin, clase,
                       COALESCE(NULLIF(trim(denominacion_inpi), ''), denominacion) AS marca,
                       titular, cuit, email, email_apoderado, lead_score, es_lead, contactado,
                       tuvo_oposicion, detalle_oposicion,
                       CASE WHEN boletin IS NULL THEN NULL ELSE COALESCE(fecha_publicacion, fecha_presentacion) END AS fecha_boletin,
                       fecha_concesion, link
                FROM marcas WHERE acta = ANY(%s)
                ORDER BY lead_score DESC NULLS LAST, acta DESC
                """,
                (actas,),
            )
            filas = cur.fetchall()
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=";")
    escritor.writerow(["Acta", "Boletín", "Clase", "Marca", "Titular", "CUIT", "Email", "Email apoderado",
                       "Score", "Lead", "Contactado", "Oposición/vista", "Detalle", "Fecha boletín",
                       "Fecha concedida", "Link"])
    for r in filas:
        tipo = ""
        if r["tuvo_oposicion"]:
            tipo = "Oposición" if re.search("OPO", r["detalle_oposicion"] or "", re.I) else "Vista"
        escritor.writerow([_celda_csv(x) for x in (
            r["acta"], r["boletin"], r["clase"], r["marca"], r["titular"], r["cuit"], r["email"],
            r["email_apoderado"], r["lead_score"], "Sí" if r["es_lead"] else "No",
            "Sí" if r["contactado"] else "No", tipo, r["detalle_oposicion"], r["fecha_boletin"],
            r["fecha_concesion"], r["link"],
        )])
    # BOM + ";" para que Excel en español lo abra con las columnas y los acentos bien.
    return Response(
        content="\ufeff" + salida.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="marcas-seleccionadas.csv"'},
    )


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
                SET caracter = CASE WHEN con_gestor_manual IS TRUE THEN caracter ELSE %s END,
                    es_lead = CASE WHEN con_gestor_manual IS TRUE THEN es_lead ELSE %s END,
                    email = %s, email_apoderado = %s,
                    motivo_sin_email = %s,
                    lead_score = CASE WHEN con_gestor_manual IS TRUE THEN lead_score ELSE %s END,
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


def _valor_sql(sql: str):
    """Ejecuta una consulta que devuelve un solo valor. None si no hay filas;
    {"error": ...} si la consulta falla (la tarjeta no se rompe)."""
    try:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                fila = cur.fetchone()
                return fila[0] if fila else None
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/crons")
def listar_crons(_: str = Depends(verificar_login)):
    """Grupos -> tarjetas -> procesos, con el estado de la última corrida de
    cada workflow y las métricas de cada tarjeta calculadas en vivo."""
    grupos = []
    for g in GRUPOS_AUTOMATIZACIONES:
        tarjetas = []
        for t in g["tarjetas"]:
            procesos = []
            for p in t["procesos"]:
                item = {
                    "etiqueta": p.get("etiqueta"),
                    "workflow_file": p["workflow_file"],
                    "horario": p.get("horario"),
                    "tiene_reporte": p["workflow_file"] in REPORTES_AUTOMATIZACIONES,
                    "proxima_ejecucion": _proxima_ejecucion(p.get("cron")),
                    **_ultima_corrida_workflow(p["workflow_file"]),
                }
                if p.get("pendientes_sql"):
                    item["pendientes"] = _valor_sql(p["pendientes_sql"])
                procesos.append(item)
            metricas = [
                {"etiqueta": m["etiqueta"], "destacada": m.get("destacada", False),
                 "valor": _valor_sql(m["sql"])}
                for m in t.get("metricas", [])
            ]
            tarjetas.append({
                "nombre": t["nombre"], "descripcion": t["descripcion"],
                "horario": t["horario"], "metricas": metricas, "procesos": procesos,
            })
        grupos.append({
            "id": g["id"], "titulo": g["titulo"], "subtitulo": g["subtitulo"],
            "plegado": g["plegado"], "tarjetas": tarjetas,
        })
    return grupos


@app.get("/api/crons/reporte")
def reporte_cron(
    workflow_file: str = Query(...),
    dias: int = Query(30, ge=1, le=365),
    _: str = Depends(verificar_login),
):
    """Qué hizo un proceso, día por día (hora Argentina), para los últimos
    `dias` días. Sale de registro_corridas (lo que anota cada corrida); para
    los días anteriores a que existiera ese registro usa lo que se puede
    reconstruir desde la base (marcado "estimado"). Días sin actividad
    aparecen en 0 para que se vean los huecos."""
    import datetime as _dt

    conf = REPORTES_AUTOMATIZACIONES.get(workflow_file)
    if not conf:
        raise HTTPException(status_code=404, detail="Este proceso no tiene reporte")
    columnas = conf["columnas"]
    modo = {k: m for k, _l, m in columnas}

    hoy = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-3))).date()
    desde_dia = hoy - _dt.timedelta(days=dias - 1)
    desde_ts = _dt.datetime.combine(desde_dia, _dt.time(0), _dt.timezone(_dt.timedelta(hours=-3)))

    por_dia: dict = {}
    primer_registro = None
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT (creado_en AT TIME ZONE '{_TZ}')::date, metricas, run_url
                    FROM registro_corridas
                    WHERE proceso = %s AND creado_en >= %s
                    ORDER BY creado_en""",
                (workflow_file, desde_ts),
            )
            filas = cur.fetchall()
            cur.execute(
                f"SELECT MIN((creado_en AT TIME ZONE '{_TZ}')::date) FROM registro_corridas WHERE proceso = %s",
                (workflow_file,),
            )
            primer_registro = cur.fetchone()[0]

            for dia, met, _url in filas:
                d = por_dia.setdefault(dia, {"corridas": 0, "valores": {}, "estimado": False})
                d["corridas"] += 1
                for k, v in (met or {}).items():
                    m = modo.get(k)
                    if m == "suma" and isinstance(v, (int, float)):
                        d["valores"][k] = d["valores"].get(k, 0) + v
                    elif m == "ultimo":
                        d["valores"][k] = v
                    elif m == "lista" and v not in (None, ""):
                        previos = d["valores"].get(k)
                        d["valores"][k] = f"{previos}, {v}" if previos else str(v)

            if conf.get("historico_sql"):
                cur.execute(conf["historico_sql"], {"desde": desde_ts})
                for dia, met in cur.fetchall():
                    if dia is None or (primer_registro and dia >= primer_registro):
                        continue  # ese día ya lo cubre el registro real
                    por_dia[dia] = {"corridas": None, "valores": dict(met or {}), "estimado": True}

    # Rango a mostrar: desde el primer día con datos (no llenar de ceros
    # meses en los que el sistema ni existía) hasta hoy.
    inicio = min(por_dia) if por_dia else hoy
    inicio = max(inicio, desde_dia)
    salida = []
    d = hoy
    while d >= inicio:
        fila = por_dia.get(d, {"corridas": 0, "valores": {}, "estimado": False})
        salida.append({"dia": d.isoformat(), **fila})
        d -= _dt.timedelta(days=1)

    totales = {}
    for k, _l, m in columnas:
        if m == "suma":
            totales[k] = sum(f["valores"].get(k) or 0 for f in salida
                             if isinstance(f["valores"].get(k), (int, float)))
        elif m == "ultimo":
            ultimo = next((f["valores"][k] for f in salida if k in f["valores"]), None)
            totales[k] = ultimo
        elif m == "lista":
            totales[k] = sum(1 for f in salida for x in str(f["valores"].get(k) or "").split(",") if x.strip())

    return {
        "workflow_file": workflow_file,
        "columnas": [{"clave": k, "etiqueta": l, "modo": m} for k, l, m in columnas],
        "dias": salida,
        "totales": totales,
        "registro_desde": primer_registro.isoformat() if primer_registro else None,
    }


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
    if destinatario and not _auth.usuario_existe(destinatario):
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
    todos) escritos después de la última vez que abrí el chat, cuántos
    abiertos van dirigidos a mí y cuál fue el último mensaje nuevo."""
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
            # El último mensaje nuevo, para el cartelito del chat (menu.js).
            cur.execute(
                """
                SELECT c.id, c.autor, c.destinatario, c.acta, c.texto
                FROM comentarios c
                LEFT JOIN comentarios_visto v ON v.usuario = %s
                WHERE c.autor <> %s AND (c.destinatario = %s OR c.destinatario IS NULL)
                  AND c.creado_en > COALESCE(v.visto_hasta, '-infinity')
                ORDER BY c.id DESC LIMIT 1
                """,
                (usuario, usuario, usuario),
            )
            u = cur.fetchone()
    ultimo = None
    if u:
        ultimo = {"id": u[0], "autor": u[1], "para_mi": u[2] == usuario, "acta": u[3],
                  "texto": (u[4] or "")[:200]}
    return {"nuevos": nuevos, "abiertos_para_mi": abiertos_para_mi, "ultimo": ultimo}


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


# --- CRM de leads -----------------------------------------------------------
# Seguimiento comercial por TITULAR (no por acta): una persona que pidió su
# marca en 3 clases es un solo contacto. Cada lead pasa por etapas
#   nuevo → contactado → respondio → reunion → cliente   (o descartado)
# y guarda asignación, teléfono, próximo seguimiento, historial de gestiones
# y, si se cerró, servicio contratado y honorarios.
#
# La clave del titular es la misma que usa el resto del panel (CUIT, o el
# nombre normalizado si todavía no hay CUIT), con una mejora: si una marca
# no tiene CUIT pero el mismo nombre SÍ aparece con un CUIT único en otra
# marca, se usa ese CUIT. Así las actas "sin verificar" de un titular no
# quedan como un lead aparte.

ETAPAS_CRM = ["nuevo", "contactado", "respondio", "reunion", "cliente", "descartado"]
ETAPAS_CRM_NOMBRE = {
    "nuevo": "Nuevo",
    "contactado": "Contactado",
    "respondio": "Respondió",
    "reunion": "Reunión / propuesta",
    "cliente": "Cliente",
    "descartado": "Descartado",
}
# Etapas donde el lead se está trabajando (para la agenda de plazos).
ETAPAS_CRM_ACTIVAS = ["contactado", "respondio", "reunion", "cliente"]
TIPOS_ACTIVIDAD = {
    "nota": "Nota",
    "llamada": "Llamada",
    "email": "Mail",
    "whatsapp": "WhatsApp",
    "reunion": "Reunión",
}
# Registrar una de estas gestiones en un lead "nuevo" lo pasa solo a "contactado".
TIPOS_ACTIVIDAD_CONTACTO = {"llamada", "email", "whatsapp", "reunion"}
MONEDAS_CRM = {"ARS", "USD"}
LIMITE_TARJETAS_DEFAULT = 60  # por columna, en "nuevo" y "descartado" (pueden ser miles)

NOMBRE_TITULAR_SQL = "NULLIF(upper(regexp_replace(trim(titular), '\\s+', ' ', 'g')), '')"

# Clave de respaldo cuando una marca todavía no está en crm_claves (se cargó
# después del último recálculo): CUIT, o nombre normalizado, o "ACTA n" si no
# hay CUIT ni un nombre usable (ej. el marcador "(titular a confirmar
# manualmente)", que comparten muchas personas distintas).
CLAVE_RESPALDO_SQL = f"""COALESCE(
    NULLIF(trim(m.cuit), ''),
    CASE WHEN {NOMBRE_TITULAR_SQL} IS NULL OR left({NOMBRE_TITULAR_SQL}, 1) = '('
         THEN 'ACTA ' || m.acta ELSE {NOMBRE_TITULAR_SQL} END
)"""

# Firma barata (~30 ms con 150.000 marcas) de todo lo que define las claves:
# si cambia algún titular, CUIT o se agrega/borra un acta, cambia la firma.
FIRMA_CLAVES_SQL = """
    SELECT count(*)::text || ':' ||
           coalesce(sum(hashtext(acta || '|' || coalesce(titular, '') || '|' || coalesce(cuit, ''))::numeric), 0)::text
    FROM marcas
"""
# Entre dos recálculos pasan al menos estos minutos (mientras corre el
# pipeline cambian muchas marcas seguidas; las nuevas usan la clave de
# respaldo hasta el próximo recálculo).
MINUTOS_ENTRE_RECALCULOS = 2
_LOCK_RECALCULO_CLAVES = 7310001


def _asegurar_claves(cur, forzar: bool = False) -> bool:
    """Mantiene al día la tabla crm_claves (acta → clave del titular).

    Calcular la clave de cada marca exige normalizar el nombre del titular
    con expresiones regulares y cruzar nombres con CUIT en toda la tabla
    `marcas`: con decenas de miles de marcas eso tarda segundos, así que se
    guarda en crm_claves y solo se recalcula cuando cambia la firma de
    `marcas` (titulares, CUIT o actas). El que llama tiene que hacer commit.
    Devuelve True si recalculó."""
    cur.execute(
        "SELECT firma, actualizado_en > now() - make_interval(mins => %s) AS reciente "
        "FROM crm_claves_estado WHERE id = 1",
        (MINUTOS_ENTRE_RECALCULOS,),
    )
    estado = cur.fetchone()
    if estado is not None and not isinstance(estado, dict):
        estado = {"firma": estado[0], "reciente": estado[1]}
    cur.execute(FIRMA_CLAVES_SQL)
    fila = cur.fetchone()
    firma = fila[0] if not isinstance(fila, dict) else list(fila.values())[0]
    if estado and estado["firma"] == firma:
        return False
    if estado and estado["reciente"] and not forzar:
        return False
    cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (_LOCK_RECALCULO_CLAVES,))
    fila = cur.fetchone()
    if not (fila[0] if not isinstance(fila, dict) else list(fila.values())[0]):
        return False  # otro pedido lo está recalculando justo ahora

    cur.execute("DELETE FROM crm_claves")
    cur.execute(
        f"""
        WITH base AS (
            SELECT acta, {NOMBRE_TITULAR_SQL} AS nom, NULLIF(trim(cuit), '') AS cuit FROM marcas
        ),
        nom_cuit AS (
            -- nombre normalizado → su único CUIT conocido (si tiene más de
            -- uno, son personas distintas con el mismo nombre: no se unen)
            SELECT nom, min(cuit) AS cuit_unico
            FROM base
            WHERE nom IS NOT NULL AND cuit IS NOT NULL AND left(nom, 1) <> '('
            GROUP BY nom
            HAVING COUNT(DISTINCT cuit) = 1
        )
        INSERT INTO crm_claves (acta, nom, clave)
        SELECT b.acta, b.nom,
               COALESCE(b.cuit, nc.cuit_unico,
                        CASE WHEN b.nom IS NULL OR left(b.nom, 1) = '(' THEN 'ACTA ' || b.acta ELSE b.nom END)
        FROM base b
        LEFT JOIN nom_cuit nc ON nc.nom = b.nom
        """
    )
    cur.execute(
        """
        INSERT INTO crm_claves_estado (id, firma, actualizado_en) VALUES (1, %s, now())
        ON CONFLICT (id) DO UPDATE SET firma = EXCLUDED.firma, actualizado_en = now()
        """,
        (firma,),
    )
    _reconciliar_claves_crm(cur)
    return True


def _cte_marcas_con_clave(filtrar_actas: bool = False) -> str:
    """CTE `mk`: marcas + columna `clave` del titular (de crm_claves, o la
    de respaldo para marcas nuevas). Con filtrar_actas, el primer parámetro
    de la consulta tiene que ser la lista de actas a incluir."""
    filtro = "WHERE m.acta = ANY(%s)" if filtrar_actas else ""
    return f"""
        mk AS (
            SELECT m.*, COALESCE(cc.clave, {CLAVE_RESPALDO_SQL}) AS clave
            FROM marcas m
            LEFT JOIN crm_claves cc ON cc.acta = m.acta
            {filtro}
        )
    """


def _sql_leads(con_filtro_lead: bool = True, filtrar_actas: bool = False) -> str:
    """CTE completo hasta `leads`: una fila por titular con los datos
    agregados de sus marcas + los campos del CRM. Con con_filtro_lead solo
    entran los titulares con al menos una marca lead (o que ya tienen fila
    en el CRM, para no "perder" a alguien que después sumó un apoderado).
    Con filtrar_actas, el primer parámetro es la lista de actas (ficha de
    un solo lead: no hace falta agregar toda la base)."""
    # Solo se agregan las marcas de titulares que interesan (con alguna marca
    # lead, o ya cargados en el CRM): con la base completa, agregar a todos
    # los titulares con agente es la mitad del costo y no se muestran.
    filtro_grupos = (
        "AND clave IN (SELECT clave FROM mk WHERE es_lead IS TRUE UNION SELECT clave FROM crm_leads)"
        if con_filtro_lead else ""
    )
    filtro = "WHERE m.acta = ANY(%s)" if filtrar_actas else ""
    return f"""
        WITH mk AS (
            SELECT m.acta, m.titular, m.cuit, m.email, m.es_lead, m.actualizado_en, m.clase,
                   m.denominacion_inpi, m.denominacion, m.lead_score, m.tuvo_oposicion,
                   m.representacion_posterior_oposicion, m.revisado_oposicion_en, m.boletin,
                   m.oposicion_atendida,
                   m.con_gestor_manual, m.con_gestor_manual_en, m.con_gestor_manual_por, m.gestor_nombre,
                   m.estado_tramite, m.creado_en, m.fecha_publicacion, m.fecha_presentacion,
                   COALESCE(cc.clave, {CLAVE_RESPALDO_SQL}) AS clave
            FROM marcas m
            LEFT JOIN crm_claves cc ON cc.acta = m.acta
            {filtro}
        ),
        grupos AS (
            SELECT clave,
                (array_agg(titular ORDER BY (titular IS NULL OR titular = ''), (NULLIF(trim(cuit), '') IS NULL),
                           (es_lead IS NOT TRUE), actualizado_en DESC NULLS LAST, acta))[1] AS titular,
                max(NULLIF(trim(cuit), '')) AS cuit,
                (array_agg(email ORDER BY (email IS NULL OR email = ''), (es_lead IS NOT TRUE), actualizado_en DESC NULLS LAST))[1] AS email,
                COUNT(*) AS cant_marcas,
                array_agg(acta ORDER BY acta) AS actas,
                array_agg(DISTINCT clase) FILTER (WHERE clase IS NOT NULL) AS clases,
                string_agg(DISTINCT NULLIF(COALESCE(NULLIF(trim(denominacion_inpi), ''), denominacion), ''), ' · ') AS marcas,
                max(lead_score) AS lead_score,
                bool_or(es_lead IS TRUE) AS es_lead,
                bool_or(es_lead IS FALSE) AS tiene_marcas_con_agente,
                bool_or(tuvo_oposicion IS TRUE) AS con_oposicion,
                bool_or(tuvo_oposicion IS TRUE AND representacion_posterior_oposicion IS NOT TRUE
                        AND oposicion_atendida IS NOT TRUE
                        AND con_gestor_manual IS NOT TRUE) AS oposicion_sin_apoderado,
                bool_or(con_gestor_manual IS TRUE) AS con_gestor_manual,
                max(con_gestor_manual_en) FILTER (WHERE con_gestor_manual IS TRUE) AS con_gestor_manual_en,
                (array_agg(con_gestor_manual_por ORDER BY con_gestor_manual_en DESC NULLS LAST)
                    FILTER (WHERE con_gestor_manual IS TRUE))[1] AS con_gestor_manual_por,
                max(gestor_nombre) FILTER (WHERE con_gestor_manual IS TRUE) AS gestor_nombre,
                bool_or(tuvo_oposicion IS TRUE AND oposicion_atendida IS TRUE) AS oposicion_atendida,
                max(revisado_oposicion_en) FILTER (WHERE tuvo_oposicion IS TRUE) AS oposicion_detectada_en,
                bool_or(boletin IS NULL) AS pre_boletin,
                bool_or(estado_tramite = 'Concedida') AS alguna_concedida,
                max(creado_en) AS detectado_en,
                max(COALESCE(fecha_publicacion, fecha_presentacion::date)) AS ultima_fecha
            FROM mk
            WHERE clave IS NOT NULL {filtro_grupos}
            GROUP BY clave
        ),
        leads AS (
            SELECT g.*,
                   COALESCE(c.etapa, 'nuevo') AS etapa,
                   c.asignado, c.telefono, c.proximo_seguimiento, c.motivo_descarte,
                   c.servicio, c.monto, c.moneda, c.etapa_cambiada_en,
                   c.modificado_en, c.modificado_por,
                   (SELECT max(a.creado_en) FROM crm_actividad a WHERE a.clave = g.clave) AS ultima_actividad
            FROM grupos g
            LEFT JOIN crm_leads c ON c.clave = g.clave
        )
    """


def _normalizar_nombre_titular(nombre: str) -> str:
    """Mismo criterio que NOMBRE_TITULAR_SQL / normalizarTitular en comun.js."""
    return re.sub(r"\s+", " ", (nombre or "").strip()).upper()


def _valor(fila, i=0):
    if fila is None:
        return None
    return list(fila.values())[i] if isinstance(fila, dict) else fila[i]


def _resolver_clave(cur, clave: str) -> str:
    """Una clave que llega del frontend (CUIT o nombre) → la clave real del
    titular: si es un nombre que ya tiene un CUIT único conocido, el CUIT."""
    clave = (clave or "").strip()
    if not clave or RE_CUIT_VALIDO.match(clave) or clave.startswith("ACTA "):
        return clave
    nom = _normalizar_nombre_titular(clave)
    cur.execute(
        "SELECT min(clave), COUNT(DISTINCT clave) FROM crm_claves WHERE nom = %s",
        (nom,),
    )
    fila = cur.fetchone()
    if fila and _valor(fila, 1) == 1:
        return _valor(fila, 0)
    return nom


def _actas_de_clave(cur, clave: str) -> list:
    """Todas las actas de un titular (usa el índice de crm_claves + las
    marcas cargadas después del último recálculo)."""
    cur.execute(
        f"""
        SELECT acta FROM crm_claves WHERE clave = %s
        UNION
        SELECT m.acta FROM marcas m
        LEFT JOIN crm_claves cc ON cc.acta = m.acta
        WHERE cc.acta IS NULL AND {CLAVE_RESPALDO_SQL} = %s
        """,
        (clave, clave),
    )
    return [_valor(f) for f in cur.fetchall()]


def _reconciliar_claves_crm(cur):
    """Si un lead se cargó al CRM cuando todavía no tenía CUIT (clave = su
    nombre) y después apareció el CUIT, se mueve su fila y su historial a la
    clave nueva. Corre después de cada recálculo de crm_claves; casi siempre
    no toca nada (0 filas)."""
    mapa = """
        WITH mapa AS (
            SELECT nom, min(clave) AS clave
            FROM crm_claves
            WHERE nom IS NOT NULL AND left(nom, 1) <> '('
            GROUP BY nom
            HAVING COUNT(DISTINCT clave) = 1 AND bool_and(clave <> nom)
        )
    """
    cur.execute(f"{mapa} UPDATE crm_actividad a SET clave = mapa.clave FROM mapa WHERE a.clave = mapa.nom")
    cur.execute(
        f"""{mapa}
        UPDATE crm_leads c SET clave = mapa.clave
        FROM mapa
        WHERE c.clave = mapa.nom
          AND NOT EXISTS (SELECT 1 FROM crm_leads x WHERE x.clave = mapa.clave)
        """
    )


_CON_ACENTO = "áéíóúüàèìòùâêîôûñç"
_SIN_ACENTO = "aeiouuaeiouaeiounc"


def _condiciones_leads(asignado, usuario, q, con_email, con_oposicion, pre_boletin, etapa=None):
    condiciones, valores = [], []
    if etapa:
        if etapa not in ETAPAS_CRM:
            raise HTTPException(status_code=400, detail=f"Etapa desconocida: {etapa}")
        condiciones.append("etapa = %s")
        valores.append(etapa)
    if asignado == "yo":
        condiciones.append("asignado = %s")
        valores.append(usuario)
    elif asignado == "sin":
        condiciones.append("asignado IS NULL")
    elif asignado:
        condiciones.append("asignado = %s")
        valores.append(asignado)
    if q:
        # Sin distinguir acentos ni mayúsculas ("cafe" encuentra "CAFÉ").
        sin_acentos = lambda col: f"translate(lower({col}), '{_CON_ACENTO}', '{_SIN_ACENTO}')"
        condiciones.append(
            f"({sin_acentos('titular')} LIKE %s OR cuit LIKE %s OR {sin_acentos('marcas')} LIKE %s "
            f"OR lower(email) LIKE %s OR telefono LIKE %s OR %s = ANY(actas))"
        )
        texto = q.strip().lower().translate(str.maketrans(_CON_ACENTO, _SIN_ACENTO))
        texto = texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        patron = f"%{texto}%"
        valores.extend([patron, patron, patron, patron, patron, re.sub(r"\D", "", q)])
    if con_email:
        condiciones.append("email IS NOT NULL AND email <> ''")
    if con_oposicion:
        condiciones.append("oposicion_sin_apoderado")
    if pre_boletin:
        condiciones.append("pre_boletin")
    where_sql = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    return where_sql, valores


ORDEN_TARJETAS_SQL = """
    (etapa = 'nuevo' AND oposicion_sin_apoderado) DESC,
    proximo_seguimiento ASC NULLS LAST,
    (email IS NOT NULL AND email <> '') DESC,
    COALESCE(etapa_cambiada_en, detectado_en) DESC NULLS LAST,
    clave
"""


def _hoy_ar():
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-3))).date()


def _sumar_anios(d, anios: int):
    """Art. 6 CCyC: mismo día del mes; si no existe en el mes de
    vencimiento (29/02 en año no bisiesto), el último día de ese mes."""
    import calendar
    anio = d.year + anios
    return d.replace(year=anio, day=min(d.day, calendar.monthrange(anio, d.month)[1]))


def _urgencia(dias: int) -> str:
    if dias < 0:
        return "vencido"
    if dias <= 7:
        return "urgente"
    if dias <= 60:
        return "proximo"
    return "lejano"


def _plazos_de_marcas(filas, hoy=None) -> list:
    """Plazos orientativos por marca, calculados con datos que ya tenemos:
    - cierre del plazo de oposición de terceros: 30 días corridos desde la
      publicación (se muestra mientras está abierto y hasta 30 días después);
    - oposición recibida (sin fecha límite calculada: la fija la notificación);
    - declaración jurada de uso de medio término: dentro del año siguiente al
      5.º aniversario de la concesión;
    - vencimiento del registro (renovación), tal como lo informa INPI.
    Son una ayuda para no perder de vista fechas: la fecha exacta se confirma
    siempre en el expediente."""
    import datetime as _dt
    hoy = hoy or _hoy_ar()
    plazos = []

    def agregar(r, tipo, titulo, fecha, detalle=""):
        dias = (fecha - hoy).days
        plazos.append({
            "acta": r["acta"],
            "marca": r.get("denominacion_inpi") or r.get("denominacion") or ("(figurativa, sin texto)" if (r.get("tipo") or "").strip().upper() == "F" else "(nombre pendiente)"),
            "clase": r.get("clase"),
            "tipo": tipo,
            "titulo": titulo,
            "detalle": detalle,
            "fecha": fecha,
            "dias": dias,
            "urgencia": _urgencia(dias),
        })

    for r in filas:
        pub = r.get("fecha_publicacion")
        if pub and r.get("boletin"):
            cierre = pub + _dt.timedelta(days=30)
            if (hoy - cierre).days <= 30:
                abierto = hoy <= cierre
                agregar(
                    r, "oposicion_terceros",
                    "Cierra el plazo de oposición de terceros" if abierto else "Cerró el plazo de oposición de terceros",
                    cierre, f"Publicada el {pub.strftime('%d/%m/%Y')} · 30 días corridos",
                )
        if r.get("tuvo_oposicion"):
            detectada = r.get("oposicion_detectada_en") or r.get("revisado_oposicion_en")
            fecha = detectada.date() if detectada else hoy
            resuelta = bool(r.get("representacion_posterior_oposicion") or r.get("oposicion_atendida")
                            or r.get("con_gestor_manual"))
            if r.get("representacion_posterior_oposicion"):
                detalle = "Ya se sumó un apoderado/gestor"
            elif r.get("con_gestor_manual"):
                detalle = "Marcado a mano: ya tiene gestor/apoderado"
            elif r.get("oposicion_atendida"):
                detalle = "Marcada como atendida: no hace falta ofrecer ayuda"
            else:
                detalle = "El plazo para responder corre desde la notificación: confirmar en el expediente"
            agregar(r, "oposicion_recibida", "Oposición / vista detectada", fecha, detalle)
            plazos[-1]["urgencia"] = "proximo" if resuelta else "urgente"
        conc = r.get("fecha_concesion")
        if conc:
            desde = _sumar_anios(conc, 5)
            hasta = _sumar_anios(conc, 6)
            if hoy < desde:
                agregar(r, "ddjj", "Se abre la DJ de uso de medio término", desde,
                        f"Ventana hasta el {hasta.strftime('%d/%m/%Y')} (concedida el {conc.strftime('%d/%m/%Y')})")
            elif hoy <= hasta:
                agregar(r, "ddjj", "Cierra la DJ de uso de medio término", hasta,
                        f"Ventana abierta desde el {desde.strftime('%d/%m/%Y')}")
        venc = r.get("fecha_vencimiento_marca")
        if venc and (hoy - venc).days <= 180:
            agregar(r, "renovacion", "Vence el registro (renovación)", venc, "Fecha de vencimiento informada por INPI")

    plazos.sort(key=lambda p: p["fecha"])
    return plazos


class CrmActualizacion(BaseModel):
    etapa: Optional[str] = None
    asignado: Optional[str] = None
    telefono: Optional[str] = None
    proximo_seguimiento: Optional[str] = None  # "YYYY-MM-DD" o null para borrar
    motivo_descarte: Optional[str] = None
    servicio: Optional[str] = None
    monto: Optional[float] = None
    moneda: Optional[str] = None


class CrmActividadNueva(BaseModel):
    tipo: str = "nota"
    texto: str
    proximo_seguimiento: Optional[str] = None


def _validar_fecha(valor: Optional[str]):
    import datetime as _dt
    if valor in (None, ""):
        return None
    try:
        return _dt.date.fromisoformat(valor)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Fecha inválida: {valor}")


def _clave_existente(cur, clave: str) -> str:
    """Resuelve la clave y confirma que corresponde a al menos una marca."""
    real = _resolver_clave(cur, clave)
    if not real:
        raise HTTPException(status_code=400, detail="Falta la clave del titular")
    if not _actas_de_clave(cur, real):
        raise HTTPException(status_code=404, detail="No hay marcas para ese titular")
    return real


def _registrar_actividad(cur, clave, autor, tipo, texto):
    cur.execute(
        "INSERT INTO crm_actividad (clave, autor, tipo, texto) VALUES (%s, %s, %s, %s)",
        (clave, autor, tipo, texto),
    )


def _sincronizar_contactado(cur, clave: str, etapa: str):
    """Mantiene la columna `contactado` de las marcas del titular alineada con
    la etapa del CRM (el filtro "Contactado" del panel principal sigue
    funcionando igual): "nuevo" = sin contactar; cualquier otra etapa
    (incluido "descartado") = gestionado. Es de mejor esfuerzo: si la tabla
    `marcas` está trabada por una corrida larga del pipeline, se saltea sin
    romper el guardado del CRM."""
    cur.execute("SAVEPOINT sync_contactado")
    try:
        cur.execute("SET LOCAL lock_timeout = '4s'")
        actas = _actas_de_clave(cur, clave)
        if actas:
            if etapa == "nuevo":
                cur.execute(
                    "UPDATE marcas SET contactado = false, contactado_en = NULL "
                    "WHERE acta = ANY(%s) AND contactado IS TRUE",
                    (actas,),
                )
            else:
                cur.execute(
                    "UPDATE marcas SET contactado = true, contactado_en = COALESCE(contactado_en, now()) "
                    "WHERE acta = ANY(%s) AND contactado IS NOT TRUE",
                    (actas,),
                )
        cur.execute("RELEASE SAVEPOINT sync_contactado")
    except Exception as e:
        cur.execute("ROLLBACK TO SAVEPOINT sync_contactado")
        print(f"[crm] no se pudo sincronizar 'contactado' de {clave}: {e}")


def _aplicar_cambios_crm(cur, clave: str, cambios: dict, usuario: str, motivo_auto: str = "") -> dict:
    """Guarda los campos de `cambios` en crm_leads (creando la fila si no
    existe), deja registro automático de cambios de etapa y asignación, y
    sincroniza `contactado`. Devuelve la fila actualizada."""
    cur.execute(
        "INSERT INTO crm_leads (clave, modificado_por) VALUES (%s, %s) ON CONFLICT (clave) DO NOTHING",
        (clave, usuario),
    )
    cur.execute("SELECT * FROM crm_leads WHERE clave = %s FOR UPDATE", (clave,))
    actual = cur.fetchone()
    if not isinstance(actual, dict):
        columnas = [d[0] for d in cur.description]
        actual = dict(zip(columnas, actual))

    sets, valores = [], []
    for campo, valor in cambios.items():
        if actual.get(campo) != valor:
            sets.append(f"{campo} = %s")
            valores.append(valor)

    etapa_nueva = cambios.get("etapa")
    cambio_etapa = "etapa" in cambios and etapa_nueva != actual.get("etapa")
    if cambio_etapa:
        sets.append("etapa_cambiada_en = now()")
        texto = f"Etapa: {ETAPAS_CRM_NOMBRE.get(actual.get('etapa'), actual.get('etapa'))} → {ETAPAS_CRM_NOMBRE[etapa_nueva]}"
        if motivo_auto:
            texto += f" ({motivo_auto})"
        if etapa_nueva == "descartado" and cambios.get("motivo_descarte"):
            texto += f" — motivo: {cambios['motivo_descarte']}"
        _registrar_actividad(cur, clave, usuario, "sistema", texto)
    if "asignado" in cambios and cambios["asignado"] != actual.get("asignado"):
        texto = f"Asignado a {cambios['asignado']}" if cambios["asignado"] else "Quedó sin asignar"
        _registrar_actividad(cur, clave, usuario, "sistema", texto)

    if sets:
        sets.extend(["modificado_en = now()", "modificado_por = %s"])
        valores.append(usuario)
        cur.execute(
            f"UPDATE crm_leads SET {', '.join(sets)} WHERE clave = %s RETURNING *",
            valores + [clave],
        )
        actual = cur.fetchone()
        if not isinstance(actual, dict):
            columnas = [d[0] for d in cur.description]
            actual = dict(zip(columnas, actual))

    if cambio_etapa:
        _sincronizar_contactado(cur, clave, etapa_nueva)
    return actual


@app.get("/api/crm/tablero")
def crm_tablero(
    usuario: str = Depends(verificar_login),
    asignado: str = "",
    q: str = "",
    con_email: bool = False,
    con_oposicion: bool = False,
    pre_boletin: bool = False,
    limite: int = Query(LIMITE_TARJETAS_DEFAULT, ge=10, le=2000),
):
    """Tarjetas del tablero: todas las de las etapas activas (hasta 500 por
    columna) y las primeras `limite` de "nuevo" y "descartado", que pueden
    ser miles. Devuelve también el total real de cada columna."""
    where_sql, valores = _condiciones_leads(asignado, usuario, q, con_email, con_oposicion, pre_boletin)
    sql = f"""
        {_sql_leads()},
        filtrados AS (SELECT * FROM leads {where_sql}),
        numerados AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY etapa ORDER BY {ORDEN_TARJETAS_SQL}) AS rn,
                   COUNT(*) OVER (PARTITION BY etapa) AS total_etapa
            FROM filtrados
        )
        SELECT * FROM numerados
        WHERE rn <= CASE WHEN etapa IN ('nuevo', 'descartado') THEN %s ELSE 500 END
        ORDER BY etapa, rn
    """
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _asegurar_claves(cur)
            conn.commit()
            cur.execute(sql, valores + [limite])
            filas = cur.fetchall()

    totales = {e: 0 for e in ETAPAS_CRM}
    honorarios = {}
    for f in filas:
        totales[f["etapa"]] = f["total_etapa"]
        if f["etapa"] == "cliente" and f["monto"] is not None:
            moneda = f["moneda"] or "ARS"
            honorarios[moneda] = honorarios.get(moneda, 0) + float(f["monto"])
    return {
        "etapas": [{"id": e, "nombre": ETAPAS_CRM_NOMBRE[e], "total": totales[e]} for e in ETAPAS_CRM],
        "leads": filas,
        "honorarios_clientes": honorarios,
        "limite": limite,
    }


COLUMNAS_ORDEN_CRM = {
    "titular": "titular", "etapa": "array_position(ARRAY['nuevo','contactado','respondio','reunion','cliente','descartado'], etapa)",
    "asignado": "asignado", "proximo_seguimiento": "proximo_seguimiento",
    "ultima_actividad": "ultima_actividad", "detectado_en": "detectado_en",
    "lead_score": "lead_score", "cant_marcas": "cant_marcas",
}


@app.get("/api/crm/lista")
def crm_lista(
    usuario: str = Depends(verificar_login),
    etapa: str = "",
    asignado: str = "",
    q: str = "",
    con_email: bool = False,
    con_oposicion: bool = False,
    pre_boletin: bool = False,
    sort: str = "ultima_actividad",
    order: str = "desc",
    limit: int = Query(100, le=500),
    offset: int = 0,
):
    where_sql, valores = _condiciones_leads(asignado, usuario, q, con_email, con_oposicion, pre_boletin, etapa or None)
    orden = COLUMNAS_ORDEN_CRM.get(sort, "ultima_actividad")
    order_sql = "ASC" if order.lower() == "asc" else "DESC"
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _asegurar_claves(cur)
            conn.commit()
            cur.execute(
                f"""
                {_sql_leads()},
                filtrados AS (SELECT * FROM leads {where_sql})
                SELECT *, COUNT(*) OVER () AS total FROM filtrados
                ORDER BY {orden} {order_sql} NULLS LAST, detectado_en DESC NULLS LAST, clave
                LIMIT %s OFFSET %s
                """,
                valores + [limit, offset],
            )
            filas = cur.fetchall()
    total = filas[0]["total"] if filas else 0
    return {"total": total, "rows": filas}


@app.get("/api/crm/agenda")
def crm_agenda(usuario: str = Depends(verificar_login), asignado: str = ""):
    """Lo que hay que hacer: seguimientos agendados (vencidos, hoy y
    próximos), leads nuevos con oposición todavía sin gestionar, y plazos
    orientativos (oposición, DJ de uso, renovación) de los leads que se
    están trabajando o ya son clientes."""
    where_asig, valores_asig = _condiciones_leads(asignado, usuario, "", False, False, False)
    extra = f" AND {where_asig[6:]}" if where_asig else ""
    hoy = _hoy_ar()
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _asegurar_claves(cur)
            conn.commit()
            # Una sola pasada por `leads` (agregar toda la base es lo caro) y
            # después se reparte en Python.
            cur.execute(
                f"""{_sql_leads()}
                SELECT * FROM leads
                WHERE ((proximo_seguimiento IS NOT NULL AND etapa <> 'descartado')
                       OR (etapa = 'nuevo' AND oposicion_sin_apoderado)
                       OR etapa = ANY(%s)){extra}""",
                [ETAPAS_CRM_ACTIVAS] + valores_asig,
            )
            filas = cur.fetchall()
            seguimientos = sorted(
                (f for f in filas if f["proximo_seguimiento"] and f["etapa"] != "descartado"),
                key=lambda f: (f["proximo_seguimiento"], f["titular"] or ""),
            )[:300]
            oposiciones = sorted(
                (f for f in filas if f["etapa"] == "nuevo" and f["oposicion_sin_apoderado"]),
                key=lambda f: (f["oposicion_detectada_en"] is None, -(f["oposicion_detectada_en"] or f["detectado_en"]).timestamp()),
            )[:100]
            activos = {f["clave"]: f for f in filas if f["etapa"] in ETAPAS_CRM_ACTIVAS}
            filas_marcas = []
            if activos:
                actas = [a for f in activos.values() for a in f["actas"]]
                cur.execute(
                    f"WITH {_cte_marcas_con_clave(filtrar_actas=True)} SELECT {COLUMNAS_MARCA}, clave FROM mk",
                    (actas,),
                )
                filas_marcas = cur.fetchall()

    plazos = []
    for p_marca in filas_marcas:
        for p in _plazos_de_marcas([p_marca], hoy):
            if p["tipo"] == "oposicion_recibida":
                continue  # ya se ve en la tarjeta del lead
            if -7 <= p["dias"] <= 60:
                lead = activos[p_marca["clave"]]
                p.update({"clave": lead["clave"], "titular": lead["titular"], "etapa": lead["etapa"],
                          "asignado": lead["asignado"]})
                plazos.append(p)
    plazos.sort(key=lambda p: p["fecha"])

    for s in seguimientos:
        s["dias"] = (s["proximo_seguimiento"] - hoy).days
        s["urgencia"] = _urgencia(s["dias"])
    return {"hoy": hoy, "seguimientos": seguimientos, "oposiciones_sin_gestionar": oposiciones, "plazos": plazos}


@app.get("/api/crm/lead")
def crm_ficha(clave: str = Query(...), usuario: str = Depends(verificar_login)):
    """Todo lo de un lead para la ficha: datos agregados + CRM, sus marcas,
    plazos orientativos e historial (gestiones del CRM + comentarios
    internos de sus actas, mezclados por fecha)."""
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _asegurar_claves(cur)
            conn.commit()
            real = _clave_existente(cur, clave)
            actas = _actas_de_clave(cur, real)
            cur.execute(
                f"{_sql_leads(con_filtro_lead=False, filtrar_actas=True)} SELECT * FROM leads WHERE clave = %s",
                (actas, real),
            )
            lead = cur.fetchone()
            cur.execute(
                f"SELECT {COLUMNAS_MARCA} FROM marcas WHERE acta = ANY(%s) "
                f"ORDER BY fecha_presentacion DESC NULLS LAST, acta DESC",
                (actas,),
            )
            marcas = cur.fetchall()
            cur.execute(
                "SELECT id, autor, tipo, texto, creado_en FROM crm_actividad WHERE clave = %s ORDER BY creado_en DESC",
                (real,),
            )
            actividad = cur.fetchall()
            cur.execute(
                "SELECT id, autor, destinatario, acta, texto, creado_en, resuelto FROM comentarios "
                "WHERE acta = ANY(%s) ORDER BY creado_en DESC",
                ([m["acta"] for m in marcas],),
            )
            comentarios = cur.fetchall()
            try:
                cliente = cartera_api.cliente_por_clave(cur, real)
            except psycopg2.Error:
                conn.rollback()
                cliente = None

    plazos = _plazos_de_marcas(marcas)
    if lead and lead.get("proximo_seguimiento"):
        dias = (lead["proximo_seguimiento"] - _hoy_ar()).days
        plazos.insert(0, {
            "acta": None, "marca": None, "clase": None, "tipo": "seguimiento",
            "titulo": "Próximo seguimiento", "detalle": "", "fecha": lead["proximo_seguimiento"],
            "dias": dias, "urgencia": _urgencia(dias),
        })
    return {
        "clave": real,
        "lead": lead,
        "marcas": marcas,
        "plazos": plazos,
        "actividad": actividad,
        "comentarios": comentarios,
        "cliente": cliente,
        "etapas": [{"id": e, "nombre": ETAPAS_CRM_NOMBRE[e]} for e in ETAPAS_CRM],
        "tipos_actividad": [{"id": k, "nombre": v} for k, v in TIPOS_ACTIVIDAD.items()],
        "usuarios": _auth.usuarios_activos(),
        "usuario": usuario,
    }


@app.post("/api/crm/lead")
def crm_actualizar(body: CrmActualizacion, clave: str = Query(...), usuario: str = Depends(verificar_login)):
    """Actualiza solo los campos que vienen en el body (un campo en null lo
    borra; un campo ausente no se toca)."""
    enviados = body.model_fields_set
    cambios = {}
    if "etapa" in enviados:
        if body.etapa not in ETAPAS_CRM:
            raise HTTPException(status_code=400, detail=f"Etapa desconocida: {body.etapa}")
        cambios["etapa"] = body.etapa
    if "asignado" in enviados:
        asignado = (body.asignado or "").strip() or None
        if asignado and not _auth.usuario_existe(asignado):
            raise HTTPException(status_code=400, detail=f"No existe el usuario {asignado}")
        cambios["asignado"] = asignado
    if "telefono" in enviados:
        tel = (body.telefono or "").strip() or None
        if tel and len(tel) > 40:
            raise HTTPException(status_code=400, detail="Teléfono demasiado largo")
        cambios["telefono"] = tel
    if "proximo_seguimiento" in enviados:
        cambios["proximo_seguimiento"] = _validar_fecha(body.proximo_seguimiento)
    for campo, largo in (("motivo_descarte", 500), ("servicio", 300)):
        if campo in enviados:
            valor = (getattr(body, campo) or "").strip() or None
            if valor and len(valor) > largo:
                raise HTTPException(status_code=400, detail=f"El campo {campo} es demasiado largo")
            cambios[campo] = valor
    if "monto" in enviados:
        if body.monto is not None and body.monto < 0:
            raise HTTPException(status_code=400, detail="Los honorarios no pueden ser negativos")
        cambios["monto"] = round(body.monto, 2) if body.monto is not None else None
    if "moneda" in enviados:
        moneda = (body.moneda or "ARS").upper()
        if moneda not in MONEDAS_CRM:
            raise HTTPException(status_code=400, detail=f"Moneda no soportada: {moneda}")
        cambios["moneda"] = moneda
    if not cambios:
        raise HTTPException(status_code=400, detail="No hay cambios para guardar")

    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _asegurar_claves(cur)
            real = _clave_existente(cur, clave)
            fila = _aplicar_cambios_crm(cur, real, cambios, usuario)
        conn.commit()
    return {"ok": True, "clave": real, "crm": fila}


CARACTER_GESTOR_MANUAL = "Gestor/apoderado (marcado a mano)"


class ConGestor(BaseModel):
    con_gestor: bool = True
    nombre: Optional[str] = None


def _aplicar_con_gestor(cur, real: str, usuario: str, con_gestor: bool, nombre: Optional[str]) -> int:
    """Marca (o desmarca) que el titular `real` tiene gestor/apoderado. Devuelve
    cuántas marcas cambiaron; levanta HTTPException 400 si no había nada para
    cambiar. El que llama hace commit."""
    actas = _actas_de_clave(cur, real)
    if con_gestor:
        cur.execute(
            """
            UPDATE marcas
            SET caracter_previo = caracter, lead_score_previo = lead_score,
                con_gestor_manual = true, con_gestor_manual_en = now(),
                con_gestor_manual_por = %s, gestor_nombre = %s,
                es_lead = false, caracter = %s,
                lead_score = (CASE WHEN TRIM(COALESCE(matricula_agente, '')) IN ('', 'Part.') THEN 50 ELSE 0 END)
                             - 100 + (CASE WHEN COALESCE(email, '') <> '' THEN 20 ELSE 0 END),
                actualizado_en = now()
            WHERE acta = ANY(%s) AND es_lead IS TRUE
            """,
            (usuario, nombre, CARACTER_GESTOR_MANUAL, actas),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=400, detail="Este titular no tiene marcas lead para marcar")
        texto = "Marcado a mano: tiene gestor/apoderado" + (f" ({nombre})" if nombre else "") \
            + ". Deja de ser lead y pasa a «con agente»."
    else:
        cur.execute(
            """
            UPDATE marcas
            SET es_lead = true, caracter = caracter_previo,
                lead_score = COALESCE(lead_score_previo, lead_score),
                caracter_previo = NULL, lead_score_previo = NULL,
                con_gestor_manual = NULL, con_gestor_manual_en = NULL,
                con_gestor_manual_por = NULL, gestor_nombre = NULL,
                actualizado_en = now()
            WHERE acta = ANY(%s) AND con_gestor_manual IS TRUE
            """,
            (actas,),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=400, detail="Este titular no estaba marcado con gestor")
        texto = "Se deshizo «tiene gestor/apoderado»: vuelve a ser lead."
    marcas = cur.rowcount
    _registrar_actividad(cur, real, usuario, "sistema", texto)
    return marcas


@app.post("/api/crm/con-gestor")
def crm_con_gestor(body: ConGestor, clave: str = Query(...), usuario: str = Depends(verificar_login)):
    """Marca (o desmarca) a mano que el titular ya tiene gestor/apoderado.
    Mismo efecto que cuando el sistema detecta que se sumó uno
    (revisar_oposiciones.py): sus marcas lead pasan a es_lead = false, salen
    de la lista de leads y de todas las revisiones, y cargar_db.py no las
    vuelve a convertir en lead al reimportar el boletín. Se guarda el carácter
    y el score anteriores para poder deshacerlo. No toca la etapa del CRM."""
    nombre = (body.nombre or "").strip() or None
    if nombre and len(nombre) > 200:
        raise HTTPException(status_code=400, detail="El nombre del gestor es demasiado largo (máx. 200 caracteres)")
    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _asegurar_claves(cur)
            real = _clave_existente(cur, clave)
            marcas = _aplicar_con_gestor(cur, real, usuario, body.con_gestor, nombre)
        conn.commit()
    return {"ok": True, "clave": real, "marcas": marcas}


@app.post("/api/crm/actividad")
def crm_agregar_actividad(body: CrmActividadNueva, clave: str = Query(...), usuario: str = Depends(verificar_login)):
    tipo = body.tipo if body.tipo in TIPOS_ACTIVIDAD else None
    if not tipo:
        raise HTTPException(status_code=400, detail=f"Tipo de gestión desconocido: {body.tipo}")
    texto = (body.texto or "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Escribí qué se hizo")
    if len(texto) > 5000:
        raise HTTPException(status_code=400, detail="El texto es demasiado largo (máx. 5000 caracteres)")
    seguimiento = _validar_fecha(body.proximo_seguimiento)

    with conexion() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _asegurar_claves(cur)
            real = _clave_existente(cur, clave)
            _registrar_actividad(cur, real, usuario, tipo, texto)
            cambios = {}
            if seguimiento:
                cambios["proximo_seguimiento"] = seguimiento
            motivo = ""
            if tipo in TIPOS_ACTIVIDAD_CONTACTO:
                cur.execute("SELECT etapa FROM crm_leads WHERE clave = %s", (real,))
                fila = cur.fetchone()
                if not fila or fila["etapa"] == "nuevo":
                    cambios["etapa"] = "contactado"
                    motivo = f"automático al registrar: {TIPOS_ACTIVIDAD[tipo]}"
            if cambios:
                _aplicar_cambios_crm(cur, real, cambios, usuario, motivo_auto=motivo)
        conn.commit()
    return {"ok": True, "clave": real}


@app.delete("/api/crm/actividad/{actividad_id}")
def crm_borrar_actividad(actividad_id: int, usuario: str = Depends(verificar_login)):
    """Solo quien la escribió puede borrarla; los registros automáticos
    (cambios de etapa/asignación) no se borran."""
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT autor, tipo FROM crm_actividad WHERE id = %s", (actividad_id,))
            fila = cur.fetchone()
            if not fila:
                raise HTTPException(status_code=404, detail="No existe esa gestión")
            if fila[1] == "sistema":
                raise HTTPException(status_code=403, detail="Los registros automáticos no se borran")
            if fila[0] != usuario:
                raise HTTPException(status_code=403, detail="Solo quien la registró la puede borrar")
            cur.execute("DELETE FROM crm_actividad WHERE id = %s", (actividad_id,))
        conn.commit()
    return {"ok": True}


class CrmClaves(BaseModel):
    claves: list[str]


@app.post("/api/crm/etapas")
def crm_etapas(body: CrmClaves, _: str = Depends(verificar_login)):
    """Etapa y asignado de varios titulares a la vez (para el badge de cada
    fila del panel principal). Recibe las claves tal como las arma el
    frontend (claveTitular: CUIT o nombre) y responde con esas mismas
    claves, aunque internamente el nombre se resuelva a un CUIT."""
    claves = [c for c in dict.fromkeys((c or "").strip() for c in body.claves) if c][:1000]
    if not claves:
        return {}
    with conexion() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH entrada AS (
                    SELECT original, upper(regexp_replace(trim(original), '\\s+', ' ', 'g')) AS nom
                    FROM unnest(%s::text[]) AS original
                ),
                mapa AS (
                    SELECT nom, min(clave) AS clave FROM crm_claves
                    WHERE nom IN (SELECT nom FROM entrada)
                    GROUP BY nom HAVING COUNT(DISTINCT clave) = 1
                )
                SELECT e.original, c.etapa, c.asignado, c.proximo_seguimiento
                FROM entrada e
                LEFT JOIN mapa ON mapa.nom = e.nom
                JOIN crm_leads c ON c.clave = COALESCE(mapa.clave, e.original)
                """,
                (claves,),
            )
            return {
                original: {"etapa": etapa, "nombre": ETAPAS_CRM_NOMBRE.get(etapa, etapa),
                           "asignado": asignado, "proximo_seguimiento": seguimiento}
                for original, etapa, asignado, seguimiento in cur.fetchall()
            }


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
def index(_: str = Depends(verificar_pagina)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/titular/{clave}")
def pagina_titular(clave: str, _: str = Depends(verificar_pagina)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "titular.html"))


@app.get("/ayuda")
def pagina_ayuda(_: str = Depends(verificar_pagina)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "ayuda.html"))


@app.get("/crons")
def pagina_crons(_: str = Depends(verificar_pagina)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "crons.html"))


@app.get("/boletines")
def pagina_boletines(_: str = Depends(verificar_pagina)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "boletines.html"))


@app.get("/comentarios")
def pagina_comentarios(_: str = Depends(verificar_pagina)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "comentarios.html"))


@app.get("/clientes")
def pagina_clientes(_: str = Depends(verificar_pagina)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "clientes.html"))


@app.get("/mails")
def pagina_mails(_: str = Depends(verificar_pagina)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "mails.html"))


@app.get("/crm")
def pagina_crm(_: str = Depends(verificar_pagina)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "crm.html"))


@app.get("/calendario")
def pagina_calendario(_: str = Depends(verificar_pagina)):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "calendario.html"))


@app.get("/tareas")
def pagina_tareas(_: str = Depends(verificar_pagina)):
    """Tablero de tareas: lleva a Kan (alternativa open source a Trello, autohospedada en Railway)."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(os.environ.get("KAN_URL") or KAN_URL_DEFECTO)


@app.get("/disponibilidad")
def pagina_disponibilidad(_: str = Depends(verificar_pagina)):
    """La pantalla Calendario abierta directo en la vista Disponibilidad (acceso rápido ⚡ del menú)."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "calendario.html"))


@app.get("/agendar-llamada")
def pagina_agendar_llamada(_: str = Depends(verificar_pagina)):
    """Solo el formulario para agendar (para dejarlo como acceso rápido)."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "agendar.html"))


# Sección Clientes (cartera + vigilancia): el router recibe el login, la
# conexión y los helpers del CRM que necesita (para no importar app.py).
from types import SimpleNamespace  # noqa: E402

app.include_router(cartera_api.crear_router(
    verificar_login, conexion,
    SimpleNamespace(
        asegurar_claves=_asegurar_claves, clave_existente=_clave_existente, actas_de_clave=_actas_de_clave,
        aplicar_cambios_crm=_aplicar_cambios_crm, usuarios=_auth.usuarios_activos,
    ),
))

# Formularios para clientes (persona física / jurídica): páginas públicas
# /formulario/... (sin login) + gestión de respuestas en Clientes → Formularios.
app.include_router(formularios_api.crear_router(verificar_login, conexion))
app.include_router(accesos_api.crear_router(verificar_login, conexion, CLAVES_ACCESO_RAPIDO, _contar_consulta))

# Pestaña "Mails": cuenta de Resend, remitente, reply-to y plantillas de cada mail.
def _crm_registrar_mail(cur, acta: str, usuario: str, texto: str):
    """Deja un mail mandado desde el panel como gestión «Mail» del titular en
    el CRM y marca el acta como contactada; si el lead estaba en «nuevo», pasa
    a «contactado» (mismo criterio que registrar una gestión a mano)."""
    cur.execute("UPDATE marcas SET contactado = true, contactado_en = COALESCE(contactado_en, now()) WHERE acta = %s", (acta,))
    _asegurar_claves(cur)
    cur.execute(f"WITH {_cte_marcas_con_clave(filtrar_actas=True)} SELECT clave FROM mk", ([acta],))
    fila = cur.fetchone()
    clave = fila and (fila["clave"] if isinstance(fila, dict) else fila[0])
    if not clave:
        return
    _registrar_actividad(cur, clave, usuario, "email", texto)
    cur.execute("SELECT etapa FROM crm_leads WHERE clave = %s", (clave,))
    f = cur.fetchone()
    etapa = (f["etapa"] if isinstance(f, dict) else f[0]) if f else "nuevo"
    if etapa == "nuevo":
        _aplicar_cambios_crm(cur, clave, {"etapa": "contactado"}, usuario, motivo_auto="automático al mandar un mail desde el panel")


app.include_router(mails_api.crear_router(verificar_login, _auth.verificar_admin, conexion, _crm_registrar_mail))

# Pestaña «Análisis de marca» de la ficha del titular: texto, oposiciones y PDF con membrete.
app.include_router(analisis_api.crear_router(verificar_login, conexion))

# Sección Calendario: eventos sincronizados con Google Calendar, vinculados por número de acta.
app.include_router(calendario_api.crear_router(verificar_login, conexion, _auth.verificar_admin))


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
