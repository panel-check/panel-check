-- Esquema para Postgres (Railway). Pensado para que NocoDB se conecte
-- directo sobre estas tablas.

CREATE TABLE IF NOT EXISTS boletines (
    numero          TEXT PRIMARY KEY,
    fecha           DATE,
    pdf_url         TEXT,
    procesado_en    TIMESTAMPTZ DEFAULT now(),
    total_marcas    INTEGER,
    estado          TEXT DEFAULT 'pendiente'  -- pendiente | procesado | error
);

CREATE TABLE IF NOT EXISTS marcas (
    acta                TEXT PRIMARY KEY,
    boletin             TEXT REFERENCES boletines(numero),
    clase               INTEGER,
    tipo                TEXT,      -- D | M | F | T
    denominacion        TEXT,      -- la que trae el boletín (vacía en M/F)
    denominacion_inpi   TEXT,      -- completada vía webservice para M/F
    fecha_presentacion  TIMESTAMPTZ,
    titular             TEXT,
    pais                TEXT,
    cuit                TEXT,
    matricula_agente    TEXT,      -- "" (sin dato) | "Part." | número
    caracter            TEXT,      -- vacío | "Apoderado" | "Gestor Ratificado" (ver Paso 4 del skill)
    es_lead             BOOLEAN,   -- true solo si caracter está vacío (solicitante sin ayuda)
    email               TEXT,      -- obtenido del Formulario vía Grilla Digital (Paso 5)
    email_apoderado     TEXT,
    lead_score          INTEGER DEFAULT 0,
    link                TEXT,
    revisado_manual     BOOLEAN DEFAULT false,  -- para las Mixtas/Figurativas sin match de nombre
    creado_en           TIMESTAMPTZ DEFAULT now(),
    actualizado_en      TIMESTAMPTZ DEFAULT now()
);

-- Agregado para el panel propio (marcar leads como gestionados).
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS contactado BOOLEAN DEFAULT false;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS contactado_en TIMESTAMPTZ;

-- Agregado para explicar por qué no se pudo recuperar el email de un lead
-- (se completa en validar_leads.py y también se puede reintentar a mano
-- desde el panel).
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS motivo_sin_email TEXT;

-- Contador de reintentos (reintentar_sin_email.py / reintentar_sin_verificar.py):
-- cada intento fallido duplica la espera hasta el próximo (1, 2, 4... máx. 48 h).
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS reintentos_n INTEGER DEFAULT 0;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS reintento_ultimo_en TIMESTAMPTZ;

-- Agregado para el seguimiento de oposiciones/vistas post-publicación
-- (ver scripts/revisar_oposiciones.py). fecha_publicacion viene de la fila
-- "Hoja Publicacion" de Grilla Digital — es la fecha real de publicación
-- de ESE expediente (más confiable que boletines.fecha, que es una sola
-- fecha por boletín y puede quedar en null si se forzó el boletín a mano).
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS fecha_publicacion DATE;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS tuvo_oposicion BOOLEAN;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS detalle_oposicion TEXT;
ALTER TABLE boletines ADD COLUMN IF NOT EXISTS oposiciones_buscadas_en TIMESTAMPTZ;  -- última búsqueda manual completa (botón de /boletines)
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS revisado_oposicion_en TIMESTAMPTZ;
-- Esquema de revisión a los 10/23/33 días + rechequeos (revisar_oposiciones.py)
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS opo_chequeos INTEGER;            -- hitos cumplidos (0-3)
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS opo_ultimo_chequeo_en TIMESTAMPTZ;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_detectada_en TIMESTAMPTZ; -- primera vez que se vio
-- Oposición marcada a mano como "ya atendida" (botón en la ficha del lead)
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_atendida BOOLEAN;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_atendida_en TIMESTAMPTZ;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_atendida_por TEXT;

-- Lead marcado a mano como "tiene gestor/apoderado" (botón en la ficha del lead).
-- Pasa a es_lead = false (sale de los leads) y se guardan el carácter y el score
-- anteriores para poder deshacerlo. cargar_db.py no lo revierte al reimportar.
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS con_gestor_manual BOOLEAN;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS con_gestor_manual_en TIMESTAMPTZ;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS con_gestor_manual_por TEXT;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS gestor_nombre TEXT;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS caracter_previo TEXT;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS lead_score_previo INTEGER;

