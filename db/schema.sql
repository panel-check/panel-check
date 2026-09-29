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

CREATE INDEX IF NOT EXISTS idx_marcas_boletin ON marcas(boletin);
CREATE INDEX IF NOT EXISTS idx_marcas_es_lead ON marcas(es_lead);
CREATE INDEX IF NOT EXISTS idx_marcas_titular ON marcas(titular);
CREATE INDEX IF NOT EXISTS idx_marcas_contactado ON marcas(contactado);
CREATE INDEX IF NOT EXISTS idx_marcas_fecha_publicacion ON marcas(fecha_publicacion);
