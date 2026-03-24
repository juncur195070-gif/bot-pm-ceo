"""
Endpoints de BACKLOG — la tabla central del sistema.

GET    /api/v1/backlog              → Lista con filtros + paginacion
GET    /api/v1/backlog/kanban       → Agrupado por estado
GET    /api/v1/backlog/{codigo}     → Detalle (BK-0001)
POST   /api/v1/backlog              → Crear item
PATCH  /api/v1/backlog/{codigo}     → Actualizar
GET    /api/v1/backlog/buscar       → Busqueda por texto
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from uuid import UUID
import asyncpg

from app.api.dependencies import get_db_conn
from app.api.auth import verificar_api_key
from app.models.schemas import BacklogCreate, BacklogUpdate, BacklogListResponse
from app.db.queries import backlog as q

router = APIRouter(
    prefix="/backlog",
    tags=["Backlog"],
    dependencies=[Depends(verificar_api_key)]
)


@router.get("/")
async def listar_backlog(
    estado: Optional[str] = None,
    cliente_id: Optional[UUID] = None,
    dev_id: Optional[UUID] = None,
    tipo: Optional[str] = None,
    urgencia: Optional[str] = None,
    sort: str = Query("posicion_backlog:asc", description="Campo:asc|desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_db_conn)
):
    """Lista items del backlog con filtros, orden y paginacion."""
    items, total = await q.listar_backlog(
        conn, estado=estado, cliente_id=cliente_id, dev_id=dev_id,
        tipo=tipo, urgencia=urgencia, page=page, per_page=per_page, sort=sort
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page
    }


@router.get("/kanban")
async def vista_kanban(conn: asyncpg.Connection = Depends(get_db_conn)):
    """Items agrupados por estado — ideal para vista Kanban."""
    return await q.obtener_kanban(conn)


@router.get("/buscar")
async def buscar_items(
    texto: str = Query(..., min_length=2, description="Texto a buscar en titulo/descripcion"),
    limite: int = Query(5, ge=1, le=20),
    conn: asyncpg.Connection = Depends(get_db_conn)
):
    """Busca items por texto en titulo o descripcion."""
    return await q.buscar_items(conn, texto, limite)


@router.get("/{codigo}")
async def detalle_item(codigo: str, conn: asyncpg.Connection = Depends(get_db_conn)):
    """Detalle completo de un item por codigo (BK-0001)."""
    item = await q.obtener_item(conn, codigo)
    if not item:
        raise HTTPException(404, f"Item {codigo} no encontrado")
    return item


@router.post("/", status_code=201)
async def crear_item(data: BacklogCreate, conn: asyncpg.Connection = Depends(get_db_conn)):
    """Crea un nuevo item en el backlog."""
    item = await q.crear_item(conn, data.model_dump())
    return {"message": "Item creado", "codigo": item["codigo"], "data": item}


@router.patch("/{codigo}")
async def actualizar_item(
    codigo: str,
    data: BacklogUpdate,
    conn: asyncpg.Connection = Depends(get_db_conn)
):
    """Actualiza campos de un item. El trigger actualiza fechas automaticamente."""
    item = await q.actualizar_item(conn, codigo, data.model_dump(exclude_unset=True))
    if not item:
        raise HTTPException(404, f"Item {codigo} no encontrado")
    return {"message": "Item actualizado", "data": item}
