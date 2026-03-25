# CLAUDE.md — Guia para desarrolladores

## Que es este proyecto

Bot PM/CEO para gestion de tareas via WhatsApp. El PM y CEO interactuan por voz/texto con un bot que usa Claude (tool-use) para crear tareas, asignar devs, priorizar backlog, trackear bugs y gestionar clientes.

## Stack

- **Python 3.12** + **FastAPI** + **asyncpg**
- **Claude Haiku 4.5** (tool-use, loop agentico max 3 iteraciones)
- **PostgreSQL** (fuente unica de verdad)
- **Kapso** (WhatsApp API)
- **Airtable** (mirror push-only para Kanban)
- **APScheduler** (tareas programadas in-process)
- **Railway** (deploy) + **Supabase** o Railway PostgreSQL

## Arquitectura

```
WhatsApp (Kapso) → webhook → orchestrator → Claude → tools → DB → respuesta → WhatsApp
                                                        ↓
                                                   Airtable (sync)
```

## Estructura de carpetas

```
app/
├── main.py                  # FastAPI app, webhooks, healthcheck
├── config/
│   ├── settings.py          # Variables de entorno (pydantic-settings)
│   └── database.py          # Pool asyncpg
├── bot/                     # Capa de orquestacion
│   ├── orchestrator.py      # Flujo principal: webhook → auth → Claude → respuesta
│   ├── agent_loop.py        # Loop agentico: Claude → tool → Claude → ...
│   └── context_builder.py   # Construye prompt + historial + alertas por rol
├── tools/                   # Tools de Claude (organizados por dominio)
│   ├── registry.py          # Dispatcher con dict + @register decorator
│   ├── base.py              # Helpers: ok(), fail(), resolver_codigo(), sync_airtable()
│   ├── definitions.py       # Schemas JSON de cada tool para Claude
│   ├── consultas.py         # consultar_backlog, consultar_item, consultar_equipo, consultar_metricas, consultar_cliente
│   ├── backlog_ops.py       # crear_item, actualizar_item, asignar_tarea, establecer_fechas, reportar_bloqueo, derivar, adjuntar_imagen, actualizar_estado_dev
│   ├── equipo_ops.py        # reasignar_bug_guard, gestionar_dev
│   ├── cliente_ops.py       # gestionar_cliente, resumen_cliente
│   ├── utilidades.py        # recordatorio, buscar_historial, nota_rapida, cambiar_rol
│   └── predicciones.py      # predecir_entrega (Monte Carlo)
├── db/
│   ├── queries/             # Funciones SQL por entidad
│   │   ├── backlog.py       # CRUD backlog_items (con JOINs a clientes/devs)
│   │   ├── clientes.py      # CRUD clientes + fuzzy search
│   │   ├── desarrolladores.py # CRUD devs + capacidad equipo
│   │   ├── leads.py         # CRUD leads
│   │   ├── metricas.py      # Dashboard, rendimiento, velocidad, prediccion
│   │   └── auditoria.py     # Log de acciones
│   └── migrations/
│       ├── 001_initial.sql  # Schema completo (13 tablas + triggers)
│       └── 002_recordatorios.sql
├── api/
│   ├── router.py            # Monta /api/v1/*
│   ├── auth.py              # API Key auth (X-API-Key header)
│   └── routes/              # Endpoints REST por entidad
├── services/
│   ├── claude.py            # Cliente Anthropic (retry, fallback)
│   ├── kapso.py             # Cliente Kapso WhatsApp (enviar, verificar firma)
│   └── airtable_sync.py     # Push mirror a Airtable
├── prompts/
│   └── system_prompts.py    # Prompts por rol (PM, CEO, Dev, Autorizado)
├── scheduled/
│   ├── scheduler.py         # APScheduler config
│   ├── scoring.py           # WSJF scoring nocturno (23:00)
│   ├── asignacion.py        # Asignacion semanal (lun 08:00)
│   ├── monitoreo.py         # Alertas + briefing + recap
│   └── emergencia.py        # Asignacion inmediata de bugs criticos
└── utils/
    └── phone.py             # Normalizacion de telefonos
```

## Como agregar un nuevo tool

### 1. Crear el handler

En el modulo de dominio correcto (`app/tools/<dominio>.py`):

```python
from app.tools.registry import register
from app.tools.base import ok, fail

@register("mi_nuevo_tool")
async def mi_nuevo_tool(conn, params, usuario):
    """Descripcion de lo que hace."""
    # Logica de negocio
    resultado = await conn.fetch("SELECT ...")
    return ok({"message": "Hecho", "data": resultado})
```

Si el tool NO necesita `usuario`, omitelo:

```python
@register("mi_tool_sin_usuario")
async def mi_tool_sin_usuario(conn, params):
    ...
```

