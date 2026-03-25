"""
Agent Loop — El loop agentico del bot.

Este es el corazon del sistema. Implementa el patron:
  Claude piensa → pide tool → ejecuta → Claude piensa de nuevo → ...

Maximo 5 iteraciones para evitar loops infinitos.
Si Claude falla, retorna mensaje de error graceful.

Ejemplo de flujo:
  Iter 1: Claude pide consultar_backlog({cliente: "MINSUR"}) → recibe 3 items
  Iter 2: Claude pide asignar_tarea({codigo: "BK-0120", auto: true}) → asignado a Carlos
  Iter 3: Claude termina (end_turn) → "Registre BK-0120 y lo asigne a Carlos R."
"""

import asyncpg
from app.services.claude import claude_service
from app.bot.tool_executor import ejecutar_tool

MAX_ITERATIONS = 3  # Reducido de 5 a 3 — suficiente para consulta+accion+respuesta


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

        # Llamar a Claude (usa model_override si viene, sino el default)
        resultado = await claude_service.llamar(
            system=system_prompt,
            messages=messages,
            tools=tools if tools else None,
            model=model_override,
        )

        # Si Claude fallo completamente
        if resultado["error"]:
            error_msg = resultado["error"].lower()
            if "rate limit" in error_msg:
                respuesta_error = "Estoy procesando muchos mensajes. Espera unos segundos e intenta de nuevo."
            else:
                respuesta_error = "Tuve un problema procesando tu mensaje. Intenta de nuevo."
            return {
                "respuesta": respuesta_error,
                "iteraciones": iteracion,
                "tools_usados": tools_usados,
                "modelo_usado": resultado["model_used"],
                "error": resultado["error"],
            }

        # Si Claude quiere usar tools
        if resultado["stop_reason"] == "tool_use" and resultado["tool_calls"]:
            # Agregar la respuesta de Claude (con tool_use blocks) a messages
            messages.append({
                "role": "assistant",
                "content": resultado["content"]
            })

            # Ejecutar cada tool que Claude pidio
            tool_results = []
            for tool_call in resultado["tool_calls"]:
                tool_name = tool_call.name
                tool_input = tool_call.input

                print(f"  🔧 Tool: {tool_name}({tool_input})")
                tools_usados.append(tool_name)

                # Ejecutar el tool
                result = await ejecutar_tool(tool_name, tool_input, conn, usuario)

                print(f"  ✅ Resultado: {result[:200]}...")

                # Detectar si el tool fallo via JSON parse (robusto)
                is_error = False
                try:
                    import json
                    parsed = json.loads(result)
                    is_error = parsed.get("ok") is False
                except (json.JSONDecodeError, AttributeError):
                    is_error = result.startswith("Error")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": result,
                    "is_error": is_error,
                })

            # Agregar resultados de tools a messages (para que Claude los lea)
            messages.append({
                "role": "user",
                "content": tool_results,
            })

            # Continuar el loop — Claude procesara los resultados
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

    # Si se alcanzo el maximo de iteraciones
    return {
        "respuesta": "Estuve procesando mucho y no pude completar. Intenta ser mas especifico.",
        "iteraciones": MAX_ITERATIONS,
        "tools_usados": tools_usados,
        "modelo_usado": resultado.get("model_used"),
        "error": "max_iterations_reached",
    }