-- Agregado para el seguimiento del ciclo de vida de la marca ya concedida
-- (ver scripts/revisar_estado.py): permite ofrecer vigilancia marcaria,
-- avisar de la declaración jurada de uso (a los 5 años) y de la renovación
-- (a los 10 años). estado_tramite/fecha_concesion/numero_disposicion vienen
-- de la sección RESOLUCIÓN del expediente ("TIPO:" y "DISPOSICION:");
-- fecha_vencimiento_marca viene directo de "VENCE:" (INPI ya calcula
-- concesión + 10 años, no hace falta calcularlo nosotros).
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS estado_tramite TEXT;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS fecha_concesion DATE;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS numero_disposicion TEXT;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS fecha_vencimiento_marca DATE;

-- Agregado el 29/09/2026: si después de detectada la oposición aparece en
-- Grilla Digital una presentación de "Acompaña Poder" o "Ratifica" (alguien
-- se sumó como apoderado/gestor para responder), señal fuerte de que el
-- titular ya no es un lead frío -- ver revisar_oposiciones.py.
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS representacion_posterior_oposicion BOOLEAN;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS detalle_representacion_posterior TEXT;

-- Agregado el 05/10/2026: estado de la oposición leído del expediente de INPI
-- (tablas OPOSICIONES y VISTAS) -- ver scripts/oposiciones_expediente.py.
--   estado_oposicion: sin_notificar | notificada_en_plazo | plazo_vencido |
--     vista_pendiente | oposicion_sin_detalle (el lead SIRVE: nadie la trabaja) |
--     contestada | levantada | con_apoderado (ya no sirve).
-- oposicion_agente_oponente es el apoderado del OPONENTE, no del titular.
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS estado_oposicion TEXT;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS estado_oposicion_detalle TEXT;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS estado_oposicion_en TIMESTAMPTZ;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_sirve BOOLEAN;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_fecha_presentacion DATE;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_fecha_notificacion DATE;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_fecha_vencimiento DATE;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_fecha_levantamiento DATE;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_agente_oponente TEXT;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS oposicion_posible_apoderado BOOLEAN;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS estado_oposicion_version INTEGER; -- 2 = reglas actuales

-- Agregado el 30/09/2026: aviso por mail (Resend) de leads con oposición/vista
-- nueva -- ver scripts/notificar_oposiciones.py. Cada lead se avisa una sola vez.
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS notificado_oposicion_en TIMESTAMPTZ;

-- Agregado el 30/09/2026: escaneo directo de números de acta secuenciales
-- (scripts/escanear_actas_nuevas.py). El acta se asigna al depositar la
-- solicitud -- semanas antes de que INPI lo publique en un boletín -- así
-- que se puede detectar (y contactar) un lead el mismo día que se registra,
-- sin esperar el boletín. "fuente" distingue de dónde salió el registro
-- originalmente; "boletin" queda NULL hasta que el boletín real lo alcanza
-- (en ese momento cargar_db.py lo completa, ver ON CONFLICT más abajo).
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS fuente TEXT DEFAULT 'boletin'; -- 'boletin' | 'escaneo_directo'

-- Puntero de hasta qué número de acta se escaneó -- una sola fila (id=1).
CREATE TABLE IF NOT EXISTS escaneo_actas (
    id                      SMALLINT PRIMARY KEY DEFAULT 1,
    ultima_acta_confirmada  BIGINT NOT NULL,
    actualizado_en          TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT escaneo_actas_una_fila CHECK (id = 1)
);

