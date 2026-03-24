"""
Queries para la tabla CLIENTES.

Cada funcion recibe una conexion del pool y ejecuta SQL.
Retorna dicts o listas de dicts (no objetos asyncpg.Record).

Uso:
    pool = get_pool()
    async with pool.acquire() as conn:
        clientes = await listar_clientes(conn)
"""

import asyncpg
from typing import Optional
from uuid import UUID


async def listar_clientes(
    conn: asyncpg.Connection,
    estado: Optional[str] = None,
    page: int = 1,
    per_page: int = 20
) -> tuple[list[dict], int]:
    """
    Lista clientes con filtro opcional y paginacion.
    Retorna (lista_clientes, total_count).
    """
    where = "WHERE 1=1"
    params = []
    idx = 1

    if estado:
        where += f" AND estado_cliente = ${idx}"
        params.append(estado)
        idx += 1

    # Contar total (para paginacion)
    total = await conn.fetchval(f"SELECT COUNT(*) FROM clientes {where}", *params)

    # Obtener pagina
    offset = (page - 1) * per_page
    rows = await conn.fetch(
        f"""SELECT * FROM clientes {where}
            ORDER BY mrr_mensual DESC, nombre_clinica ASC
            LIMIT ${idx} OFFSET ${idx + 1}""",
        *params, per_page, offset
    )

    return [dict(r) for r in rows], total


async def obtener_cliente(conn: asyncpg.Connection, codigo: str) -> Optional[dict]:
    """Obtiene un cliente por su codigo (CLI-001)."""
    row = await conn.fetchrow(
        "SELECT * FROM clientes WHERE codigo = $1", codigo
    )
    return dict(row) if row else None


async def obtener_cliente_por_id(conn: asyncpg.Connection, id: UUID) -> Optional[dict]:
    """Obtiene un cliente por su UUID."""
    row = await conn.fetchrow("SELECT * FROM clientes WHERE id = $1", id)
    return dict(row) if row else None


async def buscar_cliente_por_nombre(conn: asyncpg.Connection, nombre: str) -> Optional[dict]:
    """
    Busca un cliente por nombre con multiples estrategias:
    1. Match exacto (case insensitive)
    2. Match parcial (LIKE)
    3. Match por similitud fonetica (para errores de transcripcion de audio)

    Ejemplo: "Kuro" matchea con "Curo" porque suenan similar.
    """
    # 1. Match exacto
    row = await conn.fetchrow(
        "SELECT * FROM clientes WHERE LOWER(nombre_clinica) = LOWER($1)",
        nombre
    )
    if row:
        return dict(row)

    # 2. Match parcial (LIKE)
    row = await conn.fetchrow(
        "SELECT * FROM clientes WHERE LOWER(nombre_clinica) LIKE LOWER($1) LIMIT 1",
        f"%{nombre}%"
    )
    if row:
        return dict(row)

    # 3. Match por similitud — usa pg_trgm si esta disponible,
    #    sino compara con distancia de edicion simple
    #    Busca clientes donde al menos 60% de las letras coinciden
    rows = await conn.fetch(
        "SELECT *, LENGTH(nombre_clinica) as len FROM clientes ORDER BY nombre_clinica"
    )
    nombre_lower = nombre.lower()
    mejor_match = None
    mejor_score = 0

    for r in rows:
        clinica_lower = r["nombre_clinica"].lower()
        # Calcular similitud simple: letras en comun / longitud maxima
        comunes = sum(1 for c in nombre_lower if c in clinica_lower)
        max_len = max(len(nombre_lower), len(clinica_lower))
        score = comunes / max_len if max_len > 0 else 0

        # Tambien verificar si difieren en solo 1-2 caracteres (typos/audio)
        if len(nombre_lower) == len(clinica_lower):
            diffs = sum(1 for a, b in zip(nombre_lower, clinica_lower) if a != b)
            if diffs <= 2:  # Maximo 2 letras diferentes
                score = max(score, 0.8)

        if score > mejor_score:
            mejor_score = score
            mejor_match = r

    if mejor_match and mejor_score >= 0.6:
        return dict(mejor_match)

    return None


