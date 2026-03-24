"""
Router principal de la API REST.

Agrupa todas las rutas bajo el prefijo /api/v1.
Cada grupo de rutas esta en su propio archivo en routes/.

Resultado:
    /api/v1/backlog/*
    /api/v1/clientes/*
    /api/v1/devs/*
    /api/v1/metricas/*
    /api/v1/auditoria/*
    /api/v1/docs  ← Swagger UI auto-generada
"""

from fastapi import APIRouter

from app.api.routes import (
    backlog,
    clientes,
    desarrolladores,
    metricas,
    auditoria,
)

# Router padre — todo cuelga de /api/v1
api_router = APIRouter(prefix="/api/v1")

# Registrar cada grupo de rutas
api_router.include_router(backlog.router)
api_router.include_router(clientes.router)
api_router.include_router(desarrolladores.router)
api_router.include_router(metricas.router)
api_router.include_router(auditoria.router)
