"""
Configuracion central de la aplicacion.

Lee TODAS las variables de entorno y las expone como un objeto tipado.
Si falta una variable obligatoria, la app NO arranca y te dice cual falta.

Uso en cualquier parte del codigo:
    from app.config.settings import settings
    print(settings.BOT_NAME)  # "Carlo"
    print(settings.DATABASE_URL)  # "postgresql://..."
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """
    Cada campo es una variable de entorno.
    Pydantic la busca automaticamente (case insensitive).
    Si tiene default, es opcional. Si no, es obligatoria.
    """

    # ── Bot ──
    # Nombre del bot. Aparece en respuestas y prompts.
    # Cambiar aqui = cambiar en TODO el sistema.
    BOT_NAME: str = "Carlo"

    # Entorno de ejecucion
    SETUP: str = "LOCAL"  # LOCAL, DEV, PROD

    # ── Base de Datos ──
    # URL completa de conexion a PostgreSQL
    # Local: postgresql://postgres:postgres@db:5432/doctoc_bot
    # Supabase: postgresql://postgres:[pwd]@db.[project].supabase.co:5432/postgres
    DATABASE_URL: str = "postgresql://postgres:postgres@db:5432/doctoc_bot"

    # Pool de conexiones: cuantas conexiones simultaneas a la DB
    DB_POOL_MIN: int = 2
    DB_POOL_MAX: int = 10

    # ── Claude API ──
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL_PRIMARY: str = "claude-haiku-4-5-20251001"
    CLAUDE_MODEL_FALLBACK: str = "claude-3-haiku-20240307"
    CLAUDE_MAX_TOKENS: int = 800  # WhatsApp tiene limite de chars, no necesitamos mas
    CLAUDE_TEMPERATURE: float = 0.1  # Bajo para evitar que invente datos

    # ── WSJF Scoring ──
    WSJF_PESO_CLIENTE: float = 0.40
    WSJF_PESO_TAREA: float = 0.35
    WSJF_PESO_URGENCIA: float = 0.25

    # ── Capacidad de devs (factor de carga por nivel) ──
    # Porcentaje de horas_semana_base que se puede asignar
    # El resto es para code review, reuniones, imprevistos
    CARGA_JUNIOR: float = 0.75   # 75% — necesita mas tiempo para aprender
    CARGA_MID: float = 0.80      # 80% — estandar
    CARGA_SENIOR: float = 0.85   # 85% — mas eficiente

    # ── Kapso WhatsApp ──
    KAPSO_API_KEY: str = ""
    KAPSO_PHONE_NUMBER_ID: str = ""
    KAPSO_WEBHOOK_SECRET: str = ""

    # ── Airtable (sync push) ──
    AIRTABLE_API_KEY: str = ""
    AIRTABLE_BASE_ID: str = ""

    # ── WhatsApp destinos fijos ──
    WHATSAPP_PM: str = ""
    WHATSAPP_CEO: str = ""

    # ── API REST ──
    API_KEY_ADMIN: str = "dev-api-key-123"

    # ── Observabilidad ──
    LOGFIRE_TOKEN: Optional[str] = None

    # ── Configuracion de pydantic-settings ──
    class Config:
        # Buscar variables en archivo .env automaticamente
        env_file = ".env"
        # Si la variable existe en el sistema Y en .env, la del sistema gana
        env_file_encoding = "utf-8"


# Instancia global — importar desde cualquier parte del codigo
# from app.config.settings import settings
settings = Settings()