### 2. Agregar la definicion para Claude

En `app/tools/definitions.py`:

```python
TOOL_MI_NUEVO_TOOL = {
    "name": "mi_nuevo_tool",
    "description": "Descripcion clara para Claude de cuando usar este tool.",
    "input_schema": {
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "..."},
        },
        "required": ["param1"]
    }
}
```

Agregarlo a `ALL_TOOLS` y a los roles en `TOOLS_POR_ROL`.

### 3. Importar en registry

En `app/tools/registry.py`, agregar el import al final si es un modulo nuevo:

```python
from app.tools import mi_nuevo_modulo  # noqa
```

Si el tool esta en un modulo existente (consultas, backlog_ops, etc.), no se necesita nada mas — el `@register` se ejecuta al importar.

## Convenciones

### Respuestas de tools

SIEMPRE usar envelopes:
```python
return ok({"message": "Item creado", "data": item})     # Exito
return fail("Cliente no encontrado")                      # Error
```

Claude SOLO confirma acciones si ve `ok: true`. Si ve `ok: false`, informa el error.

### Verificacion Read-After-Write

Para operaciones de escritura, SIEMPRE verificar:
```python
await q_backlog.actualizar_item(conn, codigo, data)
verificado = await q_backlog.obtener_item(conn, codigo)
if not verificado:
    return fail("No se verifico en BD")
```

### Base de datos normalizada

- `backlog_items` NO tiene `cliente_nombre`, `dev_nombre`, etc.
- Esos datos vienen via JOIN desde `clientes` y `desarrolladores`
- Las funciones en `db/queries/backlog.py` ya hacen los JOINs
- NUNCA almacenar datos duplicados — solo `cliente_id` y `dev_id`

### Airtable sync

Despues de crear/actualizar un item, sincronizar:
```python
from app.tools.base import sync_item_airtable
await sync_item_airtable(conn, codigo)  # Background, no bloquea
```

### Busqueda fuzzy

PostgreSQL `unaccent()` esta habilitado. Las busquedas toleran:
- Tildes: "facturacion" encuentra "facturación"
- Typos parciales via LIKE con palabras clave

### Roles y permisos

| Rol | Puede |
|-----|-------|
| PM | Todo (23 tools) |
| CEO | Consultar + crear + asignar + clientes (16 tools) |
| Dev | Solo sus tareas + cambiar estado (4 tools) |
| Autorizado | Solo crear_item (1 tool) |

Los permisos se definen en `TOOLS_POR_ROL` en `definitions.py`.

## Comandos utiles

```bash
# Desarrollo local
docker-compose up -d --build
docker logs bot-pm-ceo-app-1 -f

# Base de datos
docker exec bot-pm-ceo-db-1 psql -U postgres -d doctoc_bot

# Deploy produccion
git push origin main  # Railway auto-deploya

# Migrar DB produccion (Railway PostgreSQL)
# Usar la PUBLIC_URL del PostgreSQL en Railway
psql "postgresql://postgres:xxx@host:port/railway" < app/db/migrations/001_initial.sql
```

## Variables de entorno

Ver `.env.example` para la lista completa. Las obligatorias en PROD:
- `ANTHROPIC_API_KEY`
- `API_KEY_ADMIN`
- `DATABASE_URL`
- `KAPSO_API_KEY`
- `KAPSO_PHONE_NUMBER_ID`

## Anti-mentira

El sistema detecta cuando Claude confirma una accion sin usar tools y reintenta con Sonnet. Ver `orchestrator.py` lineas 161-242.

## Scoring WSJF

Formula: `SCORE = (A × 40% + B × 35% + C × 25%) / job_size`
- A = Valor del cliente (MRR en soles, tamaño, churn risk)
- B = Gravedad de la tarea (tipo, urgencia, impacto)
- C = Urgencia temporal (deadline, antiguedad)

Rangos MRR en soles peruanos (no USD):
- >10K = 10, >3K = 8, >1K = 6, >500 = 5, >100 = 3, >0 = 2

## Bug Guard

Rotacion semanal automatica (lunes 8am). El PM puede override manualmente.
- `desarrolladores.historial_semanas_bug_guard` — contador total
- `desarrolladores.ultima_semana_bug_guard` — evita repetir consecutivo
- `bug_guard_historial` — registro por semana

## Tareas programadas

| Tarea | Horario | Archivo |
|-------|---------|---------|
| Scoring WSJF | 23:00 diario | scoring.py |
| Asignacion semanal | Lunes 08:00 | asignacion.py |
| Monitoreo alertas | L-V 09:00 | monitoreo.py |
| Briefing matutino | L-V 08:00 | monitoreo.py |
| Recap semanal | Viernes 17:00 | monitoreo.py |
