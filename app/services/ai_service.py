"""
AI Service — Wrapper agnóstico para OpenAI y Anthropic.

Provee una interfaz unificada para que el agent_loop no necesite saber
qué provider se usa. Cambia entre OpenAI y Anthropic via AI_PROVIDER en .env.

Formato de respuesta unificado:
{
    "stop_reason": "tool_use" | "end_turn" | "error",
    "content": [...],           # Formato Anthropic-compatible
    "text": "respuesta...",
    "tool_calls": [...],        # Objetos con .name, .input, .id
    "model_used": "gpt-4.1-mini",
    "duration_ms": 1234,
    "error": None | "mensaje de error"
}
"""

import time
import asyncio
from dataclasses import dataclass
from app.config.settings import settings


@dataclass
class ToolCall:
    """Wrapper para tool calls — misma interfaz que Anthropic."""
    id: str
    name: str
    input: dict


class OpenAIService:
    """Cliente async para OpenAI API con tool-use."""

    def __init__(self):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model_primary = settings.OPENAI_MODEL_PRIMARY
        self.model_fallback = settings.OPENAI_MODEL_FALLBACK
        self.max_tokens = settings.AI_MAX_TOKENS
        self.temperature = settings.AI_TEMPERATURE

    async def llamar(self, system: str, messages: list[dict], tools: list[dict] | None = None, model: str | None = None) -> dict:
        modelo = model or self.model_primary
        inicio = time.time()

        # Convertir tools de formato Anthropic → OpenAI
        openai_tools = self._convertir_tools(tools) if tools else None

        # Convertir messages de formato Anthropic → OpenAI
        openai_messages = self._convertir_messages(system, messages)

        try:
            response = await self._crear_mensaje(openai_messages, openai_tools, modelo)
        except Exception as e:
            error_msg = str(e).lower()
            if "rate limit" in error_msg or "429" in str(e):
                for intento, wait in enumerate([2, 4, 8], 1):
                    print(f"  ⚠ Rate limit, intento {intento}/3, esperando {wait}s...")
                    await asyncio.sleep(wait)
                    try:
                        response = await self._crear_mensaje(openai_messages, openai_tools, modelo)
                        break
                    except Exception:
                        continue
                else:
                    return self._error_response("Rate limit excedido.", time.time() - inicio)
            elif modelo == self.model_primary and modelo != self.model_fallback:
                print(f"  ⚠ {modelo} fallo, probando {self.model_fallback}...")
                try:
                    modelo = self.model_fallback
                    response = await self._crear_mensaje(openai_messages, openai_tools, modelo)
                except Exception as e2:
                    return self._error_response(str(e2), time.time() - inicio)
            else:
                return self._error_response(str(e), time.time() - inicio)

        duracion = int((time.time() - inicio) * 1000)
        return self._parse_response(response, modelo, duracion)

    async def _crear_mensaje(self, messages, tools, modelo):
        kwargs = {
            "model": modelo,
            "max_tokens": self.max_tokens,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
        return await self.client.chat.completions.create(**kwargs)

    def _convertir_tools(self, tools_anthropic: list[dict]) -> list[dict]:
        """Convierte tools de formato Anthropic → OpenAI function calling."""
        openai_tools = []
        for t in tools_anthropic:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                }
            })
        return openai_tools

    def _convertir_messages(self, system: str, messages: list[dict]) -> list[dict]:
        """Convierte messages de formato Anthropic → OpenAI."""
        openai_msgs = [{"role": "system", "content": system}]

        for msg in messages:
            role = msg["role"]
            content = msg.get("content")

            # Mensaje de texto normal
            if isinstance(content, str):
                openai_msgs.append({"role": role, "content": content})

            # Tool results (formato Anthropic → OpenAI)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "tool_result":
                            openai_msgs.append({
                                "role": "tool",
                                "tool_call_id": item["tool_use_id"],
                                "content": item.get("content", ""),
                            })
                    else:
                        # Content blocks de Anthropic (assistant con tool_use)
                        pass

            # Assistant content con tool_use blocks (formato Anthropic)
            elif role == "assistant" and content is not None:
                openai_msgs.append({"role": "assistant", "content": str(content)})

        return openai_msgs

    def _parse_response(self, response, modelo: str, duracion: int) -> dict:
        """Convierte respuesta OpenAI → formato unificado (compatible con Anthropic)."""
        choice = response.choices[0]
        message = choice.message

        # Extraer texto
        texto = message.content or ""

        # Extraer tool calls
        tool_calls = []
        if message.tool_calls:
            import json
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=args,
                ))

        # Mapear stop_reason
        if choice.finish_reason == "tool_calls":
            stop_reason = "tool_use"
        elif choice.finish_reason == "stop":
            stop_reason = "end_turn"
        else:
            stop_reason = choice.finish_reason or "end_turn"

        # Construir content blocks compatibles con Anthropic (para messages append)
        content_blocks = []
        if texto:
            content_blocks.append({"type": "text", "text": texto})
        if message.tool_calls:
            # Guardar raw tool_calls para re-enviar como assistant message
            content_blocks.append({"_openai_tool_calls": message.tool_calls})

        return {
            "response": response,
            "stop_reason": stop_reason,
            "content": message,  # OpenAI message object
            "text": texto,
            "tool_calls": tool_calls,
            "model_used": modelo,
            "duration_ms": duracion,
            "error": None,
        }

    def _error_response(self, error_msg: str, duracion_s: float) -> dict:
        return {
            "response": None, "stop_reason": "error", "content": [],
            "text": "", "tool_calls": [], "model_used": None,
            "duration_ms": int(duracion_s * 1000), "error": error_msg,
        }


