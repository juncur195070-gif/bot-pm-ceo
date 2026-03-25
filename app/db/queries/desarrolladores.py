"""
Queries para la tabla DESARROLLADORES.
Incluye funciones de capacidad y Bug Guard.
"""

import asyncpg
from typing import Optional
from uuid import UUID
from app.utils.phone import normalizar as normalizar_telefono


async def listar_devs(
    conn: asyncpg.Connection,
    solo_disponibles: bool = False
) -> list[dict]:
    """Lista desarrolladores, opcionalmente solo los disponibles."""
    where = "WHERE disponible = TRUE" if solo_disponibles else ""
    rows = await conn.fetch(
        f"SELECT * FROM desarrolladores {where} ORDER BY nivel DESC, nombre_completo ASC"
    )
    return [dict(r) for r in rows]


async def obtener_dev(conn: asyncpg.Connection, codigo: str) -> Optional[dict]:
    """Obtiene un dev por codigo (DEV-001)."""
    row = await conn.fetchrow("SELECT * FROM desarrolladores WHERE codigo = $1", codigo)
    return dict(row) if row else None


async def obtener_dev_por_id(conn: asyncpg.Connection, id: UUID) -> Optional[dict]:
    row = await conn.fetchrow("SELECT * FROM desarrolladores WHERE id = $1", id)
    return dict(row) if row else None


async def buscar_dev_por_nombre(conn: asyncpg.Connection, nombre: str) -> Optional[dict]:
    """
    Busca dev por nombre con tolerancia a errores de audio.
    "Carlos" matchea con "Carlos Ramirez", "Davit" matchea con "David".
    Usa pg_trgm (trigramas) si esta disponible, sino fallback Python.
    """
    # 1. Match exacto parcial (rapido)
    row = await conn.fetchrow(
        "SELECT * FROM desarrolladores WHERE LOWER(nombre_completo) LIKE LOWER($1) LIMIT 1",
        f"%{nombre}%"
    )
    if row:
        return dict(row)

    # 2. Fuzzy match con pg_trgm (todo en PostgreSQL, sin cargar a memoria)
    try:
        row = await conn.fetchrow(
            """SELECT *, similarity(LOWER(nombre_completo), LOWER($1)) as sim
               FROM desarrolladores
               WHERE similarity(LOWER(nombre_completo), LOWER($1)) > 0.25
               ORDER BY similarity(LOWER(nombre_completo), LOWER($1)) DESC
               LIMIT 1""",
            nombre
        )
        if row:
            return dict(row)
    except Exception:
        # pg_trgm no disponible — fallback a comparacion Python
        rows = await conn.fetch("SELECT * FROM desarrolladores ORDER BY nombre_completo")
        nombre_lower = nombre.lower()
        for r in rows:
            campo = r["nombre_completo"].lower()
            if len(nombre_lower) == len(campo):
                diffs = sum(1 for a, b in zip(nombre_lower, campo) if a != b)
                if diffs <= 2:
                    return dict(r)
            comunes = sum(1 for c in nombre_lower if c in campo)
            max_len = max(len(nombre_lower), len(campo))
            if max_len > 0 and comunes / max_len >= 0.7:
                return dict(r)
    return None


JORNADA_HORAS = {
    "full_time": 40,
    "medio_tiempo": 30,
    "part_time": 20,
}


async def crear_dev(conn: asyncpg.Connection, data: dict) -> dict:
    """Crea un desarrollador. Convierte jornada a horas automaticamente."""
    # Convertir jornada a horas
    horas = data.get("horas_semana_base")
    if not horas and data.get("jornada"):
        horas = JORNADA_HORAS.get(data["jornada"], 40)
    if not horas:
        horas = 40  # Default full time

    wa = normalizar_telefono(data.get("whatsapp", ""))

    row = await conn.fetchrow(
        """INSERT INTO desarrolladores (
            nombre_completo, nivel, horas_semana_base,
            skills, whatsapp, email, notas
        ) VALUES ($1,$2,$3,$4,$5,$6,$7)
        RETURNING *""",
        data["nombre_completo"], data["nivel"],
        horas, data.get("skills", []),
        wa or None, data.get("email"), data.get("notas")
    )
    return dict(row)


