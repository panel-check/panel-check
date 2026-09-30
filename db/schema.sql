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
