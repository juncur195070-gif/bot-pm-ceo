"""Shared helpers used by every tool module.

Shared helpers for all tool modules.
tool modules can import them without depending on the monolith.
"""

import json
import asyncio
from datetime import date, datetime

from app.db.queries import backlog as q_backlog
from app.db.queries.backlog import _normalizar_codigo
from app.services.airtable_sync import airtable_sync


# ── Serialisation ────────────────────────────────────────────────────

def _serializar(obj):
    """Convierte objetos no-JSON a string (UUID, date, etc.)."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if hasattr(obj, '__str__'):
        return str(obj)
    return obj


def _a_json(data) -> str:
    """Convierte resultado a JSON string para devolver a Claude."""
    return json.dumps(data, default=_serializar, ensure_ascii=False, indent=2)


# ── Envelopes ────────────────────────────────────────────────────────

def ok(data: dict) -> str:
    """Retorna envelope de exito. Claude SOLO confirma si ve ok=true."""
    return _a_json({"ok": True, **data})


def fail(error: str, **extra) -> str:
    """Retorna envelope de error. Claude debe informar el fallo al usuario."""
    return _a_json({"ok": False, "error": error, **extra})


# ── Helpers ──────────────────────────────────────────────────────────

async def resolver_codigo(conn, codigo: str, incluir_cancelados: bool = False) -> tuple[str | None, str | None]:
    """Resuelve un texto/codigo a un codigo BK-XXXX valido. Retorna (codigo, error)."""
    codigo = _normalizar_codigo(codigo)
    if codigo.startswith("BK-"):
        return codigo, None
    items = await q_backlog.buscar_items(conn, codigo, 1, incluir_cancelados=incluir_cancelados)
    if not items:
        return None, f"No encontre item con '{codigo}'"
    return items[0]["codigo"], None


async def sync_item_airtable(conn, codigo: str):
    """Sincroniza un item a Airtable en background (no bloquea respuesta)."""

    async def _do_sync():
        try:
            from app.config.database import get_pool
            pool = get_pool()
            async with pool.acquire() as sync_conn:
                item = await q_backlog.obtener_item(sync_conn, codigo)
                if not item:
                    return
                record_id = await airtable_sync.sync_backlog_item(dict(item))
                if record_id and not item.get("airtable_record_id"):
                    await q_backlog.actualizar_item(sync_conn, codigo, {"airtable_record_id": record_id})
        except Exception as e:
            print(f"  ⚠ Airtable sync failed for {codigo}: {e}")

    asyncio.create_task(_do_sync())


# ── Dev field filtering ─────────────────────────────────────────────

# Campos que el dev NO debe ver (datos financieros)
_CAMPOS_OCULTOS_DEV = {
    "cliente_mrr", "mrr_mensual", "arr_anual", "arr_calculado",
    "score_financiero", "notas_comerciales",
}


def filtrar_para_dev(resultado: str) -> str:
    """Elimina campos financieros del resultado antes de enviarlo al dev."""
    try:
        data = json.loads(resultado)
        _limpiar_recursivo(data)
        return json.dumps(data, default=_serializar, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        return resultado


def _limpiar_recursivo(obj):
    """Elimina campos sensibles de dicts y listas recursivamente."""
    if isinstance(obj, dict):
        for campo in _CAMPOS_OCULTOS_DEV:
            obj.pop(campo, None)
        for v in obj.values():
            _limpiar_recursivo(v)
    elif isinstance(obj, list):
        for item in obj:
            _limpiar_recursivo(item)