CREATE INDEX IF NOT EXISTS idx_marcas_fuente ON marcas(fuente);
CREATE INDEX IF NOT EXISTS idx_marcas_boletin ON marcas(boletin);
CREATE INDEX IF NOT EXISTS idx_marcas_es_lead ON marcas(es_lead);
CREATE INDEX IF NOT EXISTS idx_marcas_titular ON marcas(titular);
CREATE INDEX IF NOT EXISTS idx_marcas_contactado ON marcas(contactado);
CREATE INDEX IF NOT EXISTS idx_marcas_fecha_publicacion ON marcas(fecha_publicacion);
CREATE INDEX IF NOT EXISTS idx_marcas_estado_tramite ON marcas(estado_tramite);

-- Agregado el 30/09/2026: comentarios internos del equipo en el panel
-- (sección /comentarios y botón 💬 en cada fila). "acta" es opcional y NO es
-- FK a marcas (se puede comentar un acta que todavía no está cargada);
-- "destinatario" NULL = para todo el equipo. El panel también crea estas
-- tablas solo al arrancar (ver _crear_tablas_comentarios en panel/app.py).
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
);
CREATE INDEX IF NOT EXISTS idx_comentarios_acta ON comentarios(acta);
CREATE INDEX IF NOT EXISTS idx_comentarios_creado ON comentarios(creado_en DESC);

