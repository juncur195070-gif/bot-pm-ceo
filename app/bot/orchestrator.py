"""
Orchestrator — Flujo principal del bot.

Coordina todo el procesamiento de un mensaje:
  Kapso → dedup → auth → historial → contexto → Claude → tools → respuesta → WhatsApp

Es el archivo que conecta todos los servicios y modulos.
"""

import asyncio
import asyncpg
from app.config.database import get_pool
from app.config.settings import settings
from app.services.kapso import kapso_service
from app.bot.context_builder import construir_contexto, identificar_usuario
from app.bot.agent_loop import ejecutar_loop
from app.db.queries import auditoria as q_audit
from app.utils.phone import normalizar as normalizar_telefono

# Lock por usuario — evita procesar 2 mensajes del mismo usuario en paralelo
# Si el PM envia 2 mensajes rapido, el segundo espera a que termine el primero
_user_locks: dict[str, asyncio.Lock] = {}


async def procesar_mensaje(payload: dict, idempotency_key: str):
    """
    Flujo principal completo de procesamiento de un mensaje.

    Se ejecuta como BackgroundTask de FastAPI (no bloquea el webhook).
    Usa lock por usuario para evitar procesamiento paralelo (previene duplicados).

    Args:
        payload: Payload crudo de Kapso
        idempotency_key: UUID de Kapso para deduplicacion
    """
    # Obtener whatsapp del usuario para el lock
    try:
        msg_data = kapso_service.extraer_mensaje(payload)
        user_phone = msg_data["from"]
    except Exception:
        user_phone = "unknown"

    # Crear lock si no existe para este usuario
    if user_phone not in _user_locks:
        _user_locks[user_phone] = asyncio.Lock()

    # Procesar con lock — si hay otro mensaje del mismo usuario, espera
    async with _user_locks[user_phone]:
        pool = get_pool()
        async with pool.acquire() as conn:
            try:
                await _procesar(conn, payload, idempotency_key)
            except Exception as e:
                print(f"❌ Error procesando mensaje: {e}")
                # Notificar al usuario con mensaje apropiado
                try:
                    error_msg = str(e).lower()
                    if "rate limit" in error_msg:
                        texto_error = "Estoy procesando muchos mensajes. Espera unos segundos e intenta de nuevo."
                    else:
                        texto_error = "Tuve un error procesando tu mensaje. Intenta de nuevo."
                    await kapso_service.enviar_texto_seguro(msg_data["from"], texto_error)
                except Exception as notify_err:
                    print(f"  ⚠ No se pudo notificar error al usuario: {notify_err}")

                # Registrar error en auditoria
                await q_audit.registrar_accion(
                    conn,
                    origen="bot",
                    accion="error_detectado",
                    detalle=str(e),
                    resultado="Error",
                    error_detalle=str(e)
                )


