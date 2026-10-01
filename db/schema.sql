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

-- Agregado para el seguimiento de oposiciones/vistas post-publicación
-- (ver scripts/revisar_oposiciones.py). fecha_publicacion viene de la fila
-- "Hoja Publicacion" de Grilla Digital — es la fecha real de publicación
-- de ESE expediente (más confiable que boletines.fecha, que es una sola
-- fecha por boletín y puede quedar en null si se forzó el boletín a mano).
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS fecha_publicacion DATE;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS tuvo_oposicion BOOLEAN;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS detalle_oposicion TEXT;
ALTER TABLE marcas ADD COLUMN IF NOT EXISTS revisado_oposicion_en TIMESTAMPTZ;

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
