"""
Queries de metricas y dashboard.
Estas queries agregan datos de multiples tablas para generar KPIs.
"""

import asyncpg
from datetime import datetime, timedelta
import pytz

LIMA_TZ = pytz.timezone("America/Lima")


def _rango_periodo(periodo: str) -> tuple[datetime, datetime]:
    """Convierte 'esta_semana', 'este_mes', etc. a rango de fechas."""
    ahora = datetime.now(LIMA_TZ)
    if periodo == "esta_semana":
        inicio = ahora - timedelta(days=ahora.weekday())  # lunes
        inicio = inicio.replace(hour=0, minute=0, second=0)
    elif periodo == "este_mes":
        inicio = ahora.replace(day=1, hour=0, minute=0, second=0)
    elif periodo == "ultimos_7_dias":
        inicio = ahora - timedelta(days=7)
    elif periodo == "ultimos_30_dias":
        inicio = ahora - timedelta(days=30)
    else:
        inicio = ahora - timedelta(days=7)
    return inicio, ahora


async def dashboard_general(conn: asyncpg.Connection, periodo: str = "esta_semana") -> dict:
    """Dashboard completo con KPIs principales."""
    inicio, fin = _rango_periodo(periodo)

    # Items completados en el periodo
    completados = await conn.fetchval(
        """SELECT COUNT(*) FROM backlog_items
           WHERE estado = 'Desplegado' AND fecha_desplegado >= $1""",
        inicio
    )

    # Items en progreso
    en_progreso = await conn.fetchval(
        "SELECT COUNT(*) FROM backlog_items WHERE estado IN ('En Analisis','En Desarrollo','En QA')"
    )

    # Backlog pendiente
    backlog = await conn.fetchval(
        "SELECT COUNT(*) FROM backlog_items WHERE estado = 'Backlog'"
    )

    # Bugs criticos abiertos
    bugs_criticos = await conn.fetchval(
        "SELECT COUNT(*) FROM backlog_items WHERE tipo = 'Bug Critico' AND estado NOT IN ('Desplegado','Cancelado','Archivado')"
    )

    # SLA cumplido
    sla_total = await conn.fetchval(
        "SELECT COUNT(*) FROM backlog_items WHERE cumplio_sla IS NOT NULL AND fecha_desplegado >= $1",
        inicio
    )
    sla_cumplido = await conn.fetchval(
        "SELECT COUNT(*) FROM backlog_items WHERE cumplio_sla = TRUE AND fecha_desplegado >= $1",
        inicio
    )
    sla_pct = (sla_cumplido / sla_total * 100) if sla_total > 0 else 0

    # Lead time promedio
    lead_time = await conn.fetchval(
        """SELECT AVG(lead_time_horas) FROM backlog_items
           WHERE lead_time_horas IS NOT NULL AND fecha_desplegado >= $1""",
        inicio
    )

    # Items en riesgo (deadline en 3 dias o menos)
    riesgo_rows = await conn.fetch(
        """SELECT codigo, titulo, tipo, dev_nombre, deadline_interno,
                  deadline_interno - CURRENT_DATE as dias_restantes
           FROM backlog_items
           WHERE deadline_interno IS NOT NULL
           AND deadline_interno <= CURRENT_DATE + 3
           AND estado NOT IN ('Desplegado','Cancelado','Archivado')
           ORDER BY deadline_interno ASC"""
    )

    return {
        "periodo": periodo,
        "items_completados": completados or 0,
        "items_en_progreso": en_progreso or 0,
        "items_backlog": backlog or 0,
        "bugs_criticos_abiertos": bugs_criticos or 0,
        "sla_cumplido_pct": round(sla_pct, 1),
        "lead_time_promedio_horas": round(float(lead_time or 0), 1),
        "items_en_riesgo": [dict(r) for r in riesgo_rows],
    }


async def rendimiento_por_dev(conn: asyncpg.Connection, periodo: str = "esta_semana") -> list[dict]:
    """Rendimiento de cada dev en el periodo."""
    inicio, _ = _rango_periodo(periodo)

    rows = await conn.fetch(
        """SELECT
            d.codigo, d.nombre_completo, d.nivel,
            COUNT(CASE WHEN bi.estado = 'Desplegado' AND bi.fecha_desplegado >= $1 THEN 1 END) as completados,
            COUNT(CASE WHEN bi.estado IN ('En Analisis','En Desarrollo','En QA') THEN 1 END) as en_progreso,
            AVG(CASE WHEN bi.lead_time_horas IS NOT NULL AND bi.fecha_desplegado >= $1
                THEN bi.lead_time_horas END) as lead_time_prom,
            COUNT(CASE WHEN bi.cumplio_sla = TRUE AND bi.fecha_desplegado >= $1 THEN 1 END) as sla_cumplidos,
            COUNT(CASE WHEN bi.cumplio_sla IS NOT NULL AND bi.fecha_desplegado >= $1 THEN 1 END) as sla_total
           FROM desarrolladores d
           LEFT JOIN backlog_items bi ON bi.dev_id = d.id
           WHERE d.disponible = TRUE
           GROUP BY d.codigo, d.nombre_completo, d.nivel
           ORDER BY completados DESC""",
        inicio
    )
    return [dict(r) for r in rows]
