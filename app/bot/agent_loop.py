"""
Agent Loop — El loop agentico del bot.

Este es el corazon del sistema. Implementa el patron:
  Claude piensa → pide tool → ejecuta → Claude piensa de nuevo → ...

Maximo 5 iteraciones para evitar loops infinitos.
Si Claude falla, retorna mensaje de error graceful.

Ejemplo de flujo:
  Iter 1: Claude pide consultar_backlog → recibe items
  Iter 2: Claude pide asignar_tarea → asigna al mejor dev
  Iter 3: Claude termina (end_turn) → respuesta final al usuario
"""

import asyncpg
from app.services.ai_service import ai_service
from app.config.settings import settings
from app.tools.registry import ejecutar_tool

MAX_ITERATIONS = 5  # 5 iteraciones: soporta crear dependencias (cliente→item→asignar) y acciones multiples


async def ejecutar_loop(
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    conn: asyncpg.Connection,
    usuario: dict,
    model_override: str | None = None,
) -> dict:
    """
    Ejecuta el loop agentico completo.

    Args:
        system_prompt: Instrucciones del bot (personalizado por rol)
        messages: Historial de conversacion + mensaje actual
        tools: Tools disponibles para el rol del usuario
        conn: Conexion a PostgreSQL
        usuario: Datos del usuario autenticado

    Returns:
        {
            "respuesta": "texto final para enviar al usuario",
            "iteraciones": cuantas vueltas tomo,
            "tools_usados": ["consultar_backlog", "asignar_tarea"],
            "modelo_usado": "claude-sonnet-4-6-...",
            "error": None o "mensaje de error",
        }
    """
    tools_usados = []
    iteracion = 0

    while iteracion < MAX_ITERATIONS:
        iteracion += 1

        # Llamar al AI service (OpenAI o Anthropic segun AI_PROVIDER)
        resultado = await ai_service.llamar(
            system=system_prompt,
            messages=messages,
            tools=tools if tools else None,
            model=model_override,
        )

        # Si la IA fallo completamente
        if resultado["error"]:
            error_msg = resultado["error"].lower()
            print(f"  ⚠ AI error (iter {iteracion}): {resultado['error'][:100]}")
            if "rate limit" in error_msg:
                respuesta_error = "Estoy procesando muchos mensajes. Espera unos segundos e intenta de nuevo."
            else:
                respuesta_error = f"Tuve un problema procesando tu mensaje ({resultado['error'][:50]}). Intenta de nuevo."
            return {
                "respuesta": respuesta_error,
                "iteraciones": iteracion,
                "tools_usados": tools_usados,
                "modelo_usado": resultado.get("model_used", "unknown"),
                "error": resultado["error"],
            }

        # Si la IA quiere usar tools
        if resultado["stop_reason"] == "tool_use" and resultado["tool_calls"]:
            # Agregar assistant message al historial (formato depende del provider)
            if settings.AI_PROVIDER == "openai":
                # OpenAI: assistant message con tool_calls como objeto nativo
                messages.append({
                    "role": "assistant",
                    "content": resultado["text"] or None,
                    "tool_calls": [
                        {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": __import__("json").dumps(tc.input)}}
                        for tc in resultado["tool_calls"]
                    ]
                })
            else:
                # Anthropic: assistant message con content blocks
                messages.append({
                    "role": "assistant",
                    "content": resultado["content"]
                })

            # Ejecutar cada tool
            tool_results = []
            for tool_call in resultado["tool_calls"]:
                tool_name = tool_call.name
                tool_input = tool_call.input

                print(f"  🔧 Tool: {tool_name}({tool_input})")
                tools_usados.append(tool_name)

                result = await ejecutar_tool(tool_name, tool_input, conn, usuario)
                print(f"  ✅ Resultado: {result[:200]}...")

                # Detectar si el tool fallo
                is_error = False
                try:
                    import json
                    parsed = json.loads(result)
                    is_error = parsed.get("ok") is False
                except (json.JSONDecodeError, AttributeError):
                    is_error = result.startswith("Error")

                tool_results.append({
                    "tool_call_id": tool_call.id,
                    "tool_use_id": tool_call.id,  # Anthropic compat
                    "type": "tool_result",
                    "content": result,
                    "is_error": is_error,
                })

            # Agregar resultados al historial (formato depende del provider)
            if settings.AI_PROVIDER == "openai":
                for tr in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tr["tool_call_id"],
                        "content": tr["content"],
                    })
            else:
                messages.append({
                    "role": "user",
                    "content": tool_results,
                })

            continue

        # Si Claude termino (end_turn) — extraer respuesta final
        elif resultado["stop_reason"] == "end_turn":
            texto = resultado["text"].strip()

            if not texto:
                texto = "Listo, procesado."

            return {
                "respuesta": texto,
                "iteraciones": iteracion,
                "tools_usados": tools_usados,
                "modelo_usado": resultado["model_used"],
                "error": None,
            }

        # Otro stop_reason inesperado
        else:
            return {
                "respuesta": "No pude procesar tu mensaje. Intenta reformularlo.",
                "iteraciones": iteracion,
                "tools_usados": tools_usados,
                "modelo_usado": resultado["model_used"],
                "error": f"stop_reason inesperado: {resultado['stop_reason']}",
            }

    # Si se alcanzo el maximo de iteraciones — informar qué se logró
    if tools_usados:
        resumen = f"Ejecute {len(tools_usados)} acciones ({', '.join(tools_usados)}) pero no alcance a completar todo. ¿Qué falta?"
    else:
        resumen = "No pude procesar tu mensaje. Intenta dividirlo en partes mas pequeñas o ser mas especifico."
    return {
        "respuesta": resumen,
        "iteraciones": MAX_ITERATIONS,
        "tools_usados": tools_usados,
        "modelo_usado": resultado.get("model_used"),
        "error": "max_iterations_reached",
    }
