"""
Tool Registry — Maps tool names to handler functions.

Adding a new tool:
  1. Create handler in the appropriate domain module
  2. Import and register here
  3. Add definition in definitions.py

The registry handles: dispatching, error wrapping, and dev field filtering.
"""

import asyncpg
from app.tools.base import fail, filtrar_para_dev


# Handler registry — populated by imports below
_HANDLERS: dict[str, callable] = {}


def register(name: str):
    """Decorator to register a tool handler."""
    def decorator(fn):
        _HANDLERS[name] = fn
        return fn
    return decorator


async def ejecutar_tool(
    nombre: str,
    params: dict,
    conn: asyncpg.Connection,
    usuario: dict
) -> str:
    """
    Ejecuta un tool y retorna el resultado como string.

    Wrapper central que maneja:
    - Dispatch al handler correcto
    - Error handling uniforme
    - Filtrado de campos financieros para devs
    """
    handler = _HANDLERS.get(nombre)
    if not handler:
        return fail(f"Tool '{nombre}' no reconocido")

    try:
        # Determinar si el handler necesita 'usuario' o no
        import inspect
        sig = inspect.signature(handler)
        param_names = list(sig.parameters.keys())

        if "usuario" in param_names:
            result = await handler(conn, params, usuario)
        else:
            result = await handler(conn, params)
    except Exception as e:
        result = fail(f"Error ejecutando {nombre}: {str(e)}")

    # Filtrar campos financieros para devs
    if usuario.get("rol") == "desarrollador":
        result = filtrar_para_dev(result)

    return result


# ── Import all domain modules to trigger @register decorators ──
# Each module registers its own handlers on import
from app.tools import consultas      # noqa
from app.tools import backlog_ops    # noqa
from app.tools import equipo_ops     # noqa
from app.tools import cliente_ops    # noqa
from app.tools import utilidades     # noqa
from app.tools import predicciones   # noqa