async def actualizar_dev(conn: asyncpg.Connection, codigo: str, data: dict) -> Optional[dict]:
    """Actualiza campos de un dev."""
    from datetime import date as date_type
    DATE_FIELDS = {"fecha_regreso", "ultima_semana_bug_guard"}

    campos = {}
    for k, v in data.items():
        if v is None:
            continue
        if k == "whatsapp" and isinstance(v, str):
            campos[k] = normalizar_telefono(v)
        elif k in DATE_FIELDS and isinstance(v, str):
            try:
                campos[k] = date_type.fromisoformat(v)
            except ValueError:
                continue
        else:
            campos[k] = v

    if not campos:
        return await obtener_dev(conn, codigo)

    sets = []
    params = []
    for i, (key, value) in enumerate(campos.items(), 1):
        sets.append(f"{key} = ${i}")
        params.append(value)

    params.append(codigo)
    row = await conn.fetchrow(
        f"UPDATE desarrolladores SET {', '.join(sets)} WHERE codigo = ${len(params)} RETURNING *",
        *params
    )
    return dict(row) if row else None


async def obtener_capacidad_equipo(conn: asyncpg.Connection) -> list[dict]:
    """
    Capacidad actual basada en HORAS, no en conteo de tareas.

    Cada dev tiene:
      horas_disponibles = horas_sprint_semana × factor_carga
      horas_asignadas   = SUM(horas_esfuerzo) de tareas activas
      horas_libres      = horas_disponibles - horas_asignadas
      porcentaje_carga  = horas_asignadas / horas_disponibles × 100
      puede_recibir     = TRUE si porcentaje_carga < 100%
    """
    from app.config.settings import settings

    FACTOR_CARGA = {
        "Junior": settings.CARGA_JUNIOR,
        "Mid": settings.CARGA_MID,
        "Senior": settings.CARGA_SENIOR,
    }

    rows = await conn.fetch(
        """SELECT
            d.id, d.codigo, d.nombre_completo, d.nivel, d.disponible,
            d.horas_semana_base, d.horas_sprint_semana,
            d.bug_guard_semana_actual,
            d.skills, d.whatsapp,
            COALESCE(t.horas_asignadas, 0) as horas_asignadas,
            COALESCE(t.tareas_activas, 0) as tareas_activas
           FROM desarrolladores d
           LEFT JOIN (
               SELECT dev_id,
                      SUM(COALESCE(horas_esfuerzo, 4)) as horas_asignadas,
                      COUNT(*) as tareas_activas
               FROM backlog_items
               WHERE estado IN ('Backlog','En Analisis','En Desarrollo','En QA')
               AND dev_id IS NOT NULL
               GROUP BY dev_id
           ) t ON t.dev_id = d.id
           WHERE d.disponible = TRUE
           ORDER BY d.nivel DESC, d.nombre_completo ASC"""
    )

    resultado = []
    for r in rows:
        dev = dict(r)
        factor = FACTOR_CARGA.get(dev["nivel"], 0.80)
        horas_disponibles = round(dev["horas_sprint_semana"] * factor)
        horas_asignadas = float(dev["horas_asignadas"] or 0)
        horas_libres = max(0, horas_disponibles - horas_asignadas)
        porcentaje = round((horas_asignadas / horas_disponibles * 100) if horas_disponibles > 0 else 0)

        dev["horas_disponibles"] = horas_disponibles
        dev["horas_asignadas"] = horas_asignadas
        dev["horas_libres"] = horas_libres
        dev["porcentaje_carga"] = porcentaje
        dev["puede_recibir"] = porcentaje < 100
        dev["factor_carga"] = factor
        resultado.append(dev)

    return resultado


async def obtener_tareas_dev(conn: asyncpg.Connection, codigo_dev: str) -> list[dict]:
    """Tareas activas de un dev especifico."""
    rows = await conn.fetch(
        """SELECT bi.* FROM backlog_items bi
           JOIN desarrolladores d ON bi.dev_id = d.id
           WHERE d.codigo = $1
           AND bi.estado NOT IN ('Desplegado','Cancelado','Archivado')
           ORDER BY bi.posicion_backlog ASC""",
        codigo_dev
    )
    return [dict(r) for r in rows]


async def obtener_bug_guard(conn: asyncpg.Connection) -> Optional[dict]:
    """Obtiene el Bug Guard activo de la semana."""
    row = await conn.fetchrow(
        "SELECT * FROM desarrolladores WHERE bug_guard_semana_actual = TRUE LIMIT 1"
    )
    return dict(row) if row else None
