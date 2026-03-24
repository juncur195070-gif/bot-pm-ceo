"""
Queries para AUDITORIA_LOG.
Solo INSERT (append-only) y SELECT (consulta).
Nunca UPDATE ni DELETE — es un log inmutable.
"""

import asyncpg
from typing import Optional
from uuid import UUID


async def registrar_accion(
    conn: asyncpg.Connection,
    origen: str,
    accion: str,
    usuario_id: Optional[UUID] = None,
    backlog_item_id: Optional[UUID] = None,
    desarrollador_id: Optional[UUID] = None,
    cliente_id: Optional[UUID] = None,
    detalle: Optional[str] = None,
    score_anterior: Optional[float] = None,
    score_nuevo: Optional[float] = None,
    resultado: str = "Exito",
    error_detalle: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Registra una accion en el log de auditoria."""
    import json
    row = await conn.fetchrow(
        """INSERT INTO auditoria_log (
            origen, accion, usuario_id, backlog_item_id, desarrollador_id,
            cliente_id, detalle, score_anterior, score_nuevo,
            resultado, error_detalle, metadata
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        RETURNING *""",
        origen, accion, usuario_id, backlog_item_id, desarrollador_id,
        cliente_id, detalle, score_anterior, score_nuevo,
        resultado, error_detalle, json.dumps(metadata) if metadata else None
    )
    return dict(row)


async def listar_auditoria(
    conn: asyncpg.Connection,
    backlog_item_id: Optional[UUID] = None,
    usuario_id: Optional[UUID] = None,
    accion: Optional[str] = None,
    page: int = 1,
    per_page: int = 50
) -> tuple[list[dict], int]:
    """Lista entradas de auditoria con filtros."""
    where = "WHERE 1=1"
    params = []
    idx = 1

    if backlog_item_id:
        where += f" AND backlog_item_id = ${idx}"; params.append(backlog_item_id); idx += 1
    if usuario_id:
        where += f" AND usuario_id = ${idx}"; params.append(usuario_id); idx += 1
    if accion:
        where += f" AND accion = ${idx}"; params.append(accion); idx += 1

    total = await conn.fetchval(f"SELECT COUNT(*) FROM auditoria_log {where}", *params)

    offset = (page - 1) * per_page
    rows = await conn.fetch(
        f"""SELECT * FROM auditoria_log {where}
            ORDER BY timestamp DESC LIMIT ${idx} OFFSET ${idx + 1}""",
        *params, per_page, offset
    )
    return [dict(r) for r in rows], total
