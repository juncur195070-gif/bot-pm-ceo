"""
Conexion a PostgreSQL usando asyncpg.

Pool de conexiones: mantiene N conexiones abiertas y las reutiliza.
Esto evita el overhead de abrir/cerrar conexion en cada request.

Uso:
    from app.config.database import get_pool

    async def mi_query():
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM clientes")
            return rows
"""

import asyncpg
from app.config.settings import settings

# Variable global que guarda el pool de conexiones
# Se inicializa en init_db() y se usa en toda la app
_pool: asyncpg.Pool | None = None


async def init_db() -> asyncpg.Pool:
    """
    Crea el pool de conexiones a PostgreSQL.
    Se llama UNA vez al arrancar la app (en main.py).

    El pool mantiene entre DB_POOL_MIN y DB_POOL_MAX conexiones
    abiertas y listas para usar.
    """
    global _pool
    try:
        # statement_cache_size=0 requerido para Supabase (usa PgBouncer)
        # Sin esto: "prepared statement already exists" error
        _pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=settings.DB_POOL_MIN,
            max_size=settings.DB_POOL_MAX,
            statement_cache_size=0,
        )
        # Validar que la conexion funciona
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as e:
        print(f"  ❌ Error conectando a PostgreSQL: {e}")
        raise
    return _pool


async def close_db():
    """
    Cierra todas las conexiones del pool.
    Se llama al apagar la app (en main.py).
    """
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """
    Retorna el pool de conexiones activo.
    Usar en los endpoints y queries.

    Lanza error si el pool no fue inicializado
    (significa que init_db() no se llamo).
    """
    if _pool is None:
        raise RuntimeError("Database pool no inicializado. Llama init_db() primero.")
    return _pool
