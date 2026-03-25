"""
Queries para la tabla LEADS.
"""

import asyncpg
from typing import Optional
from uuid import UUID


async def listar_leads(conn: asyncpg.Connection, estado: Optional[str] = None) -> list[dict]:
    """Lista leads con filtro opcional de estado."""
    if estado:
        rows = await conn.fetch(
            "SELECT * FROM leads WHERE estado_lead = $1 ORDER BY created_at DESC", estado
        )
    else:
        rows = await conn.fetch(
            "SELECT * FROM leads WHERE estado_lead NOT IN ('Perdido','Convertido') ORDER BY created_at DESC"
        )
    return [dict(r) for r in rows]


async def obtener_lead(conn: asyncpg.Connection, codigo: str) -> Optional[dict]:
    row = await conn.fetchrow("SELECT * FROM leads WHERE codigo = $1", codigo)
    return dict(row) if row else None


async def buscar_lead_por_nombre(conn: asyncpg.Connection, nombre: str) -> Optional[dict]:
    """Busca lead por nombre con tolerancia a errores."""
    # Match exacto
    row = await conn.fetchrow(
        "SELECT * FROM leads WHERE LOWER(nombre_clinica) LIKE LOWER($1) LIMIT 1",
        f"%{nombre}%"
    )
    if row:
        return dict(row)

    # Fuzzy match con pg_trgm
    try:
        row = await conn.fetchrow(
            """SELECT *, similarity(LOWER(nombre_clinica), LOWER($1)) as sim
               FROM leads
               WHERE similarity(LOWER(nombre_clinica), LOWER($1)) > 0.25
               ORDER BY similarity(LOWER(nombre_clinica), LOWER($1)) DESC
               LIMIT 1""",
            nombre
        )
        if row:
            return dict(row)
    except Exception:
        # pg_trgm no disponible — fallback Python
        rows = await conn.fetch("SELECT * FROM leads ORDER BY nombre_clinica")
        nombre_lower = nombre.lower()
        for r in rows:
            clinica = r["nombre_clinica"].lower()
            if len(nombre_lower) == len(clinica):
                diffs = sum(1 for a, b in zip(nombre_lower, clinica) if a != b)
                if diffs <= 2:
                    return dict(r)
    return None


async def crear_lead(conn: asyncpg.Connection, data: dict) -> dict:
    """Crea un lead nuevo. Codigo generado automaticamente."""
    from datetime import date as date_type
    fecha_renovacion = data.get("fecha_renovacion")
    if isinstance(fecha_renovacion, str):
        try:
            fecha_renovacion = date_type.fromisoformat(fecha_renovacion)
        except ValueError:
            fecha_renovacion = None

    row = await conn.fetchrow(
        """INSERT INTO leads (
            nombre_clinica, contacto_nombre, contacto_whatsapp,
            estado_lead, mrr_estimado, tamano_estimado,
            probabilidad_cierre, requisitos_solicitados, notas
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        RETURNING *""",
        data.get("nombre_clinica"), data.get("contacto_nombre"),
        data.get("contacto_whatsapp"), data.get("estado_lead", "Nuevo"),
        data.get("mrr_estimado", 0), data.get("tamano_estimado"),
        data.get("probabilidad_cierre"), data.get("requisitos_solicitados"),
        data.get("notas")
    )
    return dict(row)


async def actualizar_lead(conn: asyncpg.Connection, codigo: str, data: dict) -> Optional[dict]:
    """Actualiza campos de un lead."""
    campos = {k: v for k, v in data.items() if v is not None}
    if not campos:
        return await obtener_lead(conn, codigo)

    sets, params = [], []
    for i, (key, value) in enumerate(campos.items(), 1):
        sets.append(f"{key} = ${i}")
        params.append(value)

    params.append(codigo)
    row = await conn.fetchrow(
        f"UPDATE leads SET {', '.join(sets)} WHERE codigo = ${len(params)} RETURNING *",
        *params
    )
    return dict(row) if row else None


async def convertir_lead_a_cliente(conn: asyncpg.Connection, codigo_lead: str, data_cliente: dict) -> dict:
    """
    Convierte un lead a cliente:
    1. Crea el cliente con los datos del lead + data_cliente
    2. Actualiza el lead a estado 'Convertido' con referencia al cliente
    """
    from app.db.queries.clientes import crear_cliente

    lead = await obtener_lead(conn, codigo_lead)
    if not lead:
        return {"error": f"Lead {codigo_lead} no encontrado"}

    # Datos del cliente = datos del lead + datos adicionales
    cliente_data = {
        "nombre_clinica": lead["nombre_clinica"],
        "mrr_mensual": data_cliente.get("mrr_mensual", lead.get("mrr_estimado", 0)),
        "tamano": data_cliente.get("tamano", lead.get("tamano_estimado", "Pequena")),
        "sla_dias": data_cliente.get("sla_dias", 7),
        "contacto_nombre": lead.get("contacto_nombre"),
        "contacto_whatsapp": lead.get("contacto_whatsapp"),
        **{k: v for k, v in data_cliente.items() if v is not None},
    }

    cliente = await crear_cliente(conn, cliente_data)

    # Actualizar lead como convertido
    await conn.execute(
        """UPDATE leads SET estado_lead = 'Convertido', cliente_convertido_id = $1
           WHERE codigo = $2""",
        cliente["id"], codigo_lead
    )

    return {"cliente": cliente, "lead": lead}
