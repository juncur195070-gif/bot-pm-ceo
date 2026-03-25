"""Consulta tools — read-only queries for backlog, items, team, metrics, clients."""

from app.tools.registry import register
from app.tools.base import ok, fail, _a_json
from app.db.queries import backlog as q_backlog
from app.db.queries import desarrolladores as q_devs
from app.db.queries import clientes as q_clientes
from app.db.queries import metricas as q_metricas
from app.db.queries import leads as q_leads


@register("consultar_backlog")
async def consultar_backlog(conn, params, usuario):
    """Busca items en el backlog con filtros. Devs solo ven sus tareas."""
    # Si es dev, forzar filtro por su dev_id
    dev_id_filtro = None
    if usuario.get("rol") == "desarrollador":
        dev_id_filtro = usuario.get("desarrollador_id") or usuario.get("id")

    if params.get("busqueda_texto"):
        incluir_cancelados = params.get("estado") in ("Cancelado", "Archivado")
        items = await q_backlog.buscar_items(conn, params["busqueda_texto"], params.get("top_n", 5), incluir_cancelados=incluir_cancelados)
        # Filtrar por dev si aplica
        if dev_id_filtro:
            items = [i for i in items if str(i.get("dev_id")) == str(dev_id_filtro)]
        # Filtrar por estado si se pidió
        if params.get("estado"):
            items = [i for i in items if i.get("estado") == params["estado"]]
        return ok({"items": items, "total": len(items)})

    cliente_id = None
    if params.get("cliente"):
        cliente = await q_clientes.buscar_cliente_por_nombre(conn, params["cliente"])
        if cliente:
            cliente_id = cliente["id"]

    items, total = await q_backlog.listar_backlog(
        conn,
        estado=params.get("estado"),
        cliente_id=cliente_id,
        dev_id=dev_id_filtro,
        tipo=params.get("tipo"),
        urgencia=params.get("urgencia"),
        per_page=params.get("top_n", 5)
    )
    return ok({"items": items, "total": total})


@register("consultar_item")
async def consultar_item(conn, params, usuario):
    """Detalle de un item por codigo o busqueda. Devs solo ven sus tareas."""
    dev_id_filtro = None
    if usuario.get("rol") == "desarrollador":
        dev_id_filtro = usuario.get("desarrollador_id") or usuario.get("id")

    if params.get("codigo"):
        item = await q_backlog.obtener_item(conn, params["codigo"])
        if not item:
            return fail("Item no encontrado con ese codigo")
        if dev_id_filtro and str(item.get("dev_id")) != str(dev_id_filtro):
            return fail("Ese item no esta asignado a ti")
        if item and item.get("score_wsjf") and float(item.get("score_wsjf", 0)) > 0:
            item["score_explicacion"] = {
                "bloque_a_cliente": f"{float(item.get('score_bloque_a', 0)):.1f}/10 (valor del cliente: MRR, tamaño, riesgo)",
                "bloque_b_tarea": f"{float(item.get('score_bloque_b', 0)):.1f}/10 (gravedad: tipo, urgencia, impacto)",
                "bloque_c_tiempo": f"{float(item.get('score_bloque_c', 0)):.1f}/10 (urgencia temporal: deadline, antigüedad)",
                "formula": "WSJF = (A×40% + B×35% + C×25%) / tamaño_tarea"
            }
        return ok({"item": item})

    if params.get("busqueda_texto") or params.get("cliente"):
        texto = params.get("busqueda_texto", params.get("cliente", ""))
        items = await q_backlog.buscar_items(conn, texto, 3)
        if dev_id_filtro:
            items = [i for i in items if str(i.get("dev_id")) == str(dev_id_filtro)]
        if items:
            item = items[0]
            if item and item.get("score_wsjf") and float(item.get("score_wsjf", 0)) > 0:
                item["score_explicacion"] = {
                    "bloque_a_cliente": f"{float(item.get('score_bloque_a', 0)):.1f}/10 (valor del cliente: MRR, tamaño, riesgo)",
                    "bloque_b_tarea": f"{float(item.get('score_bloque_b', 0)):.1f}/10 (gravedad: tipo, urgencia, impacto)",
                    "bloque_c_tiempo": f"{float(item.get('score_bloque_c', 0)):.1f}/10 (urgencia temporal: deadline, antigüedad)",
                    "formula": "WSJF = (A×40% + B×35% + C×25%) / tamaño_tarea"
                }
            return ok({"item": item})
        return fail("No se encontraron items asignados a ti con esa busqueda")

    return fail("Necesito un codigo BK-XXXX o texto para buscar")


@register("consultar_equipo")
async def consultar_equipo(conn, params, usuario):
    """Estado del equipo."""
    if params.get("skill_requerido"):
        capacidad = await q_devs.obtener_capacidad_equipo(conn)
        filtrados = [d for d in capacidad if params["skill_requerido"] in d.get("skills", [])]
        return ok({"equipo": filtrados})

    capacidad = await q_devs.obtener_capacidad_equipo(conn)
    bug_guard = await q_devs.obtener_bug_guard(conn)
    return ok({"equipo": capacidad, "bug_guard": bug_guard})


@register("consultar_metricas")
async def consultar_metricas(conn, params, usuario):
    """Dashboard y metricas."""
    tipo = params.get("tipo_metrica", "general")
    periodo = params.get("periodo", "esta_semana")

    if tipo == "velocidad":
        data = await q_metricas.velocidad_equipo(conn)
        return ok(data)

    if tipo == "por_dev":
        data = await q_metricas.rendimiento_por_dev(conn, periodo)
        return ok({"metricas": data})

    dashboard = await q_metricas.dashboard_general(conn, periodo)
    return ok({"metricas": dashboard})


@register("consultar_cliente")
async def consultar_cliente(conn, params, usuario):
    """Datos de un cliente."""
    if params.get("riesgo_churn"):
        clientes = await q_clientes.obtener_clientes_riesgo_churn(conn)
        return ok({"clientes": clientes})

    if params.get("nombre"):
        cliente = await q_clientes.buscar_cliente_por_nombre(conn, params["nombre"])
        if cliente:
            return ok({"cliente": cliente})
        return fail(f"Cliente '{params['nombre']}' no encontrado")

    if params.get("listar_todos"):
        clientes, _ = await q_clientes.listar_clientes(conn, per_page=50)
        # Agregar leads convertidos recientemente
        leads_convertidos = await conn.fetch(
            """SELECT l.codigo, l.nombre_clinica as lead_nombre, c.codigo as cliente_codigo,
                      c.nombre_clinica as cliente_nombre, c.mrr_mensual, l.updated_at as fecha_conversion
               FROM leads l
               JOIN clientes c ON l.cliente_convertido_id = c.id
               WHERE l.estado_lead = 'Convertido'
               ORDER BY l.updated_at DESC LIMIT 10"""
        )
        # MRR total
        mrr_total = await conn.fetchval("SELECT COALESCE(SUM(mrr_mensual), 0) FROM clientes WHERE estado_cliente = 'Activo'")
        return ok({
            "clientes": clientes,
            "mrr_total": float(mrr_total),
            "arr_total": float(mrr_total * 12),
            "leads_convertidos": [dict(l) for l in leads_convertidos] if leads_convertidos else [],
        })

    return fail("Indica el nombre del cliente o usa listar_todos=true")
