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
from pydantic import model_validator
from typing import Optional


class Settings(BaseSettings):
    """
    Cada campo es una variable de entorno.
    Pydantic la busca automaticamente (case insensitive).
    Si tiene default, es opcional. Si no, es obligatoria.
    """

    # ── Bot ──
    BOT_NAME: str = "Carlo"
    SETUP: str = "LOCAL"  # LOCAL, DEV, PROD

    # ── Base de Datos ──
    DATABASE_URL: str = "postgresql://postgres:postgres@db:5432/doctoc_bot"
    DB_POOL_MIN: int = 2
    DB_POOL_MAX: int = 10

    # ── Claude API ──
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL_PRIMARY: str = "claude-haiku-4-5-20251001"
    CLAUDE_MODEL_FALLBACK: str = "claude-3-haiku-20240307"
    CLAUDE_MAX_TOKENS: int = 800
    CLAUDE_TEMPERATURE: float = 0.1

    # ── WSJF Scoring ──
    WSJF_PESO_CLIENTE: float = 0.40
    WSJF_PESO_TAREA: float = 0.35
    WSJF_PESO_URGENCIA: float = 0.25

    # ── Capacidad de devs (factor de carga por nivel) ──
    CARGA_JUNIOR: float = 0.75
    CARGA_MID: float = 0.80
    CARGA_SENIOR: float = 0.85

    # ── Bug Guard ──
    BUG_GUARD_RATIO: float = 0.6  # 60% bugs, 40% sprint

    # ── Timeouts (segundos) ──
    KAPSO_TIMEOUT: int = 10
    AIRTABLE_TIMEOUT: int = 10
    IMAGEN_RECIENTE_MINUTOS: int = 10
    DEADLINE_AUTO_DIAS: int = 2

    # ── Kapso WhatsApp ──
    KAPSO_API_KEY: str = ""
    KAPSO_PHONE_NUMBER_ID: str = ""
    KAPSO_WEBHOOK_SECRET: str = ""
    KAPSO_API_VERSION: str = "v24.0"

    # ── Airtable (sync push) ──
    AIRTABLE_API_KEY: str = ""
    AIRTABLE_BASE_ID: str = ""

    # ── WhatsApp destinos fijos ──
    WHATSAPP_PM: str = ""
    WHATSAPP_CEO: str = ""

    # ── API REST ──
    API_KEY_ADMIN: str = ""

    # ── CORS ──
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # ── Observabilidad ──
    LOGFIRE_TOKEN: Optional[str] = None

    # ── Columnas permitidas para ORDER BY (whitelist anti SQL injection) ──
    ALLOWED_SORT_FIELDS: set = {
        "posicion_backlog", "created_at", "updated_at", "score_wsjf",
        "deadline_interno", "titulo", "estado", "tipo", "urgencia_declarada",
    }

    @model_validator(mode="after")
    def validar_produccion(self):
        """En PROD, API keys obligatorias no pueden estar vacias."""
        if self.SETUP == "PROD":
            if not self.API_KEY_ADMIN:
                raise ValueError("API_KEY_ADMIN es obligatorio en PROD")
            if not self.ANTHROPIC_API_KEY:
                raise ValueError("ANTHROPIC_API_KEY es obligatorio en PROD")
        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


# Instancia global — importar desde cualquier parte del codigo
# from app.config.settings import settings
settings = Settings()
