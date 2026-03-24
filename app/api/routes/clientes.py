"""
Endpoints de CLIENTES.

GET  /api/v1/clientes              → Lista con filtros
GET  /api/v1/clientes/{codigo}     → Detalle (CLI-001)
POST /api/v1/clientes              → Crear
PATCH /api/v1/clientes/{codigo}    → Actualizar
GET  /api/v1/clientes/{codigo}/backlog → Tickets del cliente
GET  /api/v1/clientes/riesgo-churn → Clientes en riesgo
"""

from fastapi import APIRouter, Depends, Query, HTTPException
import asyncpg

from app.api.dependencies import get_db_conn
from app.api.auth import verificar_api_key
from app.models.schemas import ClienteCreate, ClienteUpdate
from app.db.queries import clientes as q

router = APIRouter(
    prefix="/clientes",
    tags=["Clientes"],
    dependencies=[Depends(verificar_api_key)]  # Todas las rutas requieren API Key
)


@router.get("/")
async def listar_clientes(
    estado: str | None = Query(None, description="Filtrar por estado: Activo, En riesgo, etc."),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_db_conn)
):
    """Lista todos los clientes con filtros y paginacion."""
    items, total = await q.listar_clientes(conn, estado=estado, page=page, per_page=per_page)
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page
    }


@router.get("/riesgo-churn")
async def clientes_riesgo_churn(conn: asyncpg.Connection = Depends(get_db_conn)):
    """Clientes activos sin atencion hace mas de 30 dias."""
    return await q.obtener_clientes_riesgo_churn(conn)


@router.get("/{codigo}")
async def detalle_cliente(codigo: str, conn: asyncpg.Connection = Depends(get_db_conn)):
    """Detalle completo de un cliente por codigo (CLI-001)."""
    cliente = await q.obtener_cliente(conn, codigo)
    if not cliente:
        raise HTTPException(404, f"Cliente {codigo} no encontrado")
    return cliente


@router.post("/", status_code=201)
async def crear_cliente(data: ClienteCreate, conn: asyncpg.Connection = Depends(get_db_conn)):
    """Crea un cliente nuevo. Codigo generado automaticamente."""
    cliente = await q.crear_cliente(conn, data.model_dump())
    return {"message": "Cliente creado", "codigo": cliente["codigo"], "data": cliente}


@router.patch("/{codigo}")
async def actualizar_cliente(
    codigo: str,
    data: ClienteUpdate,
    conn: asyncpg.Connection = Depends(get_db_conn)
):
    """Actualiza campos de un cliente. Solo envia los campos que quieres cambiar."""
    cliente = await q.actualizar_cliente(conn, codigo, data.model_dump(exclude_unset=True))
    if not cliente:
        raise HTTPException(404, f"Cliente {codigo} no encontrado")
    return {"message": "Cliente actualizado", "data": cliente}


@router.get("/{codigo}/backlog")
async def backlog_cliente(codigo: str, conn: asyncpg.Connection = Depends(get_db_conn)):
    """Todos los items activos del backlog de este cliente."""
    return await q.obtener_backlog_cliente(conn, codigo)
