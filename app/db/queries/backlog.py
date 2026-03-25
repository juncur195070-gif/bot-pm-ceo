"""
Queries para la tabla BACKLOG_ITEMS — la tabla central del sistema.
Todos los items de trabajo pasan por aqui.
"""

import re
import asyncpg
from typing import Optional
from uuid import UUID


async def listar_backlog(
    conn: asyncpg.Connection,
    estado: Optional[str] = None,
    cliente_id: Optional[UUID] = None,
    dev_id: Optional[UUID] = None,
    tipo: Optional[str] = None,
    urgencia: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    sort: str = "posicion_backlog:asc"
) -> tuple[list[dict], int]:
    """Lista items del backlog con filtros y paginacion."""
    where = "WHERE 1=1"
    params = []
    idx = 1

    if estado:
        where += f" AND estado = ${idx}"; params.append(estado); idx += 1
    if cliente_id:
        where += f" AND cliente_id = ${idx}"; params.append(cliente_id); idx += 1
    if dev_id:
        where += f" AND dev_id = ${idx}"; params.append(dev_id); idx += 1
    if tipo:
        where += f" AND tipo = ${idx}"; params.append(tipo); idx += 1
    if urgencia:
        where += f" AND urgencia_declarada = ${idx}"; params.append(urgencia); idx += 1

    # Excluir terminados por defecto si no se filtra por estado
    if not estado:
        where += " AND estado NOT IN ('Desplegado','Cancelado','Archivado')"

    total = await conn.fetchval(f"SELECT COUNT(*) FROM backlog_items {where}", *params)

    # Parsear sort con whitelist contra SQL injection
    from app.config.settings import settings
    sort_field, sort_dir = "posicion_backlog", "ASC"
    if ":" in sort:
        parts = sort.split(":")
        candidate = parts[0]
        if candidate in settings.ALLOWED_SORT_FIELDS:
            sort_field = candidate
        sort_dir = "DESC" if parts[1].lower() == "desc" else "ASC"

    offset = (page - 1) * per_page
    rows = await conn.fetch(
        f"""SELECT * FROM backlog_items {where}
            ORDER BY {sort_field} {sort_dir}
            LIMIT ${idx} OFFSET ${idx + 1}""",
        *params, per_page, offset
    )
    return [dict(r) for r in rows], total


def _normalizar_codigo(texto: str) -> str:
    """
    Normaliza variantes de codigo: BK0002 → BK-0002, bk 0002 → BK-0002.
    """
    # Detectar patron BK seguido de numeros (con o sin guion/espacio)
    match = re.match(r'[Bb][Kk][\s\-]?(\d+)', texto.strip())
    if match:
        num = match.group(1).zfill(4)
        return f"BK-{num}"
    return texto


async def obtener_item(conn: asyncpg.Connection, codigo: str) -> Optional[dict]:
    """Obtiene un item por codigo. Acepta BK0002, BK-0002, bk 0002, etc."""
    codigo = _normalizar_codigo(codigo)
    row = await conn.fetchrow("SELECT * FROM backlog_items WHERE codigo = $1", codigo)
    return dict(row) if row else None


async def buscar_items(conn: asyncpg.Connection, texto: str, limite: int = 5) -> list[dict]:
    """
    Busca items por texto con multiples estrategias:
    1. Busqueda exacta con el texto completo
    2. Si no encuentra, intenta con cada palabra significativa (>3 chars)
    """
    # 1. Busqueda con texto completo (unaccent para tolerar tildes)
    query = """SELECT * FROM backlog_items
           WHERE (
               unaccent(LOWER(titulo)) LIKE unaccent(LOWER($1))
               OR unaccent(LOWER(descripcion)) LIKE unaccent(LOWER($1))
               OR unaccent(LOWER(cliente_nombre)) LIKE unaccent(LOWER($1))
               OR LOWER(codigo) LIKE LOWER($1)
               OR unaccent(LOWER(dev_nombre)) LIKE unaccent(LOWER($1))
               OR unaccent(LOWER(tipo)) LIKE unaccent(LOWER($1))
           )
           AND estado NOT IN ('Desplegado','Cancelado','Archivado')
           ORDER BY posicion_backlog ASC LIMIT $2"""

    rows = await conn.fetch(query, f"%{texto}%", limite)
    if rows:
        return [dict(r) for r in rows]

    # 2. Si no encuentra, buscar con cada palabra significativa
    palabras = [p for p in texto.split() if len(p) > 3 and p.lower() not in (
        "tarea", "lead", "cliente", "urgencia", "estado", "cambiar", "asignar",
        "crear", "para", "como", "esta", "este", "esos", "esas", "tiene"
    )]
    for palabra in palabras:
        rows = await conn.fetch(query, f"%{palabra}%", limite)
        if rows:
            return [dict(r) for r in rows]

    return []