class AnthropicService:
    """Cliente async para Anthropic API (wrapper del servicio original)."""

    def __init__(self):
        from anthropic import AsyncAnthropic
        self.client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model_primary = settings.CLAUDE_MODEL_PRIMARY
        self.model_fallback = settings.CLAUDE_MODEL_FALLBACK
        self.max_tokens = settings.AI_MAX_TOKENS
        self.temperature = settings.AI_TEMPERATURE

    async def llamar(self, system: str, messages: list[dict], tools: list[dict] | None = None, model: str | None = None) -> dict:
        from anthropic import APITimeoutError, APIError, RateLimitError
        modelo = model or self.model_primary
        inicio = time.time()

        try:
            response = await self._crear_mensaje(system, messages, tools, modelo)
        except RateLimitError:
            for intento, wait in enumerate([2, 4, 8], 1):
                print(f"  ⚠ Rate limit, intento {intento}/3, esperando {wait}s...")
                await asyncio.sleep(wait)
                try:
                    response = await self._crear_mensaje(system, messages, tools, modelo)
                    break
                except RateLimitError:
                    continue
            else:
                return self._error_response("Rate limit excedido.", time.time() - inicio)
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
        text_blocks = [b for b in response.content if b.type == "text"]
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        texto = "".join(b.text for b in text_blocks)

        return {
            "response": response, "stop_reason": response.stop_reason,
            "content": response.content, "text": texto, "tool_calls": tool_blocks,
            "model_used": modelo, "duration_ms": duracion, "error": None,
        }

    async def _crear_mensaje(self, system, messages, tools, modelo):
        kwargs = {"model": modelo, "max_tokens": self.max_tokens,
                  "system": system, "messages": messages, "temperature": self.temperature}
        if tools:
            kwargs["tools"] = tools
        return await self.client.messages.create(**kwargs)

    def _error_response(self, error_msg: str, duracion_s: float) -> dict:
        return {
            "response": None, "stop_reason": "error", "content": [],
            "text": "", "tool_calls": [], "model_used": None,
            "duration_ms": int(duracion_s * 1000), "error": error_msg,
        }


# ── Factory: crear el servicio correcto segun AI_PROVIDER ──
def _crear_servicio():
    if settings.AI_PROVIDER == "openai":
        print(f"   🤖 AI Provider: OpenAI ({settings.OPENAI_MODEL_PRIMARY})")
        return OpenAIService()
    else:
        print(f"   🤖 AI Provider: Anthropic ({settings.CLAUDE_MODEL_PRIMARY})")
        return AnthropicService()


ai_service = _crear_servicio()
