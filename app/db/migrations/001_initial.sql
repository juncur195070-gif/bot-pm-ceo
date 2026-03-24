-- ============================================================
-- DOCTOC Bot PM/CEO — Migration Inicial
-- 13 tablas + triggers + indices + secuencias
-- Se ejecuta automaticamente al crear la DB por primera vez
-- ============================================================

-- ── Secuencias para codigos automaticos ──
CREATE SEQUENCE IF NOT EXISTS seq_clientes START 1;
CREATE SEQUENCE IF NOT EXISTS seq_leads START 1;
CREATE SEQUENCE IF NOT EXISTS seq_devs START 1;
CREATE SEQUENCE IF NOT EXISTS seq_backlog START 1;

-- ============================================================
-- 1. CLIENTES — Clientes activos de Doctoc
-- ============================================================
CREATE TABLE IF NOT EXISTS clientes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo              VARCHAR(10) UNIQUE NOT NULL,
    nombre_clinica      VARCHAR(200) NOT NULL,
    mrr_mensual         NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (mrr_mensual >= 0),
    arr_calculado       NUMERIC(10,2) GENERATED ALWAYS AS (mrr_mensual * 12) STORED,
    tamano              VARCHAR(20) NOT NULL CHECK (tamano IN ('Grande','Mediana','Pequena')),
    sla_dias            INTEGER NOT NULL,
    segmento            VARCHAR(50),
    estado_cliente      VARCHAR(20) NOT NULL DEFAULT 'Activo'
                            CHECK (estado_cliente IN ('Activo','En riesgo','Suspendido','Churned')),
    contacto_nombre     VARCHAR(200),
    contacto_cargo      VARCHAR(200),
    contacto_whatsapp   VARCHAR(20),
    contacto_email      VARCHAR(200),
    fecha_inicio_contrato DATE,
    fecha_renovacion    DATE,
    renovacion_estado   VARCHAR(20) DEFAULT 'pendiente'
                            CHECK (renovacion_estado IN ('pendiente','contactado','renovado','perdido')),
    renovacion_notas    TEXT,
    fecha_ultimo_item_resuelto TIMESTAMPTZ,
    notas_comerciales   TEXT,
    airtable_record_id  VARCHAR(30),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clientes_estado ON clientes(estado_cliente);

