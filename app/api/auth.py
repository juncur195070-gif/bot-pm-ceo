"""
Autenticacion de la API REST.

Fase 1: API Key simple en header X-API-Key.
Fase 2 (futuro): JWT con roles cuando haya web app.

Uso en las rutas:
    @router.get("/", dependencies=[Depends(verificar_api_key)])
    async def mi_ruta():
        ...
"""

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from app.config.settings import settings

# FastAPI busca este header automaticamente
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verificar_api_key(api_key: str = Security(api_key_header)):
    """
    Verifica que el request tenga una API Key valida.
    Si no la tiene o no coincide, responde 401.
    """
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Falta header X-API-Key"
        )
    if api_key != settings.API_KEY_ADMIN:
        raise HTTPException(
            status_code=401,
            detail="API Key invalida"
        )
    return api_key
