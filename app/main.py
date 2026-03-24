"""
DOCTOC Bot PM/CEO — Punto de entrada de la aplicacion.

Este archivo:
1. Crea la app FastAPI
2. Conecta a PostgreSQL al arrancar
3. Registra la API REST (/api/v1/*)
4. Expone webhooks para Kapso y Airtable (Fase 2)
5. Healthcheck para Railway

Para correr localmente:
    uvicorn app.main:app --reload --port 8000

Para correr con Docker:
    docker-compose up
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings
from app.config.database import init_db, close_db
from app.api.router import api_router
from app.services.kapso import kapso_service
from app.bot.orchestrator import procesar_mensaje


# ── Lifespan: lo que pasa al arrancar y apagar la app ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan manager — se ejecuta al inicio y al final de la app.

    INICIO (antes de recibir requests):
      → Conectar pool de PostgreSQL
      → (Fase 2) Iniciar APScheduler

    FINAL (al apagar):
      → Cerrar pool de PostgreSQL
      → (Fase 2) Detener APScheduler
    """
    # ── STARTUP ──
    print(f"🚀 Iniciando {settings.BOT_NAME} Bot...")
    print(f"   Entorno: {settings.SETUP}")
    # No loguear DATABASE_URL — contiene credenciales
    print(f"   DB: conectando...")

    # Conectar a PostgreSQL
    await init_db()
    print("   ✅ PostgreSQL conectado")

    # Iniciar tareas programadas (scoring 23:00, asignacion lun 08:00, monitoreo L-V 09:00)
    from app.scheduled.scheduler import configurar_tareas
    configurar_tareas()

    print(f"   ✅ {settings.BOT_NAME} listo para recibir requests")

    yield  # La app corre aqui

    # ── SHUTDOWN ──
    print(f"   🛑 Apagando {settings.BOT_NAME}...")
    await close_db()
    print("   ✅ PostgreSQL desconectado")


# ── Crear la app FastAPI ──
app = FastAPI(
    title=f"DOCTOC {settings.BOT_NAME} Bot API",
    description="Sistema de gestion interna de tareas via WhatsApp + API REST",
    version="7.0",
    lifespan=lifespan,
    # Swagger UI accesible en /api/v1/docs
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
)


# ── CORS: origenes configurables via env var ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS.split(",") if settings.CORS_ORIGINS else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type", "Authorization"],
)


# ── Registrar la API REST ──
app.include_router(api_router)


# ── Healthcheck ──
# Railway (y otros) verifican que la app este viva haciendo GET /health
@app.get("/health", tags=["Sistema"])
async def healthcheck():
    """Verifica que la app este funcionando. Railway lo usa como healthcheck."""
    return {
        "status": "ok",
        "bot_name": settings.BOT_NAME,
        "version": "7.0"
    }


# ── Raiz ──
@app.get("/", tags=["Sistema"])
async def root():
    """Pagina raiz — redirige mentalmente a /api/v1/docs para ver Swagger."""
    return {
        "message": f"Bienvenido al API de {settings.BOT_NAME}",
        "docs": "/api/v1/docs",
        "health": "/health"
    }


# ── Webhook Kapso — Recibe mensajes de WhatsApp ──
@app.post("/webhook/kapso", tags=["Webhooks"])
async def webhook_kapso(request: Request, background_tasks: BackgroundTasks):
    """
    Recibe mensajes de WhatsApp via Kapso.

    Flujo:
      1. Verificar firma HMAC-SHA256 (seguridad)
      2. Obtener idempotency key (deduplicacion)
      3. Procesar en background (no bloquear respuesta)
      4. Responder 200 OK a Kapso en <50ms

    Kapso reintenta 3 veces si no recibe 200 en 10 segundos.
    Por eso procesamos en background y respondemos inmediatamente.
    """
    # Leer body crudo para verificar firma
    body_bytes = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    idempotency_key = request.headers.get("X-Idempotency-Key", "")

    # 1. Verificar firma
    if not kapso_service.verificar_firma(body_bytes, signature):
        print(f"  ⚠ SEGURIDAD: Firma invalida desde webhook. Signature: {signature[:20]}...")
        return JSONResponse(status_code=401, content={"error": "Firma invalida"})

    # 2. Parsear payload
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "JSON invalido"})

    # 3. Solo procesar mensajes entrantes (ignorar status updates)
    event = request.headers.get("X-Webhook-Event", "")
    if event and event != "whatsapp.message.received":
        return {"status": "ignored", "event": event}

    # 4. Procesar en background — responder 200 OK inmediatamente
    background_tasks.add_task(procesar_mensaje, payload, idempotency_key)

    return {"status": "received"}


# ── Webhook Airtable — Dev mueve tarjeta en Kanban ──
@app.post("/webhook/airtable", tags=["Webhooks"])
async def webhook_airtable(request: Request, background_tasks: BackgroundTasks):
    """
    Recibe cambios de Airtable cuando un dev mueve una tarjeta en el Kanban.

    Airtable Automation envia:
    {
        "record_id": "recXXX",
        "codigo_backlog": "BK-0001",
        "nuevo_estado": "En Desarrollo",
        "anterior_estado": "En Analisis"
    }

    El bot actualiza PostgreSQL y los triggers calculan fechas automaticamente.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "JSON invalido"})
    background_tasks.add_task(_procesar_cambio_airtable, payload)
    return {"status": "received"}


async def _procesar_cambio_airtable(payload: dict):
    """Procesa un cambio de estado desde Airtable."""
    from app.config.database import get_pool
    from app.db.queries import backlog as q_backlog
    from app.db.queries import auditoria as q_audit
    from app.services.kapso import kapso_service

    codigo = payload.get("codigo_backlog", "")
    nuevo_estado = payload.get("nuevo_estado", "")
    anterior = payload.get("anterior_estado", "")

    if not codigo or not nuevo_estado:
        print("  ⚠ Airtable webhook: payload incompleto, ignorando")
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            # Actualizar estado en PostgreSQL (trigger calcula fechas)
            item = await q_backlog.actualizar_item(conn, codigo, {"estado": nuevo_estado})

            if not item:
                print(f"  ⚠ Airtable webhook: {codigo} no encontrado en DB")
                return

            print(f"  📋 Airtable: {codigo} cambio {anterior} → {nuevo_estado} (dev: {item.get('dev_nombre', '?')})")

            # Si se desplego, actualizar fecha_ultimo_item_resuelto del cliente
            if nuevo_estado == "Desplegado" and item.get("cliente_id"):
                await conn.execute(
                    "UPDATE clientes SET fecha_ultimo_item_resuelto = NOW() WHERE id = $1",
                    item["cliente_id"]
                )

            # Notificar al PM del cambio
            if settings.WHATSAPP_PM and nuevo_estado in ("En Desarrollo", "En QA", "Desplegado"):
                dev = item.get("dev_nombre") or "Sin asignar"
                emoji = {"En Desarrollo": "🔨", "En QA": "🧪", "Desplegado": "✅"}.get(nuevo_estado, "📋")
                await kapso_service.enviar_texto_seguro(
                    settings.WHATSAPP_PM,
                    f"{emoji} [{codigo}] {item['titulo']}\n{anterior} → **{nuevo_estado}**\nDev: {dev}"
                )

            # Audit log
            await q_audit.registrar_accion(
                conn,
                origen="airtable_webhook",
                accion="estado_actualizado",
                backlog_item_id=item["id"],
                detalle=f"{codigo}: {anterior} → {nuevo_estado} (via Airtable)"
            )

        except Exception as e:
            print(f"  ❌ Airtable webhook error: {e}")
