"""
Emergencia — Asignacion inmediata de bugs criticos.

Cuando se crea un Bug Critico o Solicitud Bloqueante:
  1. Busca al Bug Guard activo
  2. Le asigna el item inmediatamente
  3. Notifica al Bug Guard, PM y CEO (si cliente Gold/Platinum)

NO es una tarea programada — se llama directamente desde el bot.
"""

import asyncpg
from app.config.settings import settings
from app.services.kapso import kapso_service


async def asignar_emergencia(conn: asyncpg.Connection, item_id, item_codigo: str, item_titulo: str, cliente_nombre: str = None):
    """
    Asigna un item urgente al Bug Guard activo.

    Args:
        conn: Conexion a PostgreSQL
        item_id: UUID del backlog_item
        item_codigo: BK-XXXX
        item_titulo: Titulo del item
        cliente_nombre: Nombre del cliente (para notificacion al CEO)
    """
    # 1. Buscar Bug Guard activo
    bug_guard = await conn.fetchrow(
        """SELECT * FROM desarrolladores
           WHERE bug_guard_semana_actual = TRUE AND disponible = TRUE
           LIMIT 1"""
    )

    if not bug_guard:
        # Sin Bug Guard → notificar al PM para asignacion manual
        if settings.WHATSAPP_PM:
            await kapso_service.enviar_texto_seguro(
                settings.WHATSAPP_PM,
                f"🚨 *Bug critico sin Bug Guard*\n"
                f"[{item_codigo}] {item_titulo}\n"
                f"No hay Bug Guard disponible. Requiere asignacion manual."
            )
        return

    # 2. Asignar al Bug Guard
    await conn.execute(
        """UPDATE backlog_items SET
            dev_id = $1,
            fecha_asignacion = NOW()
           WHERE id = $2""",
        bug_guard["id"], item_id
    )

    # 3. Notificar al Bug Guard
    if bug_guard.get("whatsapp"):
        try:
            await kapso_service.enviar_texto_seguro(
                bug_guard["whatsapp"],
                f"🚨 *BUG CRITICO ASIGNADO*\n"
                f"[{item_codigo}] {item_titulo}\n"
                f"Tienes 1 hora para responder.\n"
                f"Actualiza estado cuando empieces."
            )
        except Exception as e:
            print(f"  ⚠ No se pudo notificar al Bug Guard: {e}")

    # 4. Notificar al PM
    if settings.WHATSAPP_PM:
        try:
            await kapso_service.enviar_texto_seguro(
                settings.WHATSAPP_PM,
                f"🚨 [{item_codigo}] asignado a {bug_guard['nombre_completo']} (Bug Guard)\n"
                f"{item_titulo}"
            )
        except Exception as e:
            print(f"  ⚠ No se pudo notificar al PM: {e}")

    # 5. Log
    await conn.execute(
        """INSERT INTO auditoria_log (origen, accion, backlog_item_id, desarrollador_id, detalle, resultado)
           VALUES ('emergencia', 'asignacion_realizada', $1, $2, $3, 'Exito')""",
        item_id, bug_guard["id"],
        f"Emergencia: {item_codigo} asignado a {bug_guard['nombre_completo']}"
    )

    print(f"   🚨 Emergencia: {item_codigo} → {bug_guard['nombre_completo']}")
