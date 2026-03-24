"""
Context Builder — Construye todo el contexto que Claude necesita.

Antes de cada llamada a Claude, este modulo:
  1. Identifica al usuario (auth por WhatsApp)
  2. Carga los ultimos 8 mensajes (historial conversacional)
  3. Genera el system prompt segun el rol
  4. Selecciona los tools permitidos para el rol
  5. Arma el array de messages para Claude

Sin esto, Claude no sabria con quien habla ni que paso antes.
"""

import asyncpg
from datetime import datetime
import pytz

from app.prompts.system_prompts import PROMPT_GENERATORS
from app.tools.definitions import get_tools_por_rol
from app.utils.phone import extraer_digitos

LIMA_TZ = pytz.timezone("America/Lima")


async def identificar_usuario(conn: asyncpg.Connection, whatsapp: str) -> dict | None:
    """
    Busca al usuario por numero de WhatsApp.

    Flujo:
      1. Buscar en usuarios_autorizados (PM, CEO, autorizados)
      2. Si no esta ahi, buscar en desarrolladores por whatsapp
         → entra automaticamente con rol 'desarrollador'

    Retorna sus datos o None si no esta en ninguna tabla.
    """
    # Extraer ultimos 9 digitos para busqueda tolerante
    digitos = extraer_digitos(whatsapp)

    # 1. Buscar en usuarios_autorizados (match exacto o por ultimos 9 digitos)
    row = await conn.fetchrow(
        """SELECT * FROM usuarios_autorizados
           WHERE activo = TRUE
           AND (whatsapp = $1 OR RIGHT(REGEXP_REPLACE(whatsapp, '[^0-9]', '', 'g'), 9) = $2)""",
        whatsapp, digitos
    )
    if row:
        return dict(row)

    # 2. Si no esta, buscar en desarrolladores (numero solo vive ahi)
    dev = await conn.fetchrow(
        """SELECT * FROM desarrolladores
           WHERE disponible = TRUE
           AND (whatsapp = $1 OR RIGHT(REGEXP_REPLACE(whatsapp, '[^0-9]', '', 'g'), 9) = $2)""",
        whatsapp, digitos
    )
    if dev:
        print(f"  🔧 Dev {dev['nombre_completo']} autenticado via desarrolladores")
        return {
            "id": dev["id"],
            "whatsapp": dev["whatsapp"],
            "nombre": dev["nombre_completo"],
            "rol": "desarrollador",
            "desarrollador_id": dev["id"],
            "activo": True,
            "puede_reportar": True,
            "puede_gestionar": False,
        }

    return None


async def cargar_historial(conn: asyncpg.Connection, whatsapp: str, limite: int = 6) -> list[dict]:
    """
    Carga los ultimos N mensajes del usuario (entrantes y salientes).
    Estos se pasan a Claude como contexto conversacional.

    Sin historial, Claude no entiende referencias como "eso" o "el anterior".
    """
    rows = await conn.fetch(
        """SELECT direccion, contenido, tipo_contenido, created_at
           FROM mensajes_conversacion
           WHERE whatsapp = $1
           ORDER BY created_at DESC
           LIMIT $2""",
        whatsapp, limite
    )
    # Invertir para que el mas antiguo este primero (orden cronologico)
    rows = list(reversed(rows))

    messages = []
    for row in rows:
        role = "user" if row["direccion"] == "entrante" else "assistant"
        contenido = row["contenido"]

        # Si es audio, prefijear para que Claude sepa que fue un audio
        if row["tipo_contenido"] == "audio":
            contenido = f"[Audio transcrito]: {contenido}"
        elif row["tipo_contenido"] == "imagen":
            contenido = f"[Imagen enviada]: {contenido}" if contenido else "[Imagen sin texto]"

        messages.append({"role": role, "content": contenido})

    return messages


async def construir_contexto(
    conn: asyncpg.Connection,
    whatsapp: str,
    mensaje_actual: str,
    tipo_contenido: str = "texto"
) -> dict | None:
    """
    Construye TODO el contexto necesario para una llamada a Claude.

    Retorna:
    {
        "usuario": {...},           # datos del usuario
        "system_prompt": "...",     # prompt personalizado por rol
        "messages": [...],          # historial + mensaje actual
        "tools": [...],             # tools permitidos para el rol
    }
    o None si el usuario no esta autorizado.
    """
    # 1. Identificar usuario
    usuario = await identificar_usuario(conn, whatsapp)
    if not usuario:
        return None

    # 2. Cargar historial de conversacion
    historial = await cargar_historial(conn, whatsapp)

    # 3. Agregar mensaje actual al final
    contenido_actual = mensaje_actual
    if tipo_contenido == "audio":
        contenido_actual = f"[Audio transcrito]: {mensaje_actual}"
    elif tipo_contenido == "imagen":
        contenido_actual = f"[Imagen enviada]: {mensaje_actual}" if mensaje_actual else "[Imagen sin texto]"

    historial.append({"role": "user", "content": contenido_actual})

    # 4. Generar system prompt segun rol
    generador = PROMPT_GENERATORS.get(usuario["rol"], PROMPT_GENERATORS["autorizado"])

    # Contexto basico del equipo (se puede enriquecer)
    fecha_actual = datetime.now(LIMA_TZ).strftime("%A %d/%m/%Y %H:%M")

    prompt = generador(nombre_usuario=usuario["nombre"])
    # Reemplazar placeholder de fecha
    prompt = prompt.replace("{{fecha_actual}}", fecha_actual)

    # 5. Obtener tools permitidos para el rol
    tools = get_tools_por_rol(usuario["rol"])

    return {
        "usuario": usuario,
        "system_prompt": prompt,
        "messages": historial,
        "tools": tools,
    }