-- Hasta cuándo vio cada usuario la sección de comentarios (contador de nuevos).
CREATE TABLE IF NOT EXISTS comentarios_visto (
    usuario      TEXT PRIMARY KEY,
    visto_hasta  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Agregado el 30/09/2026: CRM de leads (sección /crm del panel). Seguimiento
-- comercial por TITULAR (clave = CUIT, o el nombre normalizado si todavía no
-- hay CUIT; ver _cte_marcas_con_clave en panel/app.py). Un titular sin fila
-- en crm_leads está en la etapa "nuevo". El panel también crea estas tablas
-- solo al arrancar (ver _crear_tablas_crm en panel/app.py).
CREATE TABLE IF NOT EXISTS crm_leads (
    clave                TEXT PRIMARY KEY,
    etapa                TEXT NOT NULL DEFAULT 'nuevo', -- nuevo | contactado | respondio | reunion | cliente | descartado
    asignado             TEXT,                          -- usuario del panel (PANEL_USERS)
    telefono             TEXT,
    proximo_seguimiento  DATE,
    motivo_descarte      TEXT,
    servicio             TEXT,                          -- si es cliente: qué contrató
    monto                NUMERIC(14, 2),                -- si es cliente: honorarios
    moneda               TEXT DEFAULT 'ARS',            -- ARS | USD
    etapa_cambiada_en    TIMESTAMPTZ,
    alta_en              TIMESTAMPTZ NOT NULL DEFAULT now(),
    modificado_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
    modificado_por       TEXT
);
CREATE INDEX IF NOT EXISTS idx_crm_leads_etapa ON crm_leads(etapa);

-- Historial de gestiones de cada lead (nota | llamada | email | whatsapp |
-- reunion) + registros automáticos ("sistema") de cambios de etapa/asignación.
CREATE TABLE IF NOT EXISTS crm_actividad (
    id         SERIAL PRIMARY KEY,
    clave      TEXT NOT NULL,
    autor      TEXT NOT NULL,
    tipo       TEXT NOT NULL,
    texto      TEXT NOT NULL,
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_crm_actividad_clave ON crm_actividad(clave, creado_en DESC);

-- Registro de corridas de los procesos automáticos (scripts/registro.py):
-- qué hizo cada corrida, para el reporte por día de Automatizaciones.
CREATE TABLE IF NOT EXISTS registro_corridas (
    id          BIGSERIAL PRIMARY KEY,
    proceso     TEXT NOT NULL,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metricas    JSONB NOT NULL DEFAULT '{}'::jsonb,
    run_id      TEXT,
    run_url     TEXT
);
CREATE INDEX IF NOT EXISTS idx_registro_corridas_proceso ON registro_corridas(proceso, creado_en DESC);

-- ── Clientes y vigilancia marcaria (sección /clientes) ─────────────────
-- Las crea también cartera.crear_tablas() (panel/cartera.py) al arrancar el panel y
-- en cada script: esta copia es solo documentación/bootstrap. Los datos iniciales
-- (clases relacionadas y palabras genéricas) los carga esa función, una sola vez.

CREATE TABLE IF NOT EXISTS clientes (
    id                     SERIAL PRIMARY KEY,
    nombre                 TEXT NOT NULL,
    cuit                   TEXT,
    email                  TEXT,
    telefono               TEXT,
    contacto               TEXT,
    notas                  TEXT,
    origen                 TEXT NOT NULL DEFAULT 'cartera',
    clave_crm              TEXT,
    convertido_en          TIMESTAMPTZ,
    vigilancia_contratada  BOOLEAN NOT NULL DEFAULT false,
    vigilancia_desde       DATE,
    activo                 BOOLEAN NOT NULL DEFAULT true,
    alta_en                TIMESTAMPTZ NOT NULL DEFAULT now(),
    alta_por               TEXT,
    modificado_en          TIMESTAMPTZ NOT NULL DEFAULT now(),
    modificado_por         TEXT
);

CREATE INDEX IF NOT EXISTS idx_clientes_cuit ON clientes(cuit);

CREATE UNIQUE INDEX IF NOT EXISTS idx_clientes_clave_crm ON clientes(clave_crm) WHERE clave_crm IS NOT NULL;

CREATE TABLE IF NOT EXISTS cartera_marcas (
    acta                     TEXT PRIMARY KEY,
    cliente_id               INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    denominacion             TEXT,
    tipo                     TEXT,
    clase                    INTEGER,
    titular                  TEXT,
    cuit                     TEXT,
    fecha_presentacion       DATE,
    fecha_publicacion        DATE,
    agente                   TEXT,
    matricula_agente         TEXT,
    caracter                 TEXT,
    estado_tramite           TEXT,
    fecha_concesion          DATE,
    numero_disposicion       TEXT,
    fecha_vencimiento_marca  DATE,
    tuvo_oposicion           BOOLEAN,
    detalle_oposicion        TEXT,
    movimientos              INTEGER,
    grilla_claves            JSONB,
    ultimo_movimiento        TEXT,
    ultimo_movimiento_fecha  DATE,
    vigilar                  BOOLEAN NOT NULL DEFAULT true,
    vigilar_todas_clases     BOOLEAN NOT NULL DEFAULT false,
    terminos_vigilancia      TEXT,
    vigilancia_firma         TEXT,
    notas                    TEXT,
    origen_carga             TEXT,
    consultado_en            TIMESTAMPTZ,
    error_consulta           TEXT,
    alta_en                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    alta_por                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_cartera_marcas_cliente ON cartera_marcas(cliente_id);

CREATE TABLE IF NOT EXISTS cartera_novedades (
    id             BIGSERIAL PRIMARY KEY,
    acta           TEXT NOT NULL,
    cliente_id     INTEGER,
    tipo           TEXT NOT NULL,
    texto          TEXT NOT NULL,
    detectado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    notificado_en  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cartera_novedades_acta ON cartera_novedades(acta, detectado_en DESC);

CREATE INDEX IF NOT EXISTS idx_cartera_novedades_cliente ON cartera_novedades(cliente_id, detectado_en DESC);

CREATE TABLE IF NOT EXISTS cartera_matriculas (
    matricula  TEXT PRIMARY KEY,
    nombre     TEXT,
    alta_en    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cartera_descartes (
    acta            TEXT PRIMARY KEY,
    descartado_por  TEXT,
    descartado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cartera_avisos_plazo (
    acta        TEXT NOT NULL,
    tipo        TEXT NOT NULL,
    fecha       DATE NOT NULL,
    umbral      INTEGER NOT NULL,
    avisado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (acta, tipo, fecha, umbral)
);

CREATE TABLE IF NOT EXISTS solicitudes_escaneadas (
    acta                TEXT PRIMARY KEY,
    denominacion        TEXT,
    tipo                TEXT,
    clase               INTEGER,
    titular             TEXT,
    cuit                TEXT,
    fecha_presentacion  DATE,
    agente              TEXT,
    matricula_agente    TEXT,
    caracter            TEXT,
    es_lead             BOOLEAN,
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_solicitudes_escaneadas_cuit ON solicitudes_escaneadas(cuit);

CREATE TABLE IF NOT EXISTS vigilancia_alertas (
    id                        BIGSERIAL PRIMARY KEY,
    tipo                      TEXT NOT NULL DEFAULT 'similitud',
    acta_cliente              TEXT,
    cliente_id                INTEGER,
    acta_nueva                TEXT NOT NULL,
    denominacion_nueva        TEXT,
    clase_nueva               INTEGER,
    tipo_nuevo                TEXT,
    titular_nuevo             TEXT,
    cuit_nuevo                TEXT,
    agente_nuevo              TEXT,
    fecha_presentacion_nueva  DATE,
    fuente_nueva              TEXT,
    puntaje                   INTEGER,
    nivel                     TEXT,
    motivos                   TEXT,
    estado                    TEXT NOT NULL DEFAULT 'nueva',
    nota                      TEXT,
    decidido_por              TEXT,
    decidido_en               TIMESTAMPTZ,
    creada_en                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    notificada_en             TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_vigilancia_alertas_unica ON vigilancia_alertas(tipo, COALESCE(acta_cliente, ''), acta_nueva);

CREATE INDEX IF NOT EXISTS idx_vigilancia_alertas_estado ON vigilancia_alertas(estado, creada_en DESC);

CREATE INDEX IF NOT EXISTS idx_vigilancia_alertas_cliente ON vigilancia_alertas(cliente_id);

CREATE TABLE IF NOT EXISTS vigilancia_comparadas (
    acta          TEXT PRIMARY KEY,
    firma         TEXT,
    comparada_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vigilancia_clases_relacionadas (
    clase_a  INTEGER NOT NULL,
    clase_b  INTEGER NOT NULL,
    PRIMARY KEY (clase_a, clase_b),
    CONSTRAINT vigilancia_clases_orden CHECK (clase_a < clase_b)
);

CREATE TABLE IF NOT EXISTS vigilancia_palabras_genericas (palabra TEXT PRIMARY KEY);

CREATE TABLE IF NOT EXISTS vigilancia_config (clave TEXT PRIMARY KEY, valor TEXT);

-- ── Formularios para clientes (Clientes → Formularios) ──────────────────
-- Las crea el panel al arrancar (panel/formularios_core.py: crear_tablas).
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS tipo_persona TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS dni TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS nacionalidad TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS domicilio TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS localidad TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS provincia TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS codigo_postal TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS domicilio_comercial TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS estado_civil TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS conyuge_nombre TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS conyuge_dni TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS firmante_nombre TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS firmante_cargo TEXT;

-- Referido: quién refirió al cliente (agencia de marketing, diseñador, etc.), se muestra como tag
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS referido TEXT;

CREATE TABLE IF NOT EXISTS formularios_respuestas (
    id                SERIAL PRIMARY KEY,
    formulario        TEXT NOT NULL,           -- persona-fisica | persona-juridica
    tipo_persona      TEXT NOT NULL,           -- fisica | juridica
    datos             JSONB NOT NULL DEFAULT '{}'::jsonb,
    cliente_id        INTEGER REFERENCES clientes(id) ON DELETE SET NULL,
    cliente_nuevo     BOOLEAN NOT NULL DEFAULT false,
    estado            TEXT NOT NULL DEFAULT 'nueva',   -- nueva | revisada
    recibido_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip                TEXT,
    revisado_por      TEXT,
    revisado_en       TIMESTAMPTZ,
    aviso_enviado_en  TIMESTAMPTZ,
    aviso_error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_form_resp_cliente ON formularios_respuestas(cliente_id);
CREATE INDEX IF NOT EXISTS idx_form_resp_recibido ON formularios_respuestas(recibido_en DESC);

CREATE TABLE IF NOT EXISTS formularios_archivos (
    id            SERIAL PRIMARY KEY,
    respuesta_id  INTEGER NOT NULL REFERENCES formularios_respuestas(id) ON DELETE CASCADE,
    campo         TEXT NOT NULL,
    nombre        TEXT NOT NULL,
    mime          TEXT NOT NULL,
    tamano        INTEGER NOT NULL,
    contenido     BYTEA NOT NULL,
    subido_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_form_arch_resp ON formularios_archivos(respuesta_id);

-- Poderes generados desde la ficha del cliente (panel/poderes.py)
CREATE TABLE IF NOT EXISTS poderes_generados (
    id            SERIAL PRIMARY KEY,
    cliente_id    INTEGER REFERENCES clientes(id) ON DELETE CASCADE,
    datos         JSONB NOT NULL,
    apoderado     TEXT NOT NULL,
    generado_por  TEXT,
    generado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_poderes_cliente ON poderes_generados(cliente_id, generado_en DESC);

-- ── Configuración de mails (pestaña Mails del panel) ────────────────────
-- Las crea el panel al arrancar (panel/mails_core.py: crear_tablas). Las API
-- keys NO se guardan acá: viven en variables de entorno.
CREATE TABLE IF NOT EXISTS mails_config (
    clave           TEXT PRIMARY KEY,
    cuenta          TEXT,
    remitente       TEXT,
    responder_a     TEXT,
    destinatarios   TEXT,
    asunto          TEXT,
    cuerpo          TEXT,
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_por TEXT
);
CREATE TABLE IF NOT EXISTS mails_bajas (
    email      TEXT PRIMARY KEY,
    motivo     TEXT,
    creada_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    creada_por TEXT
);

-- Agregado el 07/10/2026: pestaña «Análisis de marca» de la ficha del titular.
-- Una fila por acta: el texto que escribe el equipo y la última consulta a INPI
-- de las oposiciones del expediente (JSON: consultado_en + items). El panel
-- también la crea solo al arrancar (ver panel/analisis_marca.py: crear_tablas).
CREATE TABLE IF NOT EXISTS analisis_marca (
    acta             TEXT PRIMARY KEY,
    texto            TEXT NOT NULL DEFAULT '',
    oposiciones      JSONB,
    actualizado_por  TEXT,
    actualizado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Logo de la marca tomado del expediente de INPI (reducido y sin metadatos);
    -- logo_consultado_en vacío = todavía no se miró el expediente.
    logo             BYTEA,
    logo_mime        TEXT,
    logo_consultado_en TIMESTAMPTZ,
    -- Denominación, tipo de marca, limitación y publicación tal como figuran en el expediente.
    expediente       JSONB,
    -- Link público del PDF (sin sesión): código largo al azar; NULL = sin link.
    token_publico    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_analisis_marca_token ON analisis_marca(token_publico) WHERE token_publico IS NOT NULL;

-- Agregado el 07/10/2026: marcas de los oponentes (la que cita el fundamento de cada
-- oposición del «Análisis de marca»), una fila por acta: lo que figura en su expediente
-- de INPI (denominación, tipo de marca, clase, protección, limitación, agente) y su logo.
-- datos.estado = 'ok' | 'no_existe'. También la crea sola el panel al arrancar.
CREATE TABLE IF NOT EXISTS analisis_marca_opuesta (
    acta           TEXT PRIMARY KEY,
    datos          JSONB NOT NULL,
    logo           BYTEA,
    logo_mime      TEXT,
    consultado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Agregado el 07/10/2026: opción por titular del «Análisis de marca» (PDF con varias marcas):
-- «es el mismo análisis para todas las marcas». Se recuerda para todo el equipo.
-- También la crea sola el panel al arrancar.
CREATE TABLE IF NOT EXISTS analisis_titular_opcion (
    cuit             TEXT PRIMARY KEY,
    mismo_analisis   BOOLEAN NOT NULL DEFAULT false,
    actualizado_por  TEXT,
    actualizado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Agregado el 07/10/2026: Calendario (sincronización con Google Calendar, ver
-- panel/calendario_core.py). calendario_eventos es la copia local de los eventos del
-- calendario de Google; `actas` son los números de acta que nombra la nota del evento
-- (el cliente o lead se resuelve al leer). calendario_estado es una sola fila con el
-- marcador de sincronización incremental y el último error. También las crea sola el
-- panel al arrancar.
CREATE TABLE IF NOT EXISTS calendario_eventos (
    id                  BIGSERIAL PRIMARY KEY,
    calendar_id         TEXT NOT NULL,
    google_id           TEXT NOT NULL,
    titulo              TEXT NOT NULL DEFAULT '',
    descripcion         TEXT,
    lugar               TEXT,
    inicio              TIMESTAMPTZ NOT NULL,
    fin                 TIMESTAMPTZ NOT NULL,
    todo_el_dia         BOOLEAN NOT NULL DEFAULT false,
    actas               TEXT[] NOT NULL DEFAULT '{}',
    origen              TEXT NOT NULL DEFAULT 'google',   -- 'google' | 'panel'
    creado_por          TEXT,
    link                TEXT,
    etag                TEXT,
    actualizado_google  TIMESTAMPTZ,
    sincronizado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (calendar_id, google_id)
);
-- Agregadas con la modalidad: 'meet' | 'llamada' | 'presencial' y el link de la videollamada de Meet.
ALTER TABLE calendario_eventos ADD COLUMN IF NOT EXISTS modalidad TEXT;
ALTER TABLE calendario_eventos ADD COLUMN IF NOT EXISTS meet_url TEXT;
-- Mail de la persona a la que se avisa del evento (para poder avisarle si después se cambia).
ALTER TABLE calendario_eventos ADD COLUMN IF NOT EXISTS email_aviso TEXT;
-- Recordatorios (seguimientos): eventos de todo el día marcados tipo='seguimiento'; hecho = ya cumplido.
ALTER TABLE calendario_eventos ADD COLUMN IF NOT EXISTS tipo TEXT NOT NULL DEFAULT 'evento';
ALTER TABLE calendario_eventos ADD COLUMN IF NOT EXISTS hecho BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_calendario_eventos_inicio ON calendario_eventos(inicio);
CREATE INDEX IF NOT EXISTS idx_calendario_eventos_actas ON calendario_eventos USING GIN (actas);

CREATE TABLE IF NOT EXISTS calendario_estado (
    id                 SMALLINT PRIMARY KEY DEFAULT 1,
    calendar_id        TEXT,
    sync_token         TEXT,
    ultimo_intento_en  TIMESTAMPTZ,
    ultimo_ok_en       TIMESTAMPTZ,
    ultima_completa_en TIMESTAMPTZ,
    ultimo_error       TEXT,
    ultimo_cambios     INTEGER,
    CONSTRAINT calendario_estado_una_fila CHECK (id = 1)
);

-- Disponibilidad horaria cargada como texto («próximo martes libre de 8 a 15»): un tramo libre por fila.
CREATE TABLE IF NOT EXISTS calendario_disponibilidad (
    id          BIGSERIAL PRIMARY KEY,
    fecha       DATE NOT NULL,
    desde       TIME NOT NULL,
    hasta       TIME NOT NULL,
    creado_por  TEXT,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (fecha, desde, hasta)
);
CREATE INDEX IF NOT EXISTS idx_calendario_disponibilidad_fecha ON calendario_disponibilidad(fecha);

-- Días enteros sin reuniones (feriado, cumpleaños…): no se ofrecen en la vista Disponibilidad.
CREATE TABLE IF NOT EXISTS calendario_dias_cerrados (
    id          BIGSERIAL PRIMARY KEY,
    fecha       DATE NOT NULL UNIQUE,
    motivo      TEXT NOT NULL DEFAULT '',
    creado_por  TEXT,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Mail diario «AGENDA» (20 hs, reuniones y llamadas del día siguiente): una fila por día de la agenda.
-- omitida = ese día no había ninguna reunión ni llamada y no se mandó nada.
CREATE TABLE IF NOT EXISTS calendario_agenda_envios (
    fecha         DATE PRIMARY KEY,
    intento_en    TIMESTAMPTZ,
    enviado_en    TIMESTAMPTZ,
    omitida       BOOLEAN NOT NULL DEFAULT false,
    cantidad      INTEGER,
    destinatarios TEXT,
    error         TEXT
);
