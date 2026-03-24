"""
Endpoints de DESARROLLADORES.

GET  /api/v1/devs              → Lista
GET  /api/v1/devs/capacidad    → Capacidad del equipo
GET  /api/v1/devs/bug-guard    → Bug Guard actual
GET  /api/v1/devs/{codigo}     → Detalle (DEV-001)
POST /api/v1/devs              → Crear
PATCH /api/v1/devs/{codigo}    → Actualizar
GET  /api/v1/devs/{codigo}/tareas → Tareas asignadas
"""

from fastapi import APIRouter, Depends, Query, HTTPException
import asyncpg

from app.api.dependencies import get_db_conn
from app.api.auth import verificar_api_key
from app.models.schemas import DevCreate, DevUpdate
from app.db.queries import desarrolladores as q

router = APIRouter(
    prefix="/devs",
    tags=["Desarrolladores"],
    dependencies=[Depends(verificar_api_key)]
)


@router.get("/")
async def listar_devs(
    solo_disponibles: bool = Query(False),
    conn: asyncpg.Connection = Depends(get_db_conn)
):
    """Lista todos los desarrolladores."""
    return await q.listar_devs(conn, solo_disponibles=solo_disponibles)


@router.get("/capacidad")
async def capacidad_equipo(conn: asyncpg.Connection = Depends(get_db_conn)):
    """Capacidad actual: horas libres, WIP, tareas activas por dev."""
    return await q.obtener_capacidad_equipo(conn)


@router.get("/bug-guard")
async def bug_guard_actual(conn: asyncpg.Connection = Depends(get_db_conn)):
    """Quien es el Bug Guard esta semana."""
    bg = await q.obtener_bug_guard(conn)
    if not bg:
        return {"message": "No hay Bug Guard asignado esta semana"}
    return bg


@router.get("/{codigo}")
async def detalle_dev(codigo: str, conn: asyncpg.Connection = Depends(get_db_conn)):
    """Detalle de un desarrollador por codigo (DEV-001)."""
    dev = await q.obtener_dev(conn, codigo)
    if not dev:
        raise HTTPException(404, f"Desarrollador {codigo} no encontrado")
    return dev


@router.post("/", status_code=201)
async def crear_dev(data: DevCreate, conn: asyncpg.Connection = Depends(get_db_conn)):
    """Registra un nuevo desarrollador."""
    dev = await q.crear_dev(conn, data.model_dump())
    return {"message": "Desarrollador creado", "codigo": dev["codigo"], "data": dev}


@router.patch("/{codigo}")
async def actualizar_dev(
    codigo: str,
    data: DevUpdate,
    conn: asyncpg.Connection = Depends(get_db_conn)
):
    """Actualiza campos de un dev (disponibilidad, skills, etc.)."""
    dev = await q.actualizar_dev(conn, codigo, data.model_dump(exclude_unset=True))
    if not dev:
        raise HTTPException(404, f"Desarrollador {codigo} no encontrado")
    return {"message": "Desarrollador actualizado", "data": dev}


@router.get("/{codigo}/tareas")
async def tareas_dev(codigo: str, conn: asyncpg.Connection = Depends(get_db_conn)):
    """Tareas activas asignadas a este dev."""
    return await q.obtener_tareas_dev(conn, codigo)
