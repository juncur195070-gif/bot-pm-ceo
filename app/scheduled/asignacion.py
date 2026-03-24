"""
Asignacion Semanal — Lunes 8am.

1. Selecciona Bug Guard (rotacion equitativa)
2. Matching de tareas a devs (skills + capacidad + WIP)
3. Notifica a cada dev sus tareas por WhatsApp
4. Resumen al PM

El Bug Guard recibe 60% tiempo para bugs, 40% para sprint.
"""

from datetime import datetime, date, timedelta
import pytz

from app.config.database import get_pool
from app.config.settings import settings
from app.services.kapso import kapso_service

LIMA_TZ = pytz.timezone("America/Lima")

# Que tipos de tarea puede recibir cada nivel
NIVEL_PERMITIDO = {
    "Junior": ["Bug Menor", "Deuda Tecnica Interna", "Deuda Tecnica Visible", "Requisito Lead"],
    "Mid": ["Bug Menor", "Bug Importante", "Solicitud Mejora", "Solicitud Bloqueante",
             "Deuda Tecnica Interna", "Deuda Tecnica Visible", "Requisito Lead"],
    "Senior": ["Bug Menor", "Bug Importante", "Bug Critico", "Solicitud Mejora",
               "Solicitud Bloqueante", "Deuda Tecnica Interna", "Deuda Tecnica Visible",
               "Requisito Lead", "Roadmap"],
}


async def ejecutar_asignacion():
    """Tarea programada: asignacion semanal cada lunes 8am."""
    print("📋 Iniciando asignacion semanal...")
    pool = get_pool()

    async with pool.acquire() as conn:
        # ═══ PASO 0: SELECCIONAR BUG GUARD ═══
        bug_guard = await _seleccionar_bug_guard(conn)

        # ═══ PASO 1: LEER TOP BACKLOG SIN ASIGNAR ═══
        items = await conn.fetch(
            """SELECT * FROM backlog_items
               WHERE estado = 'Backlog' AND dev_id IS NULL
               ORDER BY posicion_backlog ASC
               LIMIT 40"""
        )

        if not items:
            print("   No hay items en backlog para asignar")
            return

        # ═══ PASO 2: LEER DEVS DISPONIBLES ═══
        devs_raw = await conn.fetch(
            """SELECT d.*,
                COALESCE(t.horas_usadas, 0) as horas_usadas,
                COALESCE(t.tareas_activas, 0) as wip_actual
               FROM desarrolladores d
               LEFT JOIN (
                   SELECT dev_id, SUM(horas_esfuerzo) as horas_usadas, COUNT(*) as tareas_activas
                   FROM backlog_items
                   WHERE estado IN ('En Analisis','En Desarrollo','En QA') AND dev_id IS NOT NULL
                   GROUP BY dev_id
               ) t ON t.dev_id = d.id
               WHERE d.disponible = TRUE"""
        )

        # Construir mapa de capacidad basado en HORAS
        from app.config.settings import settings
        FACTOR_CARGA = {
            "Junior": settings.CARGA_JUNIOR,
            "Mid": settings.CARGA_MID,
            "Senior": settings.CARGA_SENIOR,
        }

        devs = []
        for d in devs_raw:
            dev = dict(d)
            factor = FACTOR_CARGA.get(dev["nivel"], 0.80)
            horas_disponibles = round(dev["horas_sprint_semana"] * factor)
            horas_usadas = float(dev["horas_usadas"] or 0)
            dev["horas_disponibles"] = horas_disponibles
            dev["horas_restantes"] = max(0, horas_disponibles - horas_usadas)
            dev["porcentaje_carga"] = round((horas_usadas / horas_disponibles * 100) if horas_disponibles > 0 else 0)
            dev["tareas_asignadas"] = []
            devs.append(dev)

        # ═══ PASO 3: MATCHING BASADO EN HORAS ═══
        asignaciones = []
        for item in items:
            item = dict(item)
            horas_item = item.get("horas_esfuerzo") or 4
            skills_req = item.get("skill_requerido") or []

            candidatos = []
            for dev in devs:
                # Bug Guard solo recibe bugs criticos
                if dev["bug_guard_semana_actual"] and item["tipo"] not in ("Bug Critico", "Solicitud Bloqueante"):
                    continue
                # Verificar capacidad en HORAS (no WIP count)
                if dev["horas_restantes"] < horas_item:
                    continue
                # Verificar nivel permite este tipo de tarea
                permitidos = NIVEL_PERMITIDO.get(dev["nivel"], [])
                if item["tipo"] not in permitidos:
                    continue
                # Verificar skills
                if skills_req:
                    dev_skills = dev.get("skills") or []
                    if not any(s in dev_skills for s in skills_req):
                        continue
                candidatos.append(dev)

            if not candidatos:
                continue

            # Seleccionar el con mas horas libres (balanceo de carga)
            candidatos.sort(key=lambda d: d["horas_restantes"], reverse=True)
            mejor = candidatos[0]

            asignaciones.append({
                "item_id": item["id"],
                "item_codigo": item["codigo"],
                "item_titulo": item["titulo"],
                "item_tipo": item["tipo"],
                "item_horas": horas_item,
                "dev_id": mejor["id"],
                "dev_codigo": mejor["codigo"],
                "dev_nombre": mejor["nombre_completo"],
                "dev_whatsapp": mejor["whatsapp"],
            })

            # Actualizar capacidad en memoria para el siguiente item
            mejor["horas_restantes"] -= horas_item
            mejor["tareas_asignadas"].append(item["codigo"])

        # ═══ PASO 4: APLICAR ASIGNACIONES EN DB + SYNC AIRTABLE ═══
        from app.services.airtable_sync import airtable_sync

        semana = f"S{datetime.now(LIMA_TZ).isocalendar()[1]}-{datetime.now(LIMA_TZ).year}"
        for a in asignaciones:
            await conn.execute(
                """UPDATE backlog_items SET
                    dev_id = $1, dev_nombre = $2, estado = 'En Analisis',
                    fecha_asignacion = NOW(), sprint_semana = $3
                   WHERE id = $4""",
                a["dev_id"], a["dev_nombre"], semana, a["item_id"]
            )
            # Sync a Airtable
            item_full = await conn.fetchrow("SELECT * FROM backlog_items WHERE id = $1", a["item_id"])
            if item_full and item_full.get("airtable_record_id"):
                await airtable_sync.sync_backlog_item(dict(item_full))

        # ═══ PASO 5: NOTIFICAR DEVS ═══
        por_dev = {}
        for a in asignaciones:
            wa = a["dev_whatsapp"]
            if wa not in por_dev:
                por_dev[wa] = {"nombre": a["dev_nombre"], "tareas": []}
            por_dev[wa]["tareas"].append(a)

        fecha = datetime.now(LIMA_TZ).strftime("%A %d/%m")
        for wa, data in por_dev.items():
            lineas = []
            total_h = 0
            for i, t in enumerate(data["tareas"]):
                lineas.append(f"{i+1}. [{t['item_codigo']}] {t['item_titulo']}\n   {t['item_tipo']} | {t['item_horas']}h")
                total_h += t["item_horas"]
            msg = (
                f"📋 *Tus tareas — {fecha}*\n\n"
                f"Hola {data['nombre']} 👋\n\n"
                + "\n\n".join(lineas)
                + f"\n\n📊 Total: {total_h}h"
            )
            await kapso_service.enviar_texto_seguro(wa, msg)

        # ═══ PASO 6: NOTIFICAR PM ═══
        if settings.WHATSAPP_PM:
            resumen = f"📋 *Asignaciones — {fecha}*\n\n"
            if bug_guard:
                resumen += f"🛡 Bug Guard: {bug_guard['nombre_completo']}\n\n"
            for wa, data in por_dev.items():
                resumen += f"*{data['nombre']}:* {len(data['tareas'])} tareas\n"
            sin_asignar = len(items) - len(asignaciones)
            if sin_asignar > 0:
                resumen += f"\n⚠ {sin_asignar} items sin dev disponible"
            await kapso_service.enviar_texto_seguro(settings.WHATSAPP_PM, resumen)

        # Log
        await conn.execute(
            """INSERT INTO auditoria_log (origen, accion, detalle, resultado)
               VALUES ('asignacion', 'asignacion_realizada', $1, 'Exito')""",
            f"{len(asignaciones)} asignaciones, Bug Guard: {bug_guard['nombre_completo'] if bug_guard else 'ninguno'}"
        )

        print(f"   ✅ Asignacion completada: {len(asignaciones)} tareas asignadas")


