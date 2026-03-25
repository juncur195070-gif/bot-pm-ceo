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
        where += f" AND bi.estado = ${idx}"; params.append(estado); idx += 1
    if cliente_id:
        where += f" AND bi.cliente_id = ${idx}"; params.append(cliente_id); idx += 1
    if dev_id:
        where += f" AND bi.dev_id = ${idx}"; params.append(dev_id); idx += 1
    if tipo:
        where += f" AND bi.tipo = ${idx}"; params.append(tipo); idx += 1
    if urgencia:
        where += f" AND bi.urgencia_declarada = ${idx}"; params.append(urgencia); idx += 1

    # Excluir terminados por defecto si no se filtra por estado
    if not estado:
        where += " AND bi.estado NOT IN ('Desplegado','Cancelado','Archivado')"

    total = await conn.fetchval(f"SELECT COUNT(*) FROM backlog_items bi {where}", *params)

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
        f"""SELECT bi.*, c.nombre_clinica as cliente_nombre, c.mrr_mensual as cliente_mrr,
                   c.tamano as cliente_tamano, c.sla_dias as cliente_sla_dias,
                   d.nombre_completo as dev_nombre
            FROM backlog_items bi
            LEFT JOIN clientes c ON bi.cliente_id = c.id
            LEFT JOIN desarrolladores d ON bi.dev_id = d.id
            {where}
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
    row = await conn.fetchrow(
        """SELECT bi.*, c.nombre_clinica as cliente_nombre, c.mrr_mensual as cliente_mrr,
                  c.tamano as cliente_tamano, c.sla_dias as cliente_sla_dias,
                  d.nombre_completo as dev_nombre
           FROM backlog_items bi
           LEFT JOIN clientes c ON bi.cliente_id = c.id
           LEFT JOIN desarrolladores d ON bi.dev_id = d.id
           WHERE bi.codigo = $1""",
        codigo
    )
    return dict(row) if row else None


async def buscar_items(conn: asyncpg.Connection, texto: str, limite: int = 5, incluir_cancelados: bool = False) -> list[dict]:
    """
    Busca items por texto con ranking por relevancia:
    1. Busqueda exacta con texto completo
    2. Si no, busca por palabras y rankea por cantidad de coincidencias
    """
    stopwords = {
        "tarea", "lead", "cliente", "urgencia", "estado", "cambiar", "asignar",
        "crear", "para", "como", "esta", "este", "esos", "esas", "tiene",
        "bugs", "item", "cosa", "algo", "todo", "todos", "quiero", "dame",
    }

    # 1. Busqueda con texto completo (unaccent para tolerar tildes)
    query = """SELECT bi.*, c.nombre_clinica as cliente_nombre, c.mrr_mensual as cliente_mrr,
                      c.tamano as cliente_tamano, c.sla_dias as cliente_sla_dias,
                      d.nombre_completo as dev_nombre
           FROM backlog_items bi
           LEFT JOIN clientes c ON bi.cliente_id = c.id
           LEFT JOIN desarrolladores d ON bi.dev_id = d.id
           WHERE (
               unaccent(LOWER(bi.titulo)) LIKE unaccent(LOWER($1))
               OR unaccent(LOWER(bi.descripcion)) LIKE unaccent(LOWER($1))
               OR unaccent(LOWER(c.nombre_clinica)) LIKE unaccent(LOWER($1))
               OR LOWER(bi.codigo) LIKE LOWER($1)
               OR unaccent(LOWER(d.nombre_completo)) LIKE unaccent(LOWER($1))
               OR unaccent(LOWER(bi.tipo)) LIKE unaccent(LOWER($1))
           )
           AND bi.estado NOT IN ('Desplegado'{0})
           ORDER BY bi.posicion_backlog ASC LIMIT $2""".format(
               "" if incluir_cancelados else ",'Cancelado','Archivado'"
           )

    rows = await conn.fetch(query, f"%{texto}%", limite)
    if rows:
        return [dict(r) for r in rows]

    # 2. Buscar por palabras significativas y rankear por coincidencias
    palabras = [p.lower() for p in texto.split() if len(p) > 2 and p.lower() not in stopwords]
    if not palabras:
        return []

    # Buscar todos los items (incluir cancelados si se pide)
    excluir = "('Desplegado')" if incluir_cancelados else "('Desplegado','Cancelado','Archivado')"
    todos = await conn.fetch(
        f"""SELECT bi.*, c.nombre_clinica as cliente_nombre, c.mrr_mensual as cliente_mrr,
                  c.tamano as cliente_tamano, c.sla_dias as cliente_sla_dias,
                  d.nombre_completo as dev_nombre
           FROM backlog_items bi
           LEFT JOIN clientes c ON bi.cliente_id = c.id
           LEFT JOIN desarrolladores d ON bi.dev_id = d.id
           WHERE bi.estado NOT IN {excluir}
           ORDER BY bi.posicion_backlog ASC"""
    )

    # Rankear por cantidad de palabras que coinciden en titulo+descripcion+cliente
    scored = []
    for r in todos:
        r = dict(r)
        texto_item = f"{r.get('titulo','')} {r.get('descripcion','')} {r.get('cliente_nombre','')} {r.get('dev_nombre','')} {r.get('tipo','')}".lower()
        # Contar cuantas palabras de busqueda aparecen
        matches = sum(1 for p in palabras if p in texto_item)
        if matches > 0:
            scored.append((matches, r))

    # Ordenar por mas coincidencias primero
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limite]]


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

    # Remove denormalized fields before INSERT (obtained via JOINs now)
    for key in ("cliente_nombre", "cliente_mrr", "cliente_tamano", "cliente_sla_dias", "dev_nombre"):
        data.pop(key, None)

    row = await conn.fetchrow(
        """INSERT INTO backlog_items (
            titulo, tipo, estado, descripcion, reportado_por_id,
            cliente_id,
            es_lead, lead_id, urgencia_declarada,
            deadline_interno, fecha_qa_estimada, deadline_cliente,
            impacto_todos_usuarios, skill_requerido, esfuerzo_talla,
            adjuntos_urls, notas_pm
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        RETURNING codigo""",
        data.get("titulo"), data.get("tipo"),
        data.get("estado", "Backlog"),  # Siempre Backlog al crear
        data.get("descripcion"),
        data.get("reportado_por_id"),
        data.get("cliente_id"),
        data.get("es_lead", False), data.get("lead_id"),
        data.get("urgencia_declarada"),
        _to_date(data.get("deadline_interno")), _to_date(data.get("fecha_qa_estimada")),
        _to_date(data.get("deadline_cliente")),
        data.get("impacto_todos_usuarios", False),
        data.get("skill_requerido", []),
        data.get("esfuerzo_talla"), data.get("adjuntos_urls", []),
        data.get("notas_pm")
    )
    # Retornar con JOINs para incluir cliente_nombre, dev_nombre, etc.
    return await obtener_item(conn, row["codigo"])


async def actualizar_item(conn: asyncpg.Connection, codigo: str, data: dict) -> Optional[dict]:
    """Actualiza campos de un item. Convierte fechas string a date. Permite NULL explicito."""
    from datetime import date as date_type, datetime as datetime_type

    # Remove denormalized fields before UPDATE (obtained via JOINs now)
    for key in ("cliente_nombre", "cliente_mrr", "cliente_tamano", "cliente_sla_dias", "dev_nombre"):
        data.pop(key, None)

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
        f"UPDATE backlog_items SET {', '.join(sets)} WHERE codigo = ${len(params)} RETURNING codigo",
        *params
    )
    if not row:
        return None
    # Retornar con JOINs para incluir cliente_nombre, dev_nombre, etc.
    return await obtener_item(conn, row["codigo"])


async def obtener_kanban(conn: asyncpg.Connection) -> dict[str, list[dict]]:
    """Items agrupados por estado para vista Kanban."""
    estados = ["Backlog", "En Analisis", "En Desarrollo", "En QA"]
    result = {}
    for estado in estados:
        rows = await conn.fetch(
            """SELECT bi.codigo, bi.titulo, bi.tipo, bi.urgencia_declarada,
                      d.nombre_completo as dev_nombre,
                      bi.score_wsjf, bi.deadline_interno, bi.esfuerzo_talla
               FROM backlog_items bi
               LEFT JOIN desarrolladores d ON bi.dev_id = d.id
               WHERE bi.estado = $1
               ORDER BY bi.posicion_backlog ASC LIMIT 50""",
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
