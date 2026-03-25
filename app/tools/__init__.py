"""
Tools package — domain-based tool modules.

Each module registers its handlers via @register decorator.
Use registry.ejecutar_tool() to dispatch.
"""

from app.tools.registry import ejecutar_tool  # noqa

__all__ = ["ejecutar_tool"]
