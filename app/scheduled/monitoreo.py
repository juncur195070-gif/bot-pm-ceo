"""
Monitoreo — Alertas diarias L-V 9am.

Detecta:
  1. Deadlines en <= 3 dias
  2. Deadlines vencidos
  3. Tareas estancadas >4 dias en desarrollo
  4. Backlog olvidado >45 dias

Envia alertas al PM (y al dev si aplica).
"""

from datetime import datetime
import pytz

from app.config.database import get_pool
from app.config.settings import settings
from app.services.kapso import kapso_service

LIMA_TZ = pytz.timezone("America/Lima")


async def ejecutar_monitoreo():
    """Tarea programada: alertas diarias L-V 9am."""
    print("🔔 Iniciando monitoreo de alertas...")
    pool = get_pool()
    alertas = []

    async with pool.acquire() as conn:
        # 1. Deadlines proximos (<= 3 dias)
        rows = await conn.fetch(
            """SELECT bi.codigo, bi.titulo, bi.tipo,
                      d.nombre_completo as dev_nombre,
                      bi.deadline_interno, bi.deadline_interno - CURRENT_DATE as dias,
                      d.whatsapp as dev_whatsapp
               FROM backlog_items bi
               LEFT JOIN desarrolladores d ON bi.dev_id = d.id
               WHERE bi.deadline_interno IS NOT NULL
               AND bi.deadline_interno <= CURRENT_DATE + 3
               AND bi.deadline_interno >= CURRENT_DATE
               AND bi.estado NOT IN ('Desplegado','Cancelado','Archivado')
               ORDER BY bi.deadline_interno ASC"""
        )
        for r in rows:
            dias = r["dias"] if r["dias"] is not None else 0
            emoji = "🚨" if dias <= 1 else "⚠️"
            alertas.append({
                "tipo": "deadline",
                "msg_pm": f"{emoji} [{r['codigo']}] {r['titulo']}\n   Vence en {dias} dia(s) — {r['dev_nombre'] or 'SIN ASIGNAR'}",
                "msg_dev": f"{emoji} *Deadline proximo*\n[{r['codigo']}] {r['titulo']}\nQuedan *{dias} dia(s)*" if r["dev_whatsapp"] else None,
                "dev_wa": r["dev_whatsapp"],
            })

        # 2. Deadlines vencidos
        rows = await conn.fetch(
            """SELECT bi.codigo, bi.titulo, d.nombre_completo as dev_nombre, bi.deadline_interno
               FROM backlog_items bi
               LEFT JOIN desarrolladores d ON bi.dev_id = d.id
               WHERE bi.deadline_interno < CURRENT_DATE
               AND bi.estado NOT IN ('Desplegado','Cancelado','Archivado')"""
        )
        for r in rows:
            alertas.append({
                "tipo": "vencido",
                "msg_pm": f"🚨 VENCIDO [{r['codigo']}] {r['titulo']} — {r['dev_nombre'] or 'SIN ASIGNAR'}",
                "msg_dev": None, "dev_wa": None,
            })

        # 3. Tareas estancadas >4 dias
        rows = await conn.fetch(
            """SELECT bi.codigo, bi.titulo, d.nombre_completo as dev_nombre,
                      EXTRACT(DAY FROM NOW() - bi.fecha_inicio_desarrollo) as dias_dev
               FROM backlog_items bi
               LEFT JOIN desarrolladores d ON bi.dev_id = d.id
               WHERE bi.estado = 'En Desarrollo'
               AND bi.fecha_inicio_desarrollo < NOW() - INTERVAL '4 days'"""
        )
        for r in rows:
            alertas.append({
                "tipo": "estancada",
                "msg_pm": f"🔴 Estancada [{r['codigo']}] {r['titulo']}\n   {int(r['dias_dev'] or 0)} dias en desarrollo — {r['dev_nombre']}",
                "msg_dev": None, "dev_wa": None,
            })

        # 4. Backlog olvidado >45 dias
        rows = await conn.fetch(
            """SELECT codigo, titulo,
                      EXTRACT(DAY FROM NOW() - created_at) as dias
               FROM backlog_items
               WHERE estado = 'Backlog'
               AND created_at < NOW() - INTERVAL '45 days'"""
        )
        for r in rows:
            alertas.append({
                "tipo": "olvidado",
                "msg_pm": f"📦 Olvidado [{r['codigo']}] {r['titulo']} — {int(r['dias'] or 0)} dias sin atender",
                "msg_dev": None, "dev_wa": None,
            })

        # 5. Renovaciones proximas (clientes con fecha_renovacion)
        rows = await conn.fetch(
            """SELECT codigo, nombre_clinica, mrr_mensual, tamano,
                      fecha_renovacion, fecha_renovacion - CURRENT_DATE as dias,
                      COALESCE(renovacion_estado, 'pendiente') as renovacion_estado
               FROM clientes
               WHERE fecha_renovacion IS NOT NULL
               AND fecha_renovacion <= CURRENT_DATE + 30
               AND estado_cliente = 'Activo'
               AND COALESCE(renovacion_estado, 'pendiente') NOT IN ('renovado', 'perdido')
               ORDER BY fecha_renovacion ASC"""
        )
        for r in rows:
            dias = r["dias"] if r["dias"] is not None else 0
            estado_ren = r["renovacion_estado"]
            mrr = float(r["mrr_mensual"] or 0)

            if dias < 0:
                emoji = "🚨"
                texto = f"VENCIDA hace {abs(dias)} dia(s)"
            elif dias <= 3:
                emoji = "🚨"
                texto = f"en {dias} dia(s)"
            elif dias <= 7:
                emoji = "⚠️"
                texto = f"en {dias} dia(s)"
            else:
                emoji = "📋"
                texto = f"en {dias} dia(s)"

            estado_txt = " (ya contactado)" if estado_ren == "contactado" else ""
            alertas.append({
                "tipo": "renovacion",
                "msg_pm": f"{emoji} *Renovacion {texto}*: {r['nombre_clinica']} [{r['codigo']}]\n   MRR: S/{mrr:.0f} | {r['tamano']}{estado_txt}",
                "msg_dev": None, "dev_wa": None,
            })

        # Enviar alertas
        if not alertas:
            print("   ✅ Sin alertas hoy")
            return

        # Agrupar para el PM
        fecha = datetime.now(LIMA_TZ).strftime("%d/%m/%Y")
        msg_pm = f"🔔 *Alertas — {fecha}*\n\n"
        for a in alertas:
            msg_pm += a["msg_pm"] + "\n\n"

        if settings.WHATSAPP_PM:
            await kapso_service.enviar_texto_seguro(settings.WHATSAPP_PM, msg_pm.strip())

        # Alertas individuales a devs
        for a in alertas:
            if a["msg_dev"] and a["dev_wa"]:
                await kapso_service.enviar_texto_seguro(a["dev_wa"], a["msg_dev"])

        # Log
        await conn.execute(
            """INSERT INTO auditoria_log (origen, accion, detalle, resultado)
               VALUES ('monitoreo', 'alertas_enviadas', $1, 'Exito')""",
            f"{len(alertas)} alertas detectadas"
        )

        print(f"   🔔 {len(alertas)} alertas enviadas")
