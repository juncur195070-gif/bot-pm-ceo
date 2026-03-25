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
        """SELECT bi.codigo, bi.titulo, bi.tipo, d.nombre_completo as dev_nombre, bi.deadline_interno,
                  bi.deadline_interno - CURRENT_DATE as dias_restantes
           FROM backlog_items bi
           LEFT JOIN desarrolladores d ON bi.dev_id = d.id
           WHERE bi.deadline_interno IS NOT NULL
           AND bi.deadline_interno <= CURRENT_DATE + 3
           AND bi.estado NOT IN ('Desplegado','Cancelado','Archivado')
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

    from app.config.settings import settings
    FACTOR_CARGA = {"Junior": settings.CARGA_JUNIOR, "Mid": settings.CARGA_MID, "Senior": settings.CARGA_SENIOR}

    rows = await conn.fetch(
        """SELECT
            d.codigo, d.nombre_completo, d.nivel,
            d.horas_sprint_semana,
            COUNT(CASE WHEN bi.estado = 'Desplegado' AND bi.fecha_desplegado >= $1 THEN 1 END) as completados,
            COUNT(CASE WHEN bi.estado IN ('En Analisis','En Desarrollo','En QA') THEN 1 END) as en_progreso,
            AVG(CASE WHEN bi.lead_time_horas IS NOT NULL AND bi.fecha_desplegado >= $1
                THEN bi.lead_time_horas END) as lead_time_prom,
            COUNT(CASE WHEN bi.cumplio_sla = TRUE AND bi.fecha_desplegado >= $1 THEN 1 END) as sla_cumplidos,
            COUNT(CASE WHEN bi.cumplio_sla IS NOT NULL AND bi.fecha_desplegado >= $1 THEN 1 END) as sla_total,
            COALESCE(SUM(CASE WHEN bi.estado IN ('En Analisis','En Desarrollo','En QA','Backlog')
                AND bi.dev_id IS NOT NULL
                THEN COALESCE(bi.horas_esfuerzo, 4) END), 0) as horas_asignadas
           FROM desarrolladores d
           LEFT JOIN backlog_items bi ON bi.dev_id = d.id
           WHERE d.disponible = TRUE
           GROUP BY d.codigo, d.nombre_completo, d.nivel, d.horas_sprint_semana
           ORDER BY completados DESC""",
        inicio
    )

    resultado = []
    for r in rows:
        dev = dict(r)
        factor = FACTOR_CARGA.get(dev["nivel"], 0.80)
        horas_disponibles = round((dev["horas_sprint_semana"] or 40) * factor)
        horas_asignadas = float(dev["horas_asignadas"] or 0)
        dev["horas_disponibles"] = horas_disponibles
        dev["horas_asignadas"] = horas_asignadas
        dev["horas_libres"] = max(0, horas_disponibles - horas_asignadas)
        dev["porcentaje_carga"] = round((horas_asignadas / horas_disponibles * 100) if horas_disponibles > 0 else 0)
        sla_total = dev["sla_total"] or 0
        sla_cumplidos = dev["sla_cumplidos"] or 0
        dev["sla_pct"] = round((sla_cumplidos / sla_total * 100) if sla_total > 0 else 0, 1)
        dev["lead_time_prom"] = round(float(dev["lead_time_prom"] or 0), 1)
        del dev["horas_sprint_semana"]
        resultado.append(dev)

    return resultado


async def predecir_entrega(conn: asyncpg.Connection, esfuerzo_talla: str, dev_id=None, tipo: str = None) -> dict:
    """
    Predice fecha de entrega basado en historial real de lead times.

    Estrategia:
    1. Si hay historial del dev+talla → usar ese (más preciso)
    2. Si no, historial del equipo+talla
    3. Si no, estimación nominal × 3

    Retorna P50 (probable) y P85 (peor caso) en horas y fechas.
    """
    import math
    from datetime import date, timedelta

    rows = []

    # 1. Intentar historial del dev específico
    if dev_id:
        query = """SELECT lead_time_horas FROM backlog_items
                   WHERE estado = 'Desplegado' AND lead_time_horas IS NOT NULL
                   AND esfuerzo_talla = $1 AND dev_id = $2
                   ORDER BY fecha_desplegado DESC LIMIT 20"""
        rows = await conn.fetch(query, esfuerzo_talla, dev_id)

    # 2. Si no hay suficiente, historial del equipo por talla+tipo
    if len(rows) < 5 and tipo:
        query = """SELECT lead_time_horas FROM backlog_items
                   WHERE estado = 'Desplegado' AND lead_time_horas IS NOT NULL
                   AND esfuerzo_talla = $1 AND tipo = $2
                   ORDER BY fecha_desplegado DESC LIMIT 30"""
        rows = await conn.fetch(query, esfuerzo_talla, tipo)

    # 3. Fallback: historial del equipo por talla
    if len(rows) < 5:
        query = """SELECT lead_time_horas FROM backlog_items
                   WHERE estado = 'Desplegado' AND lead_time_horas IS NOT NULL
                   AND esfuerzo_talla = $1
                   ORDER BY fecha_desplegado DESC LIMIT 50"""
        rows = await conn.fetch(query, esfuerzo_talla)

    # 4. Sin historial: estimación nominal
    if len(rows) < 3:
        NOMINAL = {"XS": 2, "S": 4, "M": 8, "L": 16, "XL": 32}
        horas_nom = NOMINAL.get(esfuerzo_talla, 8)
        return {
            "p50_horas": round(horas_nom * 3, 1),
            "p85_horas": round(horas_nom * 5, 1),
            "p50_fecha": _horas_a_fecha(horas_nom * 3),
            "p85_fecha": _horas_a_fecha(horas_nom * 5),
            "basado_en": "estimacion_nominal",
            "n_historico": 0,
            "nota": "Sin historial suficiente. Usando estimación nominal × multiplicador."
        }

    # Calcular percentiles
    times = sorted([float(r["lead_time_horas"]) for r in rows])
    n = len(times)
    p50 = times[int(n * 0.50)]
    p85 = times[min(int(n * 0.85), n - 1)]

    return {
        "p50_horas": round(p50, 1),
        "p85_horas": round(p85, 1),
        "p50_fecha": _horas_a_fecha(p50),
        "p85_fecha": _horas_a_fecha(p85),
        "basado_en": "historial_real",
        "n_historico": n,
        "nota": f"Basado en {n} items completados con talla {esfuerzo_talla}."
    }


async def predecir_sprint(conn: asyncpg.Connection) -> dict:
    """
    Monte Carlo: simula 1000 escenarios para predecir cuándo se completa el sprint.
    Usa datos reales de lead_time por talla.
    """
    import random
    from datetime import date, timedelta

    # Items activos (asignados, no desplegados)
    items = await conn.fetch(
        """SELECT bi.esfuerzo_talla, d.nombre_completo as dev_nombre FROM backlog_items bi
           LEFT JOIN desarrolladores d ON bi.dev_id = d.id
           WHERE bi.estado IN ('Backlog', 'En Analisis', 'En Desarrollo', 'En QA')
           AND bi.dev_id IS NOT NULL
           ORDER BY bi.score_wsjf DESC"""
    )

    if not items:
        return {"error": "No hay items activos asignados para predecir"}

    # Historial por talla
    historico = {}
    for talla in ["XS", "S", "M", "L", "XL"]:
        rows = await conn.fetch(
            """SELECT lead_time_horas FROM backlog_items
               WHERE estado = 'Desplegado' AND lead_time_horas IS NOT NULL
               AND esfuerzo_talla = $1""", talla)
        if rows:
            historico[talla] = [float(r["lead_time_horas"]) for r in rows]
        else:
            # Fallback nominal
            historico[talla] = [{"XS": 6, "S": 12, "M": 24, "L": 48, "XL": 96}[talla]]

    # Capacidad del equipo (horas/día)
    total_horas_dia = await conn.fetchval(
        """SELECT COALESCE(SUM(horas_sprint_semana), 40) / 5.0
           FROM desarrolladores WHERE disponible = TRUE"""
    )

    # Monte Carlo: 1000 simulaciones
    resultados_dias = []
    for _ in range(1000):
        total_horas = 0
        for item in items:
            talla = item["esfuerzo_talla"] or "M"
            pool = historico.get(talla, [24])
            total_horas += random.choice(pool)
        # Convertir horas totales a días calendario (equipo en paralelo)
        dias = total_horas / float(total_horas_dia)
        resultados_dias.append(dias)

    resultados_dias.sort()

    return {
        "n_items": len(items),
        "p50_dias": round(resultados_dias[500], 1),
        "p50_fecha": _dias_a_fecha(resultados_dias[500]),
        "p85_dias": round(resultados_dias[850], 1),
        "p85_fecha": _dias_a_fecha(resultados_dias[850]),
        "p95_dias": round(resultados_dias[950], 1),
        "p95_fecha": _dias_a_fecha(resultados_dias[950]),
        "horas_equipo_dia": round(float(total_horas_dia), 1),
        "basado_en": "monte_carlo_1000_simulaciones",
    }


def _horas_a_fecha(horas: float) -> str:
    """Convierte horas de trabajo a fecha calendario (salta fines de semana)."""
    import math
    from datetime import date, timedelta
    horas_por_dia = 6.5  # promedio realista de horas productivas/día
    dias_laborales = math.ceil(horas / horas_por_dia)
    fecha = date.today()
    contados = 0
    while contados < dias_laborales:
        fecha += timedelta(days=1)
        if fecha.weekday() < 5:  # Lun-Vie
            contados += 1
    return fecha.isoformat()


def _dias_a_fecha(dias: float) -> str:
    """Convierte días laborales a fecha calendario."""
    import math
    from datetime import date, timedelta
    dias_lab = math.ceil(dias)
    fecha = date.today()
    contados = 0
    while contados < dias_lab:
        fecha += timedelta(days=1)
        if fecha.weekday() < 5:
            contados += 1
    return fecha.isoformat()


async def velocidad_equipo(conn: asyncpg.Connection) -> dict:
    """Velocity: items completados por semana, tendencia, estimación de limpieza."""
    semanas = await conn.fetch(
        """SELECT DATE_TRUNC('week', fecha_desplegado) as semana,
                  COUNT(*) as completados,
                  COALESCE(SUM(horas_esfuerzo), 0) as horas
           FROM backlog_items
           WHERE estado = 'Desplegado' AND fecha_desplegado >= NOW() - INTERVAL '8 weeks'
           GROUP BY DATE_TRUNC('week', fecha_desplegado)
           ORDER BY semana DESC"""
    )

    pendientes = await conn.fetchval(
        "SELECT count(*) FROM backlog_items WHERE estado NOT IN ('Desplegado','Cancelado','Archivado')")

    if not semanas:
        return {"velocidad_items_semana": 0, "pendientes": pendientes, "semanas_historial": 0}

    items_por_semana = [s["completados"] for s in semanas]
    promedio = sum(items_por_semana) / len(items_por_semana) if items_por_semana else 0
    tendencia = "mejorando" if len(items_por_semana) >= 2 and items_por_semana[0] > items_por_semana[-1] else "estable" if len(items_por_semana) < 2 else "bajando"

    semanas_para_limpiar = round(pendientes / promedio, 1) if promedio > 0 else None

    return {
        "velocidad_items_semana": round(promedio, 1),
        "ultimas_semanas": [{"semana": str(s["semana"].date()), "completados": s["completados"], "horas": float(s["horas"])} for s in semanas[:4]],
        "tendencia": tendencia,
        "pendientes": pendientes,
        "semanas_para_limpiar": semanas_para_limpiar,
    }