async def _seleccionar_bug_guard(conn) -> dict | None:
    """
    Selecciona el Bug Guard de la semana con rotacion equitativa.
    El dev con menos semanas como Bug Guard es el elegido.
    No puede repetir semana consecutiva.
    """
    devs = await conn.fetch(
        """SELECT * FROM desarrolladores
           WHERE disponible = TRUE
           ORDER BY historial_semanas_bug_guard ASC, nombre_completo ASC"""
    )

    if not devs:
        return None

    hace_7_dias = date.today() - timedelta(days=7)
    for dev in devs:
        # Excluir si fue Bug Guard la semana pasada
        if dev["ultima_semana_bug_guard"] and dev["ultima_semana_bug_guard"] > hace_7_dias:
            continue

        # Seleccionar este dev
        await conn.execute(
            """UPDATE desarrolladores SET
                bug_guard_semana_actual = TRUE,
                ultima_semana_bug_guard = $1,
                historial_semanas_bug_guard = historial_semanas_bug_guard + 1
               WHERE id = $2""",
            date.today(), dev["id"]
        )

        # Registrar en historial
        semana = f"S{datetime.now(LIMA_TZ).isocalendar()[1]}-{datetime.now(LIMA_TZ).year}"
        await conn.execute(
            """INSERT INTO bug_guard_historial
               (semana_codigo, fecha_inicio_semana, dev_id, dev_nombre, horas_reservadas)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (semana_codigo) DO NOTHING""",
            semana, date.today(), dev["id"], dev["nombre_completo"],
            int(dev["horas_semana_base"] * 0.6)
        )

        # Notificar al Bug Guard
        msg = (
            f"🛡 *Eres el Bug Guard esta semana*\n\n"
            f"Hola {dev['nombre_completo']} 👋\n\n"
            f"📋 Tus SLAs:\n"
            f"• Bug Critico: responder en ≤ 1 hora\n"
            f"• Bug Importante: responder en ≤ 4 horas\n"
            f"• Solicitud Bloqueante: responder en ≤ 2 horas\n\n"
            f"⏰ Reserva bugs: {int(dev['horas_semana_base'] * 0.6)}h | Sprint: {dev['horas_sprint_semana']}h"
        )
        await kapso_service.enviar_texto_seguro(dev["whatsapp"], msg)

        print(f"   🛡 Bug Guard seleccionado: {dev['nombre_completo']}")
        return dict(dev)

    # Si todos fueron Bug Guard reciente, elegir el primero disponible
    if devs:
        dev = dict(devs[0])
        await conn.execute(
            "UPDATE desarrolladores SET bug_guard_semana_actual = TRUE WHERE id = $1",
            dev["id"]
        )
        return dev
    return None