async def _procesar(conn: asyncpg.Connection, payload: dict, idempotency_key: str):
    """Logica principal (separada para manejo de errores limpio)."""

    # ── 1. Extraer datos del mensaje ──
    msg = kapso_service.extraer_mensaje(payload)
    whatsapp = normalizar_telefono(msg["from"])
    contenido = msg["contenido"]
    tipo_contenido = msg["type"]
    media_url = msg.get("media_url")

    # Pre-procesar audio transcrito: convertir números en texto a dígitos
    # "nueve, uno, seis" → "916" para que Claude entienda WhatsApp numbers
    if tipo_contenido == "audio" and contenido:
        import re
        _NUMS = {"cero":"0","uno":"1","dos":"2","tres":"3","cuatro":"4","cinco":"5",
                 "seis":"6","siete":"7","ocho":"8","nueve":"9"}
        def _reemplazar_nums(texto):
            for palabra, digito in _NUMS.items():
                texto = re.sub(rf'\b{palabra}\b', digito, texto, flags=re.IGNORECASE)
            # Limpiar separadores entre digitos: "9, 1, 6" → "916"
            texto = re.sub(r'(\d),?\s*(?=\d)', r'\1', texto)
            return texto
        contenido = _reemplazar_nums(contenido)

    print(f"📩 Mensaje de {whatsapp} ({tipo_contenido}): {contenido[:100]}...")

    # Si es imagen sin caption, agregar contexto para que Claude sepa
    if not contenido and tipo_contenido == "imagen":
        contenido = "[Imagen enviada sin texto]"
    # Si es imagen CON caption, agregar la URL al contenido para Claude
    if tipo_contenido == "imagen" and media_url:
        contenido = f"{contenido}\n[URL de la imagen: {media_url}]"

    if not contenido:
        print("  ⚠ Mensaje vacio, ignorando")
        return

    # ── 2. Deduplicacion ──
    ya_procesado = await conn.fetchval(
        "SELECT 1 FROM mensajes_procesados WHERE idempotency_key = $1",
        idempotency_key
    )
    if ya_procesado:
        print("  ⚠ Mensaje duplicado, ignorando")
        return

    # Marcar como procesado
    await conn.execute(
        "INSERT INTO mensajes_procesados (idempotency_key) VALUES ($1) ON CONFLICT DO NOTHING",
        idempotency_key
    )

    # ── 3. Autenticar usuario ──
    usuario = await identificar_usuario(conn, whatsapp)
    if not usuario:
        print(f"  🚫 Usuario no autorizado: {whatsapp}")
        await kapso_service.enviar_texto_seguro(
            msg["from"],
            "No tienes acceso a este sistema. Contacta al administrador."
        )
        return

    print(f"  👤 Usuario: {usuario['nombre']} ({usuario['rol']})")

    # ── 4. Guardar mensaje entrante ──
    # Para devs autenticados via desarrolladores, usuario_id no existe en usuarios_autorizados
    # Usamos NULL en ese caso — whatsapp identifica al usuario
    usuario_id_fk = None
    if usuario.get("rol") != "desarrollador":
        usuario_id_fk = usuario["id"]

    await conn.execute(
        """INSERT INTO mensajes_conversacion
           (usuario_id, whatsapp, direccion, contenido, tipo_contenido, media_url, kapso_message_id)
           VALUES ($1, $2, 'entrante', $3, $4, $5, $6)""",
        usuario_id_fk, whatsapp, contenido, tipo_contenido, media_url, msg.get("message_id")
    )

    # ── 4b. Si es imagen, guardar la URL para que los tools la usen ──
    # Guardamos las URLs de imagenes recientes del usuario para adjuntar a items
    if media_url:
        await conn.execute(
            """UPDATE whatsapp_sesiones SET
                contexto_json = jsonb_set(
                    COALESCE(contexto_json, '{}'),
                    '{ultima_media_url}',
                    to_jsonb($1::text)
                ),
                updated_at = NOW()
               WHERE whatsapp = $2""",
            media_url, whatsapp
        )
        # Si no existe sesion, crearla
        await conn.execute(
            """INSERT INTO whatsapp_sesiones (whatsapp, usuario_id, contexto_json)
               VALUES ($1, $2, jsonb_build_object('ultima_media_url', $3::text))
               ON CONFLICT (whatsapp) DO UPDATE SET
                contexto_json = jsonb_set(
                    COALESCE(whatsapp_sesiones.contexto_json, '{}'),
                    '{ultima_media_url}',
                    to_jsonb($3::text)
                )""",
            whatsapp, usuario["id"], media_url
        )

    # ── 5. Construir contexto para Claude ──
    contexto = await construir_contexto(conn, whatsapp, contenido, tipo_contenido)
    if not contexto:
        print("  ❌ No se pudo construir contexto")
        return

    # ── 6. Ejecutar loop agentico ──
    print(f"  🤖 Llamando a Claude con {len(contexto['tools'])} tools...")
    resultado = await ejecutar_loop(
        system_prompt=contexto["system_prompt"],
        messages=contexto["messages"],
        tools=contexto["tools"],
        conn=conn,
        usuario=usuario,
    )

    respuesta = resultado["respuesta"]
    print(f"  💬 Respuesta ({resultado['iteraciones']} iter, {resultado['modelo_usado']}): {respuesta[:100]}...")

    # ── 6b. ANTI-MENTIRA: Si CONFIRMA accion sin usar tool → reintentar ──
    # Solo detecta cuando Claude dice "YO hice algo" (primera persona + confirmacion)
    # No se activa cuando Claude describe estado ("está asignado", "hay varios")
    no_uso_tools = len(resultado["tools_usados"]) == 0
    necesita_reintento = False

    if no_uso_tools and resultado.get("error") is None:
        respuesta_lower = respuesta.lower()

        # Excluir preguntas — Claude pide mas datos, no miente
        es_pregunta = "?" in respuesta and any(p in respuesta_lower for p in [
            "necesito", "falta", "cuál", "cual", "qué", "que nivel", "que skills",
            "dame", "dime", "confirma", "puedes"
        ])

        if not es_pregunta:
            # Detectar SOLO confirmaciones en primera persona (Claude dice "yo hice")
            # Patron: verbo de accion conjugado + emoji de confirmacion
            acciones_primera_persona = [
                "he creado", "he actualizado", "he asignado", "he registrado",
                "he cambiado", "he establecido", "he agregado", "he eliminado",
                "he modificado", "he guardado", "he configurado",
                "fue creado", "fue asignado", "fue actualizado", "fue registrado",
                "queda creado", "queda asignado", "queda actualizado",
            ]
            confirmo_accion = any(p in respuesta_lower for p in acciones_primera_persona)

            # Tambien detectar emoji ✅ + verbo participio (patron comun de mentira)
            tiene_check = "✅" in respuesta
            tiene_participio = any(p in respuesta_lower for p in [
                "creado", "asignado", "actualizado", "registrado", "guardado"
            ])
            # ✅ + participio SIN tool = muy probable mentira
            confirmo_con_emoji = tiene_check and tiene_participio

            # Caso 3: Acepto instruccion sin actuar ("entendido", "anotado", "de acuerdo")
            acepto_sin_actuar = any(p in respuesta_lower for p in [
                "entendido", "anotado", "de acuerdo", "perfecto,", "ok,", "listo,",
                "lo tengo", "lo anoto", "lo registro", "tomado en cuenta",
            ])
            # Solo si el mensaje del usuario era una instruccion (no una pregunta)
            contenido_lower = contenido.lower()
            era_instruccion = any(p in contenido_lower for p in [
                "pon a", "asigna", "crea", "cambia", "quita", "elimina",
                "es el bug guard", "bug guard", "desasigna", "actualiza",
            ])

            if confirmo_accion or confirmo_con_emoji or (acepto_sin_actuar and era_instruccion):
                necesita_reintento = True

    if necesita_reintento:
        print("  ⚠ Anti-mentira: reintentando con modelo superior...")
        # Reintento con:
        # 1. Contexto limpio (sin historial que contamina)
        # 2. Instruccion explicita de que NADA se guardo
        # 3. Modelo superior (Sonnet) que entiende mejor las correcciones
        instruccion_reintento = (
            f"El usuario dice: '{contenido}'\n\n"
            f"IMPORTANTE: No se ejecuto ningun cambio en la base de datos. "
            f"DEBES usar el tool correspondiente AHORA para ejecutar lo que el usuario pide. "
            f"Para reasignar una tarea: usa asignar_tarea con codigo_o_busqueda y dev_nombre. "
            f"Para desasignar: usa asignar_tarea con desasignar=true. "
            f"Para crear: usa crear_item. Para Bug Guard: usa reasignar_bug_guard. "
            f"USA EL TOOL AHORA. No respondas sin ejecutar."
        )
        resultado2 = await ejecutar_loop(
            system_prompt=contexto["system_prompt"],
            messages=[
                {"role": "user", "content": instruccion_reintento}
            ],
            tools=contexto["tools"],
            conn=conn,
            usuario=usuario,
            model_override=settings.OPENAI_MODEL_FALLBACK if settings.AI_PROVIDER == "openai" else "claude-sonnet-4-5-20241022",
        )
        if len(resultado2["tools_usados"]) > 0:
            respuesta = resultado2["respuesta"]
            resultado = resultado2
            print(f"  ✅ Reintento exitoso: {resultado2['tools_usados']}")
        else:
            respuesta = "No pude ejecutar la accion. Intenta de nuevo con mas detalle."
            resultado["tools_usados"] = []

    # ── 7. Guardar respuesta saliente ──
    await conn.execute(
        """INSERT INTO mensajes_conversacion
           (usuario_id, whatsapp, direccion, contenido, tipo_contenido, tools_usados)
           VALUES ($1, $2, 'saliente', $3, 'texto', $4)""",
        usuario_id_fk, whatsapp, respuesta, resultado["tools_usados"]
    )

    # ── 8. Enviar respuesta por WhatsApp (PRIMERO — antes del sync) ──
    await kapso_service.enviar_texto_seguro(msg["from"], respuesta)

    # ── 9. Registrar en auditoria ──
    await q_audit.registrar_accion(
        conn,
        origen="bot",
        accion="mensaje_procesado",
        usuario_id=usuario_id_fk,
        detalle=f"Tipo: {tipo_contenido}, Tools: {resultado['tools_usados']}, Iter: {resultado['iteraciones']}, Dev: {usuario['nombre']}",
        metadata={
            "modelo": resultado["modelo_usado"],
            "tools": resultado["tools_usados"],
            "iteraciones": resultado["iteraciones"],
        }
    )

    # ── 10. Auto-adjuntar imagenes (post-respuesta, no bloquea al usuario) ──
    # Solo si Claude NO uso adjuntar_imagen (evita duplicar en 2 items)
    if media_url and tipo_contenido == "imagen" and "adjuntar_imagen" not in resultado["tools_usados"]:
        import asyncio
        asyncio.create_task(_auto_adjuntar_imagen_bg(usuario["id"], media_url, resultado["tools_usados"]))

    print(f"  ✅ Mensaje procesado y respondido")


