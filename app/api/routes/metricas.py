"""
Endpoints de METRICAS — Dashboard y KPIs.

GET /api/v1/metricas/dashboard   → Dashboard general
GET /api/v1/metricas/por-dev     → Rendimiento por dev
"""

from fastapi import APIRouter, Depends, Query
import asyncpg

from app.api.dependencies import get_db_conn
from app.api.auth import verificar_api_key
from app.db.queries import metricas as q

router = APIRouter(
    prefix="/metricas",
    tags=["Metricas"],
    dependencies=[Depends(verificar_api_key)]
)


@router.get("/dashboard")
async def dashboard(
    periodo: str = Query("esta_semana", description="esta_semana, este_mes, ultimos_7_dias, ultimos_30_dias"),
    conn: asyncpg.Connection = Depends(get_db_conn)
):
    """Dashboard general: items completados, SLA, lead time, bugs, riesgos."""
    return await q.dashboard_general(conn, periodo)


@router.get("/por-dev")
async def rendimiento_devs(
    periodo: str = Query("esta_semana"),
    conn: asyncpg.Connection = Depends(get_db_conn)
):
    """Rendimiento de cada dev: completados, en progreso, lead time, SLA."""
    return await q.rendimiento_por_dev(conn, periodo)