-- ============================================================
-- 2. LEADS — Prospectos que aun no son clientes
-- ============================================================
CREATE TABLE IF NOT EXISTS leads (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo              VARCHAR(10) UNIQUE NOT NULL,
    nombre_clinica      VARCHAR(200) NOT NULL,
    contacto_nombre     VARCHAR(200),
    contacto_whatsapp   VARCHAR(20),
    estado_lead         VARCHAR(30) NOT NULL DEFAULT 'Nuevo'
                            CHECK (estado_lead IN ('Nuevo','En negociacion','Propuesta enviada','Perdido','Convertido')),
    mrr_estimado        NUMERIC(10,2) DEFAULT 0,
    tamano_estimado     VARCHAR(20),
    probabilidad_cierre NUMERIC(5,2) CHECK (probabilidad_cierre BETWEEN 0 AND 100),
    requisitos_solicitados TEXT,
    cliente_convertido_id UUID REFERENCES clientes(id),
    notas               TEXT,
    airtable_record_id  VARCHAR(30),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 3. DESARROLLADORES — Equipo de desarrollo
-- ============================================================
CREATE TABLE IF NOT EXISTS desarrolladores (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo              VARCHAR(10) UNIQUE NOT NULL,
    nombre_completo     VARCHAR(200) NOT NULL,
    nivel               VARCHAR(10) NOT NULL CHECK (nivel IN ('Junior','Mid','Senior')),
    horas_semana_base   INTEGER NOT NULL CHECK (horas_semana_base BETWEEN 1 AND 50),
    disponible          BOOLEAN NOT NULL DEFAULT TRUE,
    fecha_regreso       DATE,
    skills              TEXT[] NOT NULL DEFAULT '{}',
    whatsapp            VARCHAR(20) UNIQUE NOT NULL,
    email               VARCHAR(200),
    wip_limit           INTEGER GENERATED ALWAYS AS (
                            CASE WHEN nivel = 'Senior' THEN 2 ELSE 1 END
                        ) STORED,
    bug_guard_semana_actual BOOLEAN NOT NULL DEFAULT FALSE,
    bug_guard_horas_reserva INTEGER DEFAULT 0,
    historial_semanas_bug_guard INTEGER NOT NULL DEFAULT 0,
    ultima_semana_bug_guard DATE,
    horas_sprint_semana INTEGER GENERATED ALWAYS AS (
                            CASE WHEN bug_guard_semana_actual
                                THEN GREATEST(1, (horas_semana_base * 4 / 10))
                                ELSE horas_semana_base
                            END
                        ) STORED,
    tareas_completadas_total INTEGER NOT NULL DEFAULT 0,
    notas               TEXT,
    airtable_record_id  VARCHAR(30),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dev_disponible ON desarrolladores(disponible) WHERE disponible = TRUE;
CREATE UNIQUE INDEX IF NOT EXISTS idx_un_solo_bug_guard
    ON desarrolladores(bug_guard_semana_actual)
    WHERE bug_guard_semana_actual = TRUE;

-- ============================================================
-- 4. USUARIOS_AUTORIZADOS — Quien puede hablar con el bot
-- ============================================================
CREATE TABLE IF NOT EXISTS usuarios_autorizados (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whatsapp            VARCHAR(20) UNIQUE NOT NULL,
    nombre              VARCHAR(200) NOT NULL,
    rol                 VARCHAR(20) NOT NULL
                            CHECK (rol IN ('pm','ceo','desarrollador','autorizado')),
    desarrollador_id    UUID REFERENCES desarrolladores(id),
    activo              BOOLEAN NOT NULL DEFAULT TRUE,
    puede_reportar      BOOLEAN NOT NULL DEFAULT TRUE,
    puede_gestionar     BOOLEAN NOT NULL DEFAULT FALSE,
    recibe_resumen_nocturno BOOLEAN NOT NULL DEFAULT FALSE,
    recibe_alertas_urgentes BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usuarios_wa ON usuarios_autorizados(whatsapp) WHERE activo = TRUE;

-- ============================================================
-- 5. BACKLOG_ITEMS — Tabla central de tareas/bugs/solicitudes
-- ============================================================
CREATE TABLE IF NOT EXISTS backlog_items (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo              VARCHAR(10) UNIQUE NOT NULL,
    titulo              VARCHAR(200) NOT NULL,
    tipo                VARCHAR(30) NOT NULL,
    descripcion         TEXT,
    reportado_por_id    UUID REFERENCES usuarios_autorizados(id),
    cliente_id          UUID REFERENCES clientes(id) ON DELETE SET NULL,
    cliente_nombre      VARCHAR(200),
    cliente_mrr         NUMERIC(10,2) DEFAULT 0,
    cliente_tamano      VARCHAR(20),
    cliente_sla_dias    INTEGER,
    es_lead             BOOLEAN NOT NULL DEFAULT FALSE,
    lead_id             UUID REFERENCES leads(id) ON DELETE SET NULL,
    urgencia_declarada  VARCHAR(20) CHECK (urgencia_declarada IN ('Critica','Alta','Media','Baja')),
    deadline_interno    DATE,
    fecha_qa_estimada   DATE,
    deadline_cliente    DATE,
    impacto_todos_usuarios BOOLEAN NOT NULL DEFAULT FALSE,
    skill_requerido        TEXT[] DEFAULT '{}',
    esfuerzo_talla      VARCHAR(10) CHECK (esfuerzo_talla IN ('XS','S','M','L','XL')),
    horas_esfuerzo      INTEGER GENERATED ALWAYS AS (
                            CASE esfuerzo_talla
                                WHEN 'XS' THEN 2 WHEN 'S' THEN 4 WHEN 'M' THEN 8
                                WHEN 'L' THEN 16 WHEN 'XL' THEN 32 ELSE NULL
                            END
                        ) STORED,
    score_wsjf          NUMERIC(5,2) DEFAULT 0,
    posicion_backlog    INTEGER DEFAULT 9999,
    score_bloque_a      NUMERIC(4,2) DEFAULT 0,
    score_bloque_b      NUMERIC(4,2) DEFAULT 0,
    score_bloque_c      NUMERIC(4,2) DEFAULT 0,
    estado              VARCHAR(20) NOT NULL DEFAULT 'Backlog',
    dev_id              UUID REFERENCES desarrolladores(id) ON DELETE SET NULL,
    dev_nombre          VARCHAR(200),
    fecha_asignacion         TIMESTAMPTZ,
    sprint_semana            VARCHAR(10),
    fecha_inicio_desarrollo  TIMESTAMPTZ,
    fecha_qa                 TIMESTAMPTZ,
    fecha_desplegado         TIMESTAMPTZ,
    lead_time_horas          NUMERIC(6,1),
    cumplio_sla              BOOLEAN,
    adjuntos_urls       TEXT[] DEFAULT '{}',
    notas_dev           TEXT,
    notas_pm            TEXT,
    derivado_a          VARCHAR(200),
    derivado_motivo     TEXT,
    airtable_record_id  VARCHAR(30),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backlog_estado ON backlog_items(estado);
CREATE INDEX IF NOT EXISTS idx_backlog_codigo ON backlog_items(codigo);
CREATE INDEX IF NOT EXISTS idx_backlog_cliente ON backlog_items(cliente_id);
CREATE INDEX IF NOT EXISTS idx_backlog_dev ON backlog_items(dev_id);
CREATE INDEX IF NOT EXISTS idx_backlog_activos ON backlog_items(posicion_backlog)
    WHERE estado NOT IN ('Desplegado','Cancelado','Archivado');
CREATE INDEX IF NOT EXISTS idx_backlog_deadline ON backlog_items(deadline_interno)
    WHERE deadline_interno IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_backlog_sprint ON backlog_items(sprint_semana, dev_id);
CREATE INDEX IF NOT EXISTS idx_backlog_created ON backlog_items(created_at DESC);

-- ============================================================
-- 6. MENSAJES_CONVERSACION — Historial completo del bot
-- ============================================================
CREATE TABLE IF NOT EXISTS mensajes_conversacion (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id          UUID NOT NULL REFERENCES usuarios_autorizados(id),
    whatsapp            VARCHAR(20) NOT NULL,
    direccion           VARCHAR(10) NOT NULL CHECK (direccion IN ('entrante','saliente')),
    contenido           TEXT NOT NULL,
    tipo_contenido      VARCHAR(20) NOT NULL DEFAULT 'texto',
    media_url           TEXT,
    intencion_detectada VARCHAR(50),
    backlog_item_id     UUID REFERENCES backlog_items(id),
    tools_usados        TEXT[],
    kapso_message_id    VARCHAR(100),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mensajes_usuario ON mensajes_conversacion(usuario_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mensajes_wa ON mensajes_conversacion(whatsapp, created_at DESC);

-- ============================================================
-- 8. MENSAJES_PROCESADOS — Deduplicacion de webhooks
-- ============================================================
CREATE TABLE IF NOT EXISTS mensajes_procesados (
    idempotency_key     VARCHAR(100) PRIMARY KEY,
    procesado_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 9. WHATSAPP_SESIONES — Estado conversacional
-- ============================================================
CREATE TABLE IF NOT EXISTS whatsapp_sesiones (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whatsapp            VARCHAR(20) UNIQUE NOT NULL,
    usuario_id          UUID REFERENCES usuarios_autorizados(id),
    ultimo_mensaje_texto TEXT,
    ultimo_mensaje_at   TIMESTAMPTZ,
    ultimo_backlog_codigo VARCHAR(10),
    estado_conversacion VARCHAR(30) NOT NULL DEFAULT 'idle',
    contexto_json       JSONB DEFAULT '{}',
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 10. SCORING_HISTORIAL — Evolucion de scores noche a noche
-- ============================================================
CREATE TABLE IF NOT EXISTS scoring_historial (
    id                  BIGSERIAL PRIMARY KEY,
    backlog_item_id     UUID NOT NULL REFERENCES backlog_items(id) ON DELETE CASCADE,
    fecha_calculo       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    score_wsjf          NUMERIC(5,2) NOT NULL,
    posicion_backlog    INTEGER NOT NULL,
    score_bloque_a      NUMERIC(4,2) NOT NULL,
    score_bloque_b      NUMERIC(4,2) NOT NULL,
    score_bloque_c      NUMERIC(4,2) NOT NULL,
    dias_en_backlog     INTEGER,
    dias_al_deadline    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_scoring_item ON scoring_historial(backlog_item_id, fecha_calculo DESC);

-- ============================================================
-- 11. BUG_GUARD_HISTORIAL — Rotacion semanal
-- ============================================================
CREATE TABLE IF NOT EXISTS bug_guard_historial (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    semana_codigo       VARCHAR(10) UNIQUE NOT NULL,
    fecha_inicio_semana DATE NOT NULL,
    dev_id              UUID NOT NULL REFERENCES desarrolladores(id),
    dev_nombre          VARCHAR(200) NOT NULL,
    horas_reservadas    INTEGER NOT NULL,
    bugs_atendidos_total INTEGER NOT NULL DEFAULT 0,
    bugs_criticos       INTEGER NOT NULL DEFAULT 0,
    tiempo_promedio_respuesta_min NUMERIC(6,1),
    sla_critico_cumplido_pct NUMERIC(5,2),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 12. NOTIFICACIONES_INTERNAS — Log de WhatsApp enviados
-- ============================================================
CREATE TABLE IF NOT EXISTS notificaciones_internas (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    destinatario_whatsapp VARCHAR(20) NOT NULL,
    usuario_id          UUID REFERENCES usuarios_autorizados(id),
    tipo_mensaje        VARCHAR(60) NOT NULL,
    backlog_item_id     UUID REFERENCES backlog_items(id),
    mensaje_enviado     TEXT NOT NULL,
    estado_envio        VARCHAR(20) NOT NULL DEFAULT 'Enviado',
    kapso_message_id    VARCHAR(100),
    error_detalle       TEXT,
    sent_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 13. AUDITORIA_LOG — Registro de cada accion del sistema
-- ============================================================
CREATE TABLE IF NOT EXISTS auditoria_log (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    origen              VARCHAR(40) NOT NULL,
    usuario_id          UUID REFERENCES usuarios_autorizados(id),
    accion              VARCHAR(50) NOT NULL,
    backlog_item_id     UUID REFERENCES backlog_items(id),
    desarrollador_id    UUID REFERENCES desarrolladores(id),
    cliente_id          UUID REFERENCES clientes(id),
    detalle             TEXT,
    score_anterior      NUMERIC(5,2),
    score_nuevo         NUMERIC(5,2),
    resultado           VARCHAR(20) NOT NULL DEFAULT 'Exito',
    error_detalle       TEXT,
    metadata            JSONB
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON auditoria_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_item ON auditoria_log(backlog_item_id);

-- ============================================================
-- TRIGGERS
-- ============================================================

-- Trigger: updated_at automatico
CREATE OR REPLACE FUNCTION trg_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at := NOW(); RETURN NEW; END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_clientes_upd BEFORE UPDATE ON clientes FOR EACH ROW EXECUTE FUNCTION trg_updated_at();
CREATE TRIGGER trg_leads_upd BEFORE UPDATE ON leads FOR EACH ROW EXECUTE FUNCTION trg_updated_at();
CREATE TRIGGER trg_devs_upd BEFORE UPDATE ON desarrolladores FOR EACH ROW EXECUTE FUNCTION trg_updated_at();
CREATE TRIGGER trg_usuarios_upd BEFORE UPDATE ON usuarios_autorizados FOR EACH ROW EXECUTE FUNCTION trg_updated_at();
CREATE TRIGGER trg_backlog_upd BEFORE UPDATE ON backlog_items FOR EACH ROW EXECUTE FUNCTION trg_updated_at();
CREATE TRIGGER trg_sesiones_upd BEFORE UPDATE ON whatsapp_sesiones FOR EACH ROW EXECUTE FUNCTION trg_updated_at();

-- Trigger: generar codigos automaticos (BK-0001, CLI-001, etc.)
CREATE OR REPLACE FUNCTION trg_generar_codigo()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_TABLE_NAME = 'backlog_items' THEN
        NEW.codigo := 'BK-' || LPAD(nextval('seq_backlog')::text, 4, '0');
    ELSIF TG_TABLE_NAME = 'clientes' THEN
        NEW.codigo := 'CLI-' || LPAD(nextval('seq_clientes')::text, 3, '0');
    ELSIF TG_TABLE_NAME = 'leads' THEN
        NEW.codigo := 'LED-' || LPAD(nextval('seq_leads')::text, 3, '0');
    ELSIF TG_TABLE_NAME = 'desarrolladores' THEN
        NEW.codigo := 'DEV-' || LPAD(nextval('seq_devs')::text, 3, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cod_backlog BEFORE INSERT ON backlog_items FOR EACH ROW EXECUTE FUNCTION trg_generar_codigo();
CREATE TRIGGER trg_cod_clientes BEFORE INSERT ON clientes FOR EACH ROW EXECUTE FUNCTION trg_generar_codigo();
CREATE TRIGGER trg_cod_leads BEFORE INSERT ON leads FOR EACH ROW EXECUTE FUNCTION trg_generar_codigo();
CREATE TRIGGER trg_cod_devs BEFORE INSERT ON desarrolladores FOR EACH ROW EXECUTE FUNCTION trg_generar_codigo();

-- Trigger: calcular lead_time, SLA y fechas de ciclo de vida
CREATE OR REPLACE FUNCTION trg_calcular_lead_time()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.estado = 'Desplegado' AND OLD.estado != 'Desplegado' THEN
        NEW.fecha_desplegado := NOW();
        NEW.lead_time_horas := EXTRACT(EPOCH FROM (NOW() - NEW.created_at)) / 3600.0;
        IF NEW.cliente_sla_dias IS NOT NULL THEN
            NEW.cumplio_sla := (NEW.lead_time_horas / 24.0) <= NEW.cliente_sla_dias;
        END IF;
    END IF;
    IF NEW.estado = 'En Desarrollo' AND OLD.estado != 'En Desarrollo' THEN
        NEW.fecha_inicio_desarrollo := NOW();
    END IF;
    IF NEW.estado = 'En QA' AND OLD.estado != 'En QA' THEN
        NEW.fecha_qa := NOW();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_lead_time BEFORE UPDATE ON backlog_items FOR EACH ROW EXECUTE FUNCTION trg_calcular_lead_time();

-- Trigger: solo 1 Bug Guard a la vez
CREATE OR REPLACE FUNCTION trg_un_solo_bug_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.bug_guard_semana_actual = TRUE THEN
        UPDATE desarrolladores SET bug_guard_semana_actual = FALSE
        WHERE id != NEW.id AND bug_guard_semana_actual = TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_bug_guard BEFORE UPDATE ON desarrolladores FOR EACH ROW EXECUTE FUNCTION trg_un_solo_bug_guard();

-- ============================================================
-- DATOS INICIALES DE EJEMPLO
-- (Descomenta y ajusta con datos reales de tu equipo)
-- ============================================================

-- Ejemplo: insertar un cliente
-- INSERT INTO clientes (nombre_clinica, mrr_mensual, tamano, sla_dias, segmento, contacto_nombre, contacto_whatsapp)
-- VALUES ('MINSUR', 12000, 'Grande', 30, 'Mineria', 'Jorge Quispe', '+51987654321');

-- Ejemplo: insertar un desarrollador
-- INSERT INTO desarrolladores (nombre_completo, alias, nivel, horas_semana_base, skills, whatsapp)
-- VALUES ('Carlos Ramirez', 'Carlos R.', 'Senior', 26, '{Backend,BD,Integraciones API}', '+51999111111');

-- Ejemplo: insertar usuario autorizado (PM)
-- INSERT INTO usuarios_autorizados (whatsapp, nombre, rol, puede_gestionar, recibe_resumen_nocturno, recibe_alertas_urgentes)
-- VALUES ('+51999111222', 'Tatiana', 'pm', TRUE, TRUE, TRUE);
