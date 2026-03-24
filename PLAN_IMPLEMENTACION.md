# DOCTOC — Bot PM/CEO: Plan de Implementacion Completo

## Sistema de Gestion Interna de Tareas — Bot Conversacional WhatsApp

> **Version:** 7.0 — Arquitectura Servidor Simple
> **Stack:** FastAPI + PostgreSQL (Supabase) + Claude API (Tool-Use) + Kapso + Airtable (Kanban)
> **Hosting:** Railway.app ($5/mes) + Supabase (gratis)
> **Nombre del bot:** Configurable via `BOT_NAME` (default: "Carlo")

---

## TABLA DE CONTENIDO

1. [Vision General](#1-vision-general)
2. [Arquitectura del Sistema](#2-arquitectura)
3. [Tech Stack y Estructura del Proyecto](#3-tech-stack)
4. [Base de Datos — PostgreSQL via Supabase](#4-base-de-datos)
5. [API REST — Visualizacion y Futuro Web App](#5-api-rest)
6. [Airtable — Kanban Visual (Espejo)](#6-airtable)
7. [Integracion Kapso — Webhooks y Mensajeria](#7-kapso)
8. [Motor Conversacional — Claude Tool-Use](#8-motor-conversacional)
9. [Tools del Bot (14 Tools)](#9-tools)
10. [Sistema de Roles y Permisos](#10-roles)
11. [Gestion de Contexto Conversacional](#11-contexto)
12. [Tareas Programadas — APScheduler](#12-tareas-programadas)
13. [Sistema Bug Guard](#13-bug-guard)
14. [Scoring WSJF v2](#14-scoring)
15. [Nombre del Bot — Configuracion Global](#15-nombre-bot)
16. [Integracion Kapso — Ejemplo Completo](#16-ejemplo-kapso)
17. [Simulaciones de Conversacion](#17-simulaciones)
18. [Desarrollo Local — Docker Compose](#18-desarrollo-local)
19. [Deploy — Railway + Supabase](#19-deploy)
20. [Plan de Implementacion por Fases](#20-fases)
21. [Escalabilidad Futura](#21-escalabilidad)
22. [Variables de Entorno](#22-env-vars)

---

## 1. Vision General

### Que es

Bot conversacional interno de Doctoc via WhatsApp. PM (Tatiana), CEO (Diego) y desarrolladores gestionan tareas, bugs, asignaciones y metricas en lenguaje natural — texto, audios e imagenes.

### Que NO es

- NO es un bot para clientes externos de Doctoc
- NO envia notificaciones a clientes de Doctoc

### Principios de diseno

1. **Conversacional** — Sin comandos. Lenguaje natural, audios e imagenes
2. **Inteligente** — Claude con tool-use decide que hacer
3. **Sin perdida de contexto** — Historial completo en PostgreSQL
4. **PostgreSQL = fuente unica** — Todo vive en la DB. Airtable es solo vista visual
5. **API-first** — API REST para datos y futuro web app
6. **Simple de operar** — 1 servidor, 1 DB, `git push` para deploy
7. **Barato** — $5/mes total (Railway + Supabase gratis)
8. **Nombre configurable** — Cambiar en UN solo lugar

---

## 2. Arquitectura

```
┌──────────────────────────────────────────────────────────────┐
│  WhatsApp (texto / audio / imagen)                           │
│       ↓                                                      │
│  Kapso (transcribe audio, media_url, HMAC signature)         │
│       ↓                                                      │
│  HTTPS POST → tu-app.railway.app                             │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ↓
┌──────────────────────────────────────────────────────────────┐
│  FastAPI — Servidor Unico (Railway.app, $5/mes)              │
│  Siempre activo, 0 cold start, <1s respuesta                 │
│                                                              │
│  ENDPOINTS:                                                  │
│    POST /webhook/kapso         → Bot conversacional          │
│    POST /webhook/airtable      → Sync Airtable→PostgreSQL    │
│    GET  /api/v1/*              → API REST (14 grupos)        │
│    GET  /api/v1/docs           → Swagger UI auto-generada    │
│    GET  /health                → Healthcheck                 │
│                                                              │
│  TAREAS PROGRAMADAS (APScheduler, in-process):               │
│    23:00 diario  → scoring_wsjf()                            │
│    08:00 lunes   → asignacion_semanal()                      │
│    09:00 L-V     → monitoreo_alertas()                       │
│                                                              │
│  BACKGROUND TASKS (FastAPI nativo):                          │
│    → Procesamiento de mensaje (no bloquea webhook)           │
│    → Sync a Airtable (post-insert)                           │
│    → Envio de notificaciones                                 │
│    → Emergencia bugs criticos                                │
└──────────────────────┬──────────────────┬────────────────────┘
                       │                  │
                       ↓                  ↓
┌──────────────────────────┐  ┌────────────────────────────────┐
│  PostgreSQL (Supabase)   │  │  Airtable (Kanban visual)      │
│  Gratis (500MB)          │  │                                │
│  13 tablas               │  │  BACKLOG_MAESTRO (espejo)      │
│  Fuente unica de verdad  │  │  → Devs ven tarjetas           │
│                          │  │  → Arrastran para cambiar      │
│  BONUS Supabase:         │  │     estado                     │
│  → Dashboard web gratis  │  │  → Imagenes adjuntas           │
│  → API REST auto (bonus) │  │                                │
│  → Auth (futuro web app) │  │  Cambios → webhook → FastAPI   │
│  → Realtime (futuro)     │  │  → UPDATE PostgreSQL           │
└──────────────────────────┘  └────────────────────────────────┘
```

### Por que esta arquitectura y no Lambda

| Criterio | Lambda (descartado) | Servidor simple (elegido) |
|----------|-------------------|--------------------------|
| Costo | $20-35/mes | $5/mes |
| Latencia | 3-8s (cold start VPC) | <1s (siempre activo) |
| Servicios a configurar | 15+ AWS | 2 (Railway + Supabase) |
| Deploy | SAM + packaging + IAM | `git push main` |
| Dev local | Complejo (mock Lambda) | `docker-compose up` |
| Mantenimiento | Alto | Bajo |
| Cron jobs | EventBridge (config externa) | APScheduler (in-process) |
| Escala hasta | Infinita | ~5,000 msgs/dia (sobra) |

---

## 3. Tech Stack

| Capa | Tecnologia | Justificacion |
|------|-----------|---------------|
| **Runtime** | Python 3.12 | Async nativo |
| **Framework** | FastAPI 0.115+ | Async, Pydantic, auto-docs Swagger |
| **LLM principal** | Claude Sonnet 4.6 | Tool-use nativo, contexto largo |
| **LLM fallback** | Claude Haiku 4.5 | Rapido en timeouts |
| **Base de datos** | PostgreSQL 15 (Supabase) | Gratis, dashboard, columnas generadas |
| **DB driver** | asyncpg | Pool async, alto rendimiento |
| **WhatsApp** | Kapso API v2 | Audio transcription, media_url, HMAC |
| **Kanban** | Airtable | Vista visual para devs, Attachments |
| **HTTP client** | httpx | Async, retry, timeouts |
| **Validacion** | Pydantic v2 | Schemas API + outputs Claude |
| **Cron** | APScheduler | In-process, timezone support |
| **Observabilidad** | Logfire | OpenTelemetry nativo |
| **Hosting app** | Railway.app | $5/mes, deploy desde GitHub |
| **Hosting DB** | Supabase | Gratis, PostgreSQL managed |
| **Container** | Docker | Deploy + dev local |
| **Dev local** | docker-compose | PostgreSQL + app con hot reload |
| **CI/CD** | GitHub → Railway auto-deploy | Push to main = deploy |

### Estructura del proyecto

```
bot-pm-ceo/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app + startup/shutdown
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py            # BOT_NAME, env vars
│   │   └── database.py            # Pool asyncpg + Supabase URL
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── schemas.py             # Pydantic: API request/response
│   │   └── tool_schemas.py        # Pydantic: tool input/output
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── queries/
│   │   │   ├── clientes.py
│   │   │   ├── leads.py
│   │   │   ├── desarrolladores.py
│   │   │   ├── backlog.py
│   │   │   ├── bugs.py
│   │   │   ├── mensajes.py
│   │   │   ├── usuarios.py
│   │   │   ├── scoring.py
│   │   │   ├── asignacion.py
│   │   │   └── auditoria.py
│   │   └── migrations/
│   │       └── 001_initial.sql    # DDL 13 tablas + triggers
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py              # Router /api/v1
│   │   ├── auth.py                # API Key middleware
│   │   ├── dependencies.py        # DB pool injection
│   │   └── routes/
│   │       ├── backlog.py         # /api/v1/backlog
│   │       ├── clientes.py        # /api/v1/clientes
│   │       ├── desarrolladores.py # /api/v1/devs
│   │       ├── leads.py           # /api/v1/leads
│   │       ├── metricas.py        # /api/v1/metricas
│   │       ├── scoring.py         # /api/v1/scoring
│   │       ├── bug_guard.py       # /api/v1/bug-guard
│   │       └── auditoria.py       # /api/v1/auditoria
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── kapso.py               # Cliente Kapso (recibir + enviar)
│   │   ├── airtable_sync.py       # Push PostgreSQL → Airtable
│   │   ├── claude.py              # Wrapper Claude API + fallback
│   │   └── notificaciones.py      # Envio mensajes proactivos
│   │
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── orchestrator.py        # Flujo principal del bot
│   │   ├── context_builder.py     # Historial + permisos para Claude
│   │   ├── tool_executor.py       # Router ejecucion de tools
│   │   └── agent_loop.py          # Loop agentico (max 5 iter)
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── definitions.py         # JSON schema de los 14 tools
│   │   ├── consultar_backlog.py
│   │   ├── consultar_item.py
│   │   ├── consultar_equipo.py
│   │   ├── consultar_metricas.py
│   │   ├── consultar_cliente.py
│   │   ├── crear_item.py
│   │   ├── actualizar_estado.py
│   │   ├── asignar_tarea.py
│   │   ├── establecer_fechas.py
│   │   ├── reportar_bloqueo.py
│   │   ├── derivar_a_persona.py
│   │   ├── reasignar_bug_guard.py
│   │   ├── gestionar_cliente.py
│   │   └── gestionar_dev.py
│   │
│   ├── prompts/
│   │   ├── __init__.py
│   │   ├── system_prompts.py      # Prompts por rol (usa BOT_NAME)
│   │   └── templates.py           # Templates mensajes WA
│   │
│   └── scheduled/
│       ├── __init__.py
│       ├── scheduler.py           # APScheduler config
│       ├── scoring.py             # WSJF nocturno + resumenes
│       ├── asignacion.py          # Bug Guard + sprint matching
│       ├── monitoreo.py           # Alertas deadlines
│       └── emergencia.py          # Bugs criticos (invocacion directa)
│
├── tests/
│   ├── test_api.py
│   ├── test_tools.py
│   ├── test_scoring.py
│   ├── test_bot_flow.py
│   └── test_kapso.py
│
├── Dockerfile
├── docker-compose.yml             # Dev local: PostgreSQL + app
├── requirements.txt
├── .env.example
├── .gitignore
├── railway.toml                   # Config Railway deploy
└── PLAN_IMPLEMENTACION.md
```

---

## 4. Base de Datos — PostgreSQL via Supabase

### Por que Supabase

```
GRATIS:
  → 500MB almacenamiento (suficiente por meses)
  → PostgreSQL 15 managed
  → Dashboard web para ver/editar tablas
  → API REST auto-generada (PostgREST) como bonus
  → Auth integrado (para futuro web app login)
  → Sin configurar VPC, security groups, IAM

CONEXION:
  DATABASE_URL=postgresql://postgres:[password]@db.[project].supabase.co:5432/postgres
  → asyncpg se conecta directo, pool de 5-10 conexiones
```

### 13 Tablas — DDL Completo

```sql
-- ============================================================
-- TABLA: clientes
-- ============================================================
CREATE TABLE clientes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo              VARCHAR(10) UNIQUE NOT NULL,
    nombre_clinica      VARCHAR(200) NOT NULL,
    razon_social        VARCHAR(300),
    ruc                 CHAR(11) UNIQUE,
    mrr_mensual         NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (mrr_mensual >= 0),
    arr_anual           NUMERIC(10,2) CHECK (arr_anual >= 0),
    arr_calculado       NUMERIC(10,2) GENERATED ALWAYS AS (
                            COALESCE(arr_anual, mrr_mensual * 12)
                        ) STORED,
    score_financiero    NUMERIC(4,2) CHECK (score_financiero BETWEEN 1 AND 10),
    tier_cliente        VARCHAR(10) CHECK (tier_cliente IN ('Platinum','Gold','Silver','Bronze')),
    tamano              VARCHAR(20) NOT NULL CHECK (tamano IN ('Grande','Mediana','Pequena')),
    sla_dias            INTEGER NOT NULL,
    segmento            VARCHAR(50),
    estado_cliente      VARCHAR(20) NOT NULL DEFAULT 'Activo'
                            CHECK (estado_cliente IN ('Activo','En riesgo','Suspendido','Churned')),
    contacto_nombre     VARCHAR(200),
    contacto_cargo      VARCHAR(200),
    contacto_whatsapp   VARCHAR(20),
    contacto_email      VARCHAR(200),
    nro_medicos         INTEGER DEFAULT 0,
    nro_usuarios_sistema INTEGER DEFAULT 0,
    fecha_inicio_contrato DATE,
    fecha_renovacion    DATE,
    fecha_ultimo_item_resuelto TIMESTAMPTZ,
    notas_comerciales   TEXT,
    airtable_record_id  VARCHAR(30),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE SEQUENCE seq_clientes START 1;
CREATE INDEX idx_clientes_estado ON clientes(estado_cliente);
CREATE INDEX idx_clientes_tier ON clientes(tier_cliente);

-- ============================================================
-- TABLA: leads
-- ============================================================
CREATE TABLE leads (
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

CREATE SEQUENCE seq_leads START 1;

-- ============================================================
-- TABLA: desarrolladores
-- ============================================================
CREATE TABLE desarrolladores (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo              VARCHAR(10) UNIQUE NOT NULL,
    nombre_completo     VARCHAR(200) NOT NULL,
    alias               VARCHAR(50) NOT NULL,
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

CREATE SEQUENCE seq_devs START 1;
CREATE INDEX idx_dev_disponible ON desarrolladores(disponible) WHERE disponible = TRUE;
CREATE UNIQUE INDEX idx_un_solo_bug_guard
    ON desarrolladores(bug_guard_semana_actual)
    WHERE bug_guard_semana_actual = TRUE;

-- ============================================================
-- TABLA: usuarios_autorizados
-- ============================================================
CREATE TABLE usuarios_autorizados (
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

CREATE INDEX idx_usuarios_wa ON usuarios_autorizados(whatsapp) WHERE activo = TRUE;

-- ============================================================
-- TABLA: backlog_items (tabla central)
-- ============================================================
CREATE TABLE backlog_items (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo              VARCHAR(10) UNIQUE NOT NULL,
    titulo              VARCHAR(200) NOT NULL,
    tipo                VARCHAR(30) NOT NULL,
    descripcion         TEXT,
    pasos_reproducir    TEXT,
    criterios_aceptacion TEXT,
    origen              VARCHAR(30) NOT NULL DEFAULT 'WhatsApp',
    reportado_por_id    UUID REFERENCES usuarios_autorizados(id),
    cliente_id          UUID REFERENCES clientes(id) ON DELETE SET NULL,
    cliente_nombre      VARCHAR(200),
    cliente_mrr         NUMERIC(10,2) DEFAULT 0,
    cliente_tamano      VARCHAR(20),
    cliente_sla_dias    INTEGER,
    es_lead             BOOLEAN NOT NULL DEFAULT FALSE,
    lead_id             UUID REFERENCES leads(id) ON DELETE SET NULL,
    urgencia_declarada  VARCHAR(20) CHECK (urgencia_declarada IN ('Critica','Alta','Media','Baja')),
    urgencia_ia         VARCHAR(20) CHECK (urgencia_ia IN ('Critica','Alta','Media','Baja')),
    deadline_interno    DATE,
    fecha_qa_estimada   DATE,
    deadline_cliente    DATE,
    deadline_es_automatico BOOLEAN NOT NULL DEFAULT FALSE,
    impacto_todos_usuarios BOOLEAN NOT NULL DEFAULT FALSE,
    bloquea_otras_tareas   INTEGER NOT NULL DEFAULT 0,
    modulos_afectados      TEXT[] DEFAULT '{}',
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
    es_asignacion_emergencia BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_asignacion         TIMESTAMPTZ,
    sprint_semana            VARCHAR(10),
    fecha_inicio_desarrollo  TIMESTAMPTZ,
    fecha_qa                 TIMESTAMPTZ,
    fecha_desplegado         TIMESTAMPTZ,
    lead_time_horas          NUMERIC(6,1),
    cumplio_sla              BOOLEAN,
    adjuntos_urls       TEXT[] DEFAULT '{}',
    notas_ia            TEXT,
    notas_dev           TEXT,
    notas_pm            TEXT,
    derivado_a          VARCHAR(200),
    derivado_motivo     TEXT,
    airtable_record_id  VARCHAR(30),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE SEQUENCE seq_backlog START 1;
CREATE INDEX idx_backlog_estado ON backlog_items(estado);
CREATE INDEX idx_backlog_codigo ON backlog_items(codigo);
CREATE INDEX idx_backlog_cliente ON backlog_items(cliente_id);
CREATE INDEX idx_backlog_dev ON backlog_items(dev_id);
CREATE INDEX idx_backlog_activos ON backlog_items(posicion_backlog)
    WHERE estado NOT IN ('Desplegado','Cancelado','Archivado');
CREATE INDEX idx_backlog_deadline ON backlog_items(deadline_interno)
    WHERE deadline_interno IS NOT NULL;
CREATE INDEX idx_backlog_sprint ON backlog_items(sprint_semana, dev_id);
CREATE INDEX idx_backlog_created ON backlog_items(created_at DESC);

-- ============================================================
-- TABLA: bugs_reportados
-- ============================================================
CREATE TABLE bugs_reportados (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo              VARCHAR(10) UNIQUE NOT NULL,
    backlog_item_id     UUID REFERENCES backlog_items(id) ON DELETE CASCADE UNIQUE,
    cliente_id          UUID REFERENCES clientes(id) ON DELETE SET NULL,
    titulo              VARCHAR(200) NOT NULL,
    severidad           VARCHAR(40) NOT NULL,
    entorno             VARCHAR(20) NOT NULL DEFAULT 'Produccion',
    mensaje_error       TEXT,
    frecuencia          VARCHAR(30),
    reportado_por_id    UUID REFERENCES usuarios_autorizados(id),
    fecha_reporte       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fecha_resolucion    TIMESTAMPTZ,
    tiempo_resolucion_horas NUMERIC(6,1) GENERATED ALWAYS AS (
        EXTRACT(EPOCH FROM (fecha_resolucion - fecha_reporte)) / 3600.0
    ) STORED,
    causa_raiz          TEXT,
    solucion_aplicada   TEXT,
    bug_guard_id        UUID REFERENCES desarrolladores(id),
    fecha_respuesta_bug_guard TIMESTAMPTZ,
    sla_respuesta_cumplido BOOLEAN,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE SEQUENCE seq_bugs START 1;

-- ============================================================
-- TABLA: mensajes_conversacion
-- ============================================================
CREATE TABLE mensajes_conversacion (
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

CREATE INDEX idx_mensajes_usuario ON mensajes_conversacion(usuario_id, created_at DESC);
CREATE INDEX idx_mensajes_wa ON mensajes_conversacion(whatsapp, created_at DESC);

-- ============================================================
-- TABLA: mensajes_procesados (deduplicacion)
-- ============================================================
CREATE TABLE mensajes_procesados (
    idempotency_key     VARCHAR(100) PRIMARY KEY,
    procesado_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- TABLA: whatsapp_sesiones
-- ============================================================
CREATE TABLE whatsapp_sesiones (
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
-- TABLA: scoring_historial
-- ============================================================
CREATE TABLE scoring_historial (
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

CREATE INDEX idx_scoring_item ON scoring_historial(backlog_item_id, fecha_calculo DESC);

-- ============================================================
-- TABLA: bug_guard_historial
-- ============================================================
CREATE TABLE bug_guard_historial (
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
-- TABLA: notificaciones_internas
-- ============================================================
CREATE TABLE notificaciones_internas (
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
-- TABLA: auditoria_log
-- ============================================================
CREATE TABLE auditoria_log (
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

CREATE INDEX idx_audit_timestamp ON auditoria_log(timestamp DESC);
CREATE INDEX idx_audit_item ON auditoria_log(backlog_item_id);

-- ============================================================
-- TRIGGERS
-- ============================================================

-- updated_at automatico
CREATE OR REPLACE FUNCTION trg_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at := NOW(); RETURN NEW; END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_clientes_upd BEFORE UPDATE ON clientes FOR EACH ROW EXECUTE FUNCTION trg_updated_at();
CREATE TRIGGER trg_leads_upd BEFORE UPDATE ON leads FOR EACH ROW EXECUTE FUNCTION trg_updated_at();
CREATE TRIGGER trg_devs_upd BEFORE UPDATE ON desarrolladores FOR EACH ROW EXECUTE FUNCTION trg_updated_at();
CREATE TRIGGER trg_usuarios_upd BEFORE UPDATE ON usuarios_autorizados FOR EACH ROW EXECUTE FUNCTION trg_updated_at();
CREATE TRIGGER trg_backlog_upd BEFORE UPDATE ON backlog_items FOR EACH ROW EXECUTE FUNCTION trg_updated_at();
CREATE TRIGGER trg_sesiones_upd BEFORE UPDATE ON whatsapp_sesiones FOR EACH ROW EXECUTE FUNCTION trg_updated_at();

-- Generar codigos automaticos
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
    ELSIF TG_TABLE_NAME = 'bugs_reportados' THEN
        NEW.codigo := 'BUG-' || LPAD(nextval('seq_bugs')::text, 3, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cod_backlog BEFORE INSERT ON backlog_items FOR EACH ROW EXECUTE FUNCTION trg_generar_codigo();
CREATE TRIGGER trg_cod_clientes BEFORE INSERT ON clientes FOR EACH ROW EXECUTE FUNCTION trg_generar_codigo();
CREATE TRIGGER trg_cod_leads BEFORE INSERT ON leads FOR EACH ROW EXECUTE FUNCTION trg_generar_codigo();
CREATE TRIGGER trg_cod_devs BEFORE INSERT ON desarrolladores FOR EACH ROW EXECUTE FUNCTION trg_generar_codigo();
CREATE TRIGGER trg_cod_bugs BEFORE INSERT ON bugs_reportados FOR EACH ROW EXECUTE FUNCTION trg_generar_codigo();

-- Lead time + SLA + fechas de ciclo de vida
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

-- Bug Guard: solo 1 a la vez
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
```

---

## 5. API REST

### Endpoints completos

```
AUTH: API Key en header X-API-Key (fase 1) → JWT (fase 2 con web app)
DOCS: Auto-generadas en /api/v1/docs (Swagger UI interactiva)

BACKLOG:
  GET    /api/v1/backlog                    Listar con filtros + paginacion
  GET    /api/v1/backlog/{codigo}           Detalle (BK-0001)
  POST   /api/v1/backlog                    Crear item
  PATCH  /api/v1/backlog/{codigo}           Actualizar campos
  GET    /api/v1/backlog/kanban             Agrupado por estado
  GET    /api/v1/backlog/por-dev/{dev_id}   Tareas de un dev

CLIENTES:
  GET    /api/v1/clientes                   Listar
  GET    /api/v1/clientes/{codigo}          Detalle (CLI-001)
  POST   /api/v1/clientes                   Crear
  PATCH  /api/v1/clientes/{codigo}          Actualizar
  GET    /api/v1/clientes/{codigo}/backlog  Tickets del cliente
  GET    /api/v1/clientes/riesgo-churn      Clientes en riesgo

LEADS:
  GET    /api/v1/leads                      Listar
  POST   /api/v1/leads                      Crear
  PATCH  /api/v1/leads/{codigo}             Actualizar
  POST   /api/v1/leads/{codigo}/convertir   Convertir a cliente

DESARROLLADORES:
  GET    /api/v1/devs                       Listar con capacidad
  GET    /api/v1/devs/{codigo}              Detalle (DEV-001)
  POST   /api/v1/devs                       Crear
  PATCH  /api/v1/devs/{codigo}              Actualizar
  GET    /api/v1/devs/{codigo}/tareas       Tareas asignadas
  GET    /api/v1/devs/capacidad             Resumen capacidad equipo
  GET    /api/v1/devs/bug-guard             Bug Guard actual

METRICAS:
  GET    /api/v1/metricas/dashboard         Dashboard general
  GET    /api/v1/metricas/sla               SLA por periodo
  GET    /api/v1/metricas/lead-time         Lead time promedio
  GET    /api/v1/metricas/por-dev           Rendimiento por dev
  GET    /api/v1/metricas/por-cliente       Por cliente

SCORING:
  GET    /api/v1/scoring/actual             Ranking actual
  GET    /api/v1/scoring/historial/{codigo} Evolucion de un item

AUDITORIA:
  GET    /api/v1/auditoria                  Historial de acciones
  GET    /api/v1/auditoria/item/{codigo}    Historial de un item
```

---

## 6. Airtable — Kanban Visual

Airtable es un espejo visual. PostgreSQL es la fuente de verdad.

```
PostgreSQL ──push──→ Airtable BACKLOG_MAESTRO
                         │
                         ├── Kanban board (devs arrastran tarjetas)
                         ├── Filtros por dev ("Mis tareas")
                         ├── Imagenes adjuntas (campo Attachment)
                         └── Cambios → Airtable Automation → webhook
                                                            → UPDATE PostgreSQL
```

Si Airtable se cae: bot + API REST funcionan al 100%. Solo se pierde vista visual.

---

## 7. Integracion Kapso

### Recibir mensajes (webhook)

```
URL: POST https://tu-app.railway.app/webhook/kapso
Headers: X-Webhook-Signature (HMAC-SHA256), X-Idempotency-Key (UUID)

Texto:  message.text.body → contenido
Audio:  message.kapso.transcript.text → contenido (auto-transcripcion)
Imagen: message.kapso.media_url → URL, message.image.caption → texto
```

### Enviar mensajes

```
POST https://api.kapso.ai/meta/whatsapp/v24.0/{phoneNumberId}/messages
Header: X-API-Key
Body: { "messaging_product": "whatsapp", "to": "51987654321",
        "type": "text", "text": { "body": "mensaje" } }
```

### Webhook response

```python
@app.post("/webhook/kapso")
async def webhook(request: Request, bg: BackgroundTasks):
    payload = await request.json()
    sig = request.headers.get("X-Webhook-Signature", "")
    key = request.headers.get("X-Idempotency-Key", "")

    if not kapso.verificar_firma(await request.body(), sig):
        return {"error": "invalid"}, 401
    if await db.ya_procesado(key):
        return {"status": "dup"}

    bg.add_task(procesar_mensaje, payload, key)  # background
    return {"status": "ok"}  # 200 OK en <50ms
```

---

## 8. Motor Conversacional

Claude Sonnet 4.6 con tool-use. Loop agentico max 5 iteraciones. Fallback a Haiku 4.5 en timeout. Ultimos 8 mensajes como contexto. Tools filtrados por rol del usuario.

---

## 9. Tools del Bot (14 Tools)

| # | Tool | PM | CEO | Dev | Descripcion |
|---|------|-----|------|------|-------------|
| 1 | consultar_backlog | Todo | Todo | Solo suyas | Buscar/filtrar items |
| 2 | consultar_item | Todo | Todo | Solo suyas | Detalle de un item |
| 3 | consultar_equipo | Si | Si | Si | Estado de devs |
| 4 | consultar_metricas | Si | Si | No | Dashboard/KPIs |
| 5 | consultar_cliente | Si | Si | No | Datos cliente/lead |
| 6 | crear_item | Si | Si | No | Registrar tarea/bug |
| 7 | actualizar_estado | Todo | No | Solo suyas | Cambiar estado |
| 8 | asignar_tarea | Si | Si | No | Asignar/reasignar |
| 9 | establecer_fechas | Si | No | No | Deadlines |
| 10 | reportar_bloqueo | Si | No | Solo suyas | Reportar bloqueo |
| 11 | derivar_a_persona | Si | Si | No | Escalar con contexto |
| 12 | reasignar_bug_guard | Si | No | No | Cambiar Bug Guard |
| 13 | gestionar_cliente | Si | Si (upd) | No | CRUD clientes/leads |
| 14 | gestionar_dev | Si | No | No | CRUD desarrolladores |

---

## 10-14. Roles, Contexto, Tareas Programadas, Bug Guard, Scoring

**Roles:** PM=todo, CEO=lectura+asignar+derivar, Dev=sus tareas, Autorizado=solo reportar.

**Contexto:** 8 mensajes previos de mensajes_conversacion. Sesion multi-paso con TTL 30min.

**Tareas programadas:** APScheduler in-process. Scoring 23:00, asignacion lun 08:00, monitoreo L-V 09:00.

**Bug Guard:** Rotacion equitativa semanal. 60% bugs / 40% sprint.

**WSJF:** (A x 0.40) + (B x 0.35) + (C x 0.25). Multiplicador x2 si deadline <= 2 dias.

---

## 15. Nombre del Bot

```python
# app/config/settings.py
BOT_NAME = os.getenv("BOT_NAME", "Carlo")
# Cambiar: solo la variable de entorno. 0 archivos que editar.
```

---

## 16-17. Ejemplo Kapso y Simulaciones

**Audio:** PM graba → Kapso transcribe → bot crea item + asigna + sync Airtable.
**Imagen:** PM envia captura → bot adjunta URL al item → dev la ve en Airtable.
**Conversacion natural:** "como vamos?" → consultar_metricas → dashboard completo.

---

## 18. Desarrollo Local

```yaml
# docker-compose.yml
services:
  app:
    build: .
    ports: ["8000:8000"]
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/doctoc_bot
      - BOT_NAME=Carlo
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - KAPSO_API_KEY=${KAPSO_API_KEY}
      - KAPSO_PHONE_NUMBER_ID=${KAPSO_PHONE_NUMBER_ID}
      - KAPSO_WEBHOOK_SECRET=${KAPSO_WEBHOOK_SECRET}
    depends_on: [db]
    volumes: ["./app:/code/app"]

  db:
    image: postgres:15
    ports: ["5432:5432"]
    environment:
      - POSTGRES_DB=doctoc_bot
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./app/db/migrations:/docker-entrypoint-initdb.d

volumes:
  pgdata:
```

```bash
# Levantar todo:
docker-compose up

# Resultado:
#   PostgreSQL en localhost:5432 (13 tablas creadas)
#   FastAPI en http://localhost:8000
#   Swagger en http://localhost:8000/api/v1/docs
#   Hot reload: cambias codigo → se recarga solo
```

---

## 19. Deploy — Railway + Supabase

### Setup Supabase (5 minutos)

```
1. Crear cuenta en supabase.com
2. New Project → nombre: doctoc-bot, region: us-east-1
3. Copiar DATABASE_URL de Settings → Database
4. Abrir SQL Editor → pegar 001_initial.sql → Run
5. Verificar 13 tablas en Table Editor
```

### Setup Railway (5 minutos)

```
1. Crear cuenta en railway.app
2. New Project → Deploy from GitHub repo
3. Seleccionar bot-pm-ceo/bot-pm-ceo
4. Variables → agregar todas las env vars
5. Deploy → Railway detecta Dockerfile → build → live

railway.toml:
  [build]
  builder = "DOCKERFILE"
  dockerfilePath = "Dockerfile"

  [deploy]
  healthcheckPath = "/health"
  restartPolicyType = "ON_FAILURE"
```

### Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Configurar Kapso webhook

```
En Kapso dashboard:
  Webhook URL: https://tu-app.railway.app/webhook/kapso
  Events: whatsapp.message.received
  Secret: (el mismo que KAPSO_WEBHOOK_SECRET)
```

---

## 20. Plan de Implementacion por Fases

### Fase 1 — DB + API REST (Dia 1-3) ✅ COMPLETADA

- [x] requirements.txt, Dockerfile, docker-compose.yml, .gitignore, .env.example, railway.toml
- [x] app/config/settings.py (BOT_NAME, env vars, Pydantic validation)
- [x] app/config/database.py (asyncpg pool)
- [x] app/db/migrations/001_initial.sql (13 tablas + triggers + indices)
- [x] app/models/schemas.py (Pydantic schemas para API)
- [x] app/db/queries/ (clientes, desarrolladores, backlog, metricas, auditoria)
- [x] app/api/ (auth API Key, router, 5 grupos de rutas, 28 endpoints)
- [x] app/main.py (FastAPI con lifespan, healthcheck, CORS)
- [x] Verificado: docker-compose up → PostgreSQL + FastAPI → 28 rutas funcionando
- [x] Verificado: CRUD clientes (CLI-001), devs (DEV-001), backlog (BK-0001)
- [x] Verificado: Kanban view, dashboard metricas, capacidad equipo
- [x] Swagger UI accesible en http://localhost:8000/api/v1/docs

### Fase 2 — Bot Conversacional (Dia 3-7) ✅ COMPLETADA

- [x] app/services/kapso.py (verificar firma HMAC, extraer mensaje texto/audio/imagen, enviar respuesta)
- [x] app/services/claude.py (wrapper Anthropic API con fallback Sonnet→Haiku, manejo de errores)
- [x] app/prompts/system_prompts.py (prompts personalizados por rol: PM, CEO, Dev, Autorizado)
- [x] app/bot/context_builder.py (auth por WhatsApp, cargar ultimos 8 mensajes, construir contexto)
- [x] app/tools/definitions.py (14 tools con JSON Schema, filtrados por rol)
- [x] app/bot/tool_executor.py (implementacion de los 14 tools con queries a PostgreSQL)
- [x] app/bot/agent_loop.py (loop agentico max 5 iteraciones, manejo de tool_use + end_turn)
- [x] app/bot/orchestrator.py (flujo completo: dedup→auth→historial→Claude→tools→respuesta→WhatsApp)
- [x] POST /webhook/kapso en main.py (procesa en background, 200 OK inmediato)
- [x] Verificado: 29 rutas, 14 tools, 4 prompts, todos los imports exitosos
- [x] Docker build exitoso, app corriendo con webhook activo

### Fase 3 — Audio, Imagenes, Airtable (Dia 7-9) ✅ COMPLETADA

- [x] Audio: Kapso transcript se procesa como texto normal — Claude detecta multiples items
- [x] Imagen: media_url se pasa a Claude en el contenido, Claude asocia al item correcto por contexto
- [x] app/services/airtable_sync.py (push unidireccional PostgreSQL→Airtable)
- [x] Sync integrado en tool_executor.py (crear_item hace push a Airtable)
- [x] Verificado: audio con 3 items → Claude creo BK-0003, BK-0004, BK-0005 correctamente
- [x] Verificado: imagen con caption → Claude asocio al BK-0005 por contexto conversacional
- [ ] Pendiente Fase 5: webhook Airtable→PostgreSQL (cuando dev mueve tarjeta en Kanban)

### Fase 4 — Tareas Programadas (Dia 9-11) ✅ COMPLETADA

- [x] app/scheduled/scheduler.py (APScheduler con timezone America/Lima)
- [x] scoring.py (WSJF v2: 3 bloques, multiplicador emergencia, resumen PM/CEO)
- [x] asignacion.py (Bug Guard rotacion equitativa + matching skills/horas/WIP)
- [x] monitoreo.py (deadlines, vencidos, estancadas >4d, olvidadas >45d)
- [x] emergencia.py (asignacion inmediata al Bug Guard, notifica PM/CEO)
- [x] Scheduler integrado en main.py lifespan
- [x] Verificado: scoring manual calculo 5 items correctamente (BK-0004 top con 4.29)
- [x] Verificado: historial de scoring guardado (5 registros)

### Fase 5 — Deploy y Validacion (Dia 11-14)

- [ ] Deploy a Railway
- [ ] Configurar Kapso webhook produccion
- [ ] Pruebas end-to-end con PM y CEO
- [ ] Monitoreo primera semana

### Fase 6 — Web App (Futuro)

- [ ] Frontend React/Next.js sobre API REST
- [ ] Dashboard con graficos
- [ ] Auth JWT con roles
- [ ] Vista Kanban web propia

---

## 21. Escalabilidad Futura

```
AHORA (50 msgs/dia):     1 servidor Railway + Supabase free    $5/mes
CRECIMIENTO (500/dia):    Subir plan Railway                    $10/mes
ESCALA (5,000+/dia):      Workers Celery + Redis                $25/mes
ENTERPRISE (50,000+/dia): Migrar bot a Lambda, API en ECS       $50+/mes
```

La API REST y la DB no cambian en ninguna etapa. Solo se escala el procesamiento.

---

## 22. Variables de Entorno

```bash
# Bot
BOT_NAME=Carlo

# PostgreSQL (Supabase)
DATABASE_URL=postgresql://postgres:[pwd]@db.[project].supabase.co:5432/postgres

# Airtable (sync push)
AIRTABLE_API_KEY=patXXX
AIRTABLE_BASE_ID=appXXX

# Claude
ANTHROPIC_API_KEY=sk-ant-XXX

# Kapso
KAPSO_API_KEY=xxx
KAPSO_PHONE_NUMBER_ID=xxx
KAPSO_WEBHOOK_SECRET=xxx

# WhatsApp destinos
WHATSAPP_PM=+51999111222
WHATSAPP_CEO=+51999333444

# API REST auth
API_KEY_ADMIN=xxx

# Observabilidad
LOGFIRE_TOKEN=xxx
```
