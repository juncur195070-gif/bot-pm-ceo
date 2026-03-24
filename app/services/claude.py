"""
Servicio Claude — Wrapper ASINCRONO de Anthropic API con fallback.

IMPORTANTE: Usa AsyncAnthropic para no bloquear el event loop.
Esto permite procesar multiples mensajes de WhatsApp en paralelo.

Si Claude Sonnet falla (timeout, error), reintenta con Haiku.
"""

import time
import asyncio
from anthropic import AsyncAnthropic, APITimeoutError, APIError, RateLimitError
from app.config.settings import settings


class ClaudeService:
    """
    Cliente ASYNC para Claude API con fallback automatico.

    Usa AsyncAnthropic — las llamadas a Claude NO bloquean el servidor.
    Tatiana y Diego pueden hablar con el bot al mismo tiempo.
    """

    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model_primary = settings.CLAUDE_MODEL_PRIMARY
        self.model_fallback = settings.CLAUDE_MODEL_FALLBACK
        self.max_tokens = settings.CLAUDE_MAX_TOKENS
        self.temperature = settings.CLAUDE_TEMPERATURE

    async def llamar(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> dict:
        """
        Llama a Claude API de forma ASINCRONA.

        No bloquea el event loop — otros requests se procesan mientras
        Claude piensa la respuesta.
        """
        modelo = model or self.model_primary
        inicio = time.time()

        try:
            response = await self._crear_mensaje(system, messages, tools, modelo)
        except RateLimitError:
            # Rate limit — esperar mas tiempo y reintentar (max 3 veces)
            for wait in [3, 10, 20]:
                print(f"  ⚠ Rate limit, esperando {wait}s...")
                await asyncio.sleep(wait)
                try:
                    response = await self._crear_mensaje(system, messages, tools, modelo)
                    break
                except RateLimitError:
                    continue
            else:
                return self._error_response("Rate limit excedido. Espera 1 minuto e intenta de nuevo.", time.time() - inicio)
        except (APITimeoutError, APIError) as e:
            if modelo == self.model_primary and modelo != self.model_fallback:
                print(f"  ⚠ {modelo} fallo ({type(e).__name__}), probando {self.model_fallback}...")
                try:
                    modelo = self.model_fallback
                    response = await self._crear_mensaje(system, messages, tools, modelo)
                except Exception as e2:
                    return self._error_response(str(e2), time.time() - inicio)
            else:
                return self._error_response(str(e), time.time() - inicio)

        duracion = int((time.time() - inicio) * 1000)

        # Extraer contenido de la respuesta
        text_blocks = [b for b in response.content if b.type == "text"]
        tool_blocks = [b for b in response.content if b.type == "tool_use"]

        texto = ""
        for b in text_blocks:
            texto += b.text

        return {
            "response": response,
            "stop_reason": response.stop_reason,
            "content": response.content,
            "text": texto,
            "tool_calls": tool_blocks,
            "model_used": modelo,
            "duration_ms": duracion,
            "error": None,
        }

    async def _crear_mensaje(self, system, messages, tools, modelo):
        """
        Llamada ASINCRONA a la API de Anthropic.
        await = libera el hilo mientras espera la respuesta de Claude.
        """
        kwargs = {
            "model": modelo,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools

        return await self.client.messages.create(**kwargs)

    def _error_response(self, error_msg: str, duracion_s: float) -> dict:
        """Retorna respuesta de error cuando Claude no esta disponible."""
        return {
            "response": None,
            "stop_reason": "error",
            "content": [],
            "text": "",
            "tool_calls": [],
            "model_used": None,
            "duration_ms": int(duracion_s * 1000),
            "error": error_msg,
        }


# Instancia global
claude_service = ClaudeService()