async def _auto_adjuntar_imagen_bg(usuario_id, media_url: str, tools_usados: list):
    """Version background — obtiene su propia conexion del pool."""
    try:
        from app.config.database import get_pool
        pool = get_pool()
        async with pool.acquire() as conn:
            await _auto_adjuntar_imagen(conn, usuario_id, media_url, tools_usados)
    except Exception as e:
        print(f"  ⚠ Background adjuntar failed: {e}")


async def _auto_adjuntar_imagen(conn: asyncpg.Connection, usuario_id, media_url: str, tools_usados: list):
    """
    Auto-adjunta una imagen al item mas reciente del usuario.

    Estrategia:
    1. Si crear_item se uso en este turno → adjuntar al item recien creado
    2. Si no → adjuntar al ultimo item activo del usuario
    """
    try:
        if "crear_item" in tools_usados:
            # Adjuntar al item mas reciente creado por este usuario
            item = await conn.fetchrow(
                """SELECT id, codigo, adjuntos_urls, airtable_record_id FROM backlog_items
                   WHERE reportado_por_id = $1
                   ORDER BY created_at DESC LIMIT 1""",
                usuario_id
            )
        else:
            # Adjuntar al ultimo item mencionado en la conversacion
            msg_con_item = await conn.fetchrow(
                """SELECT backlog_item_id FROM mensajes_conversacion
                   WHERE usuario_id = $1 AND backlog_item_id IS NOT NULL
                   ORDER BY created_at DESC LIMIT 1""",
                usuario_id
            )
            if msg_con_item and msg_con_item["backlog_item_id"]:
                item = await conn.fetchrow(
                    "SELECT id, codigo, adjuntos_urls, airtable_record_id FROM backlog_items WHERE id = $1",
                    msg_con_item["backlog_item_id"]
                )
            else:
                # Ultimo item activo del usuario
                item = await conn.fetchrow(
                    """SELECT id, codigo, adjuntos_urls, airtable_record_id FROM backlog_items
                       WHERE reportado_por_id = $1
                       AND estado NOT IN ('Desplegado','Cancelado','Archivado')
                       ORDER BY created_at DESC LIMIT 1""",
                    usuario_id
                )

        if not item:
            return

        # Agregar URL al array de adjuntos
        adjuntos = list(item["adjuntos_urls"] or [])
        if media_url not in adjuntos:
            adjuntos.append(media_url)
            await conn.execute(
                "UPDATE backlog_items SET adjuntos_urls = $1 WHERE id = $2",
                adjuntos, item["id"]
            )

            # Sync a Airtable (lee item completo de DB para no borrar campos)
            from app.services.airtable_sync import airtable_sync
            from app.db.queries.backlog import obtener_item
            item_completo = await obtener_item(conn, item["codigo"])
            if item_completo:
                record_id = await airtable_sync.sync_backlog_item(dict(item_completo))
                if record_id and not item_completo.get("airtable_record_id"):
                    await conn.execute(
                        "UPDATE backlog_items SET airtable_record_id = $1 WHERE id = $2",
                        record_id, item["id"]
                    )

            print(f"  📎 Imagen adjuntada a {item['codigo']}")

    except Exception as e:
        print(f"  ⚠ Error auto-adjuntando imagen: {e}")
