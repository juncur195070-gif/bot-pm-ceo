"""
Endpoints de AUDITORIA — Log de acciones del sistema.

GET /api/v1/auditoria            → Historial de acciones
"""

from fastapi import APIRouter, Depends, Query
from typing import Optional
from uuid import UUID
import asyncpg

from app.api.dependencies import get_db_conn
from app.api.auth import verificar_api_key
from app.db.queries import auditoria as q

router = APIRouter(
    prefix="/auditoria",
    tags=["Auditoria"],
    dependencies=[Depends(verificar_api_key)]
)


@router.get("/")
async def listar_auditoria(
    accion: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    conn: asyncpg.Connection = Depends(get_db_conn)
):
    """Historial de acciones del sistema."""
    items, total = await q.listar_auditoria(conn, accion=accion, page=page, per_page=per_page)
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page
    }
