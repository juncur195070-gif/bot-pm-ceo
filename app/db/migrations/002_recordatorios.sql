CREATE TABLE IF NOT EXISTS recordatorios (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id UUID,
    whatsapp VARCHAR(20) NOT NULL,
    texto TEXT NOT NULL,
    fecha_recordar TIMESTAMP WITH TIME ZONE NOT NULL,
    enviado BOOLEAN DEFAULT FALSE,
    backlog_item_id UUID REFERENCES backlog_items(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_recordatorios_fecha ON recordatorios(fecha_recordar) WHERE enviado = FALSE;