async def crear_item(conn: asyncpg.Connection, data: dict) -> dict:
    """Crea un item en el backlog. Convierte fechas string a date automaticamente."""
    from datetime import date as date_type

    def _to_date(val):
        """Convierte string 'YYYY-MM-DD' a date, o retorna None."""
        if val is None:
            return None
        if isinstance(val, date_type):
            return val
        if isinstance(val, str):
            try:
                return date_type.fromisoformat(val)
            except ValueError:
                return None
        return None

    row = await conn.fetchrow(
        """INSERT INTO backlog_items (
            titulo, tipo, estado, descripcion, reportado_por_id,
            cliente_id, cliente_nombre, cliente_mrr, cliente_tamano, cliente_sla_dias,
            es_lead, lead_id, urgencia_declarada,
            deadline_interno, fecha_qa_estimada, deadline_cliente,
            impacto_todos_usuarios, skill_requerido, esfuerzo_talla,
            adjuntos_urls, notas_pm
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
        RETURNING *""",
        data.get("titulo"), data.get("tipo"),
        data.get("estado", "Backlog"),  # Siempre Backlog al crear
        data.get("descripcion"),
        data.get("reportado_por_id"),
        data.get("cliente_id"), data.get("cliente_nombre"), float(data.get("cliente_mrr", 0) or 0),
        data.get("cliente_tamano"), data.get("cliente_sla_dias"),
        data.get("es_lead", False), data.get("lead_id"),
        data.get("urgencia_declarada"),
        _to_date(data.get("deadline_interno")), _to_date(data.get("fecha_qa_estimada")),
        _to_date(data.get("deadline_cliente")),
        data.get("impacto_todos_usuarios", False),
        data.get("skill_requerido", []),
        data.get("esfuerzo_talla"), data.get("adjuntos_urls", []),
        data.get("notas_pm")
    )
    return dict(row)


async def actualizar_item(conn: asyncpg.Connection, codigo: str, data: dict) -> Optional[dict]:
    """Actualiza campos de un item. Convierte fechas string a date. Permite NULL explicito."""
    from datetime import date as date_type, datetime as datetime_type
    DATE_FIELDS = {"deadline_interno", "fecha_qa_estimada", "deadline_cliente"}
    TIMESTAMP_FIELDS = {"fecha_asignacion", "fecha_inicio_desarrollo", "fecha_qa", "fecha_desplegado"}
    NULLABLE_FIELDS = {"cliente_id", "lead_id", "dev_id"}

    campos = {}
    for k, v in data.items():
        if v is None and k not in NULLABLE_FIELDS:
            continue
        if k in DATE_FIELDS and isinstance(v, str):
            try:
                campos[k] = date_type.fromisoformat(v)
            except ValueError:
                continue
        elif k in TIMESTAMP_FIELDS and isinstance(v, str):
            try:
                campos[k] = datetime_type.fromisoformat(v)
            except ValueError:
                continue
        else:
            campos[k] = v

    if not campos:
        return await obtener_item(conn, codigo)

    sets, params = [], []
    for i, (key, value) in enumerate(campos.items(), 1):
        sets.append(f"{key} = ${i}")
        params.append(value)

    params.append(codigo)
    row = await conn.fetchrow(
        f"UPDATE backlog_items SET {', '.join(sets)} WHERE codigo = ${len(params)} RETURNING *",
        *params
    )
    return dict(row) if row else None


async def obtener_kanban(conn: asyncpg.Connection) -> dict[str, list[dict]]:
    """Items agrupados por estado para vista Kanban."""
    estados = ["Backlog", "En Analisis", "En Desarrollo", "En QA"]
    result = {}
    for estado in estados:
        rows = await conn.fetch(
            """SELECT codigo, titulo, tipo, urgencia_declarada, dev_nombre,
                      score_wsjf, deadline_interno, esfuerzo_talla
               FROM backlog_items WHERE estado = $1
               ORDER BY posicion_backlog ASC LIMIT 50""",
            estado
        )
        result[estado] = [dict(r) for r in rows]
    return result


async def contar_por_estado(conn: asyncpg.Connection) -> dict[str, int]:
    """Cuenta items por estado — util para metricas rapidas."""
    rows = await conn.fetch(
        "SELECT estado, COUNT(*) as total FROM backlog_items GROUP BY estado"
    )
    return {r["estado"]: r["total"] for r in rows}
