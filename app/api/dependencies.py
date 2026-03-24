"""
Dependencias compartidas para los endpoints de la API.

La mas importante: get_db_conn() — obtiene una conexion del pool
y la devuelve automaticamente al terminar el request.

Uso en rutas:
    @router.get("/clientes")
    async def listar(conn=Depends(get_db_conn)):
        return await queries.listar_clientes(conn)
"""

import asyncpg
from app.config.database import get_pool


async def get_db_conn() -> asyncpg.Connection:
    """
    Dependency que obtiene una conexion del pool de PostgreSQL.

    FastAPI la ejecuta automaticamente antes de cada endpoint
    que la declare como dependencia con Depends(get_db_conn).

    La conexion se devuelve al pool al terminar el request.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn
