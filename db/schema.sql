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

CREATE INDEX IF NOT EXISTS idx_marcas_boletin ON marcas(boletin);
CREATE INDEX IF NOT EXISTS idx_marcas_es_lead ON marcas(es_lead);
CREATE INDEX IF NOT EXISTS idx_marcas_titular ON marcas(titular);