async def crear_cliente(conn: asyncpg.Connection, data: dict) -> dict:
    """
    Crea un cliente nuevo. El codigo (CLI-XXX) se genera automaticamente por trigger.
    Retorna el registro creado completo.
    """
    row = await conn.fetchrow(
        """INSERT INTO clientes (
            nombre_clinica, mrr_mensual,
            tamano, sla_dias, segmento, contacto_nombre, contacto_cargo,
            contacto_whatsapp, contacto_email,
            fecha_inicio_contrato, fecha_renovacion, notas_comerciales
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        RETURNING *""",
        data.get("nombre_clinica"),
        data.get("mrr_mensual", 0),
        data.get("tamano"), data.get("sla_dias"), data.get("segmento"),
        data.get("contacto_nombre"), data.get("contacto_cargo"),
        data.get("contacto_whatsapp"), data.get("contacto_email"),
        data.get("fecha_inicio_contrato"), data.get("fecha_renovacion"),
        data.get("notas_comerciales")
    )
    return dict(row)


async def actualizar_cliente(conn: asyncpg.Connection, codigo: str, data: dict) -> Optional[dict]:
    """
    Actualiza campos de un cliente. Solo actualiza los campos que vienen en data.
    Convierte strings de fecha a objetos date automaticamente.
    Retorna el registro actualizado o None si no existe.
    """
    from datetime import date as date_type

    # Campos que son tipo DATE en la DB
    DATE_FIELDS = {"fecha_renovacion", "fecha_inicio_contrato"}

    # Filtrar campos no-None y convertir tipos
    campos = {}
    for k, v in data.items():
        if v is None:
            continue
        # Convertir strings de fecha a objetos date
        if k in DATE_FIELDS and isinstance(v, str):
            try:
                campos[k] = date_type.fromisoformat(v)
            except ValueError:
                continue  # Ignorar fecha invalida
        else:
            campos[k] = v

    if not campos:
        return await obtener_cliente(conn, codigo)

    # Construir SET dinamico
    sets = []
    params = []
    for i, (key, value) in enumerate(campos.items(), 1):
        sets.append(f"{key} = ${i}")
        params.append(value)

    params.append(codigo)
    query = f"""UPDATE clientes SET {', '.join(sets)}
                WHERE codigo = ${len(params)}
                RETURNING *"""

    row = await conn.fetchrow(query, *params)
    return dict(row) if row else None


async def obtener_clientes_riesgo_churn(conn: asyncpg.Connection) -> list[dict]:
    """Clientes sin atencion hace mas de 30 dias."""
    rows = await conn.fetch(
        """SELECT *,
            EXTRACT(DAY FROM NOW() - COALESCE(fecha_ultimo_item_resuelto, created_at)) as dias_sin_atencion
           FROM clientes
           WHERE estado_cliente = 'Activo'
           AND (fecha_ultimo_item_resuelto IS NULL
                OR fecha_ultimo_item_resuelto < NOW() - INTERVAL '30 days')
           ORDER BY mrr_mensual DESC"""
    )
    return [dict(r) for r in rows]


async def obtener_backlog_cliente(conn: asyncpg.Connection, codigo_cliente: str) -> list[dict]:
    """Todos los items del backlog de un cliente especifico."""
    rows = await conn.fetch(
        """SELECT bi.* FROM backlog_items bi
           JOIN clientes c ON bi.cliente_id = c.id
           WHERE c.codigo = $1
           AND bi.estado NOT IN ('Desplegado','Cancelado','Archivado')
           ORDER BY bi.posicion_backlog ASC""",
        codigo_cliente
    )
    return [dict(r) for r in rows]
