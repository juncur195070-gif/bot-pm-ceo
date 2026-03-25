"""Cliente tools — Client/lead CRUD and client summary."""

from app.tools.registry import register
from app.tools.base import ok, fail
from app.db.queries import clientes as q_clientes
from app.db.queries import leads as q_leads


@register("gestionar_cliente")
async def gestionar_cliente(conn, params):
    """CRUD completo de clientes y leads con verificacion."""
    accion = params["accion"]

    def filtrar():
        data = {}
        for k, v in params.items():
            if k in ("accion", "codigo_o_nombre") or v is None:
                continue
            if k == "notas":
                data["notas_comerciales"] = v
            else:
                data[k] = v
        return data

    if accion == "crear_cliente":
        data = filtrar()
        cliente = await q_clientes.crear_cliente(conn, data)
        # ── VERIFICACION ──
        verificado = await q_clientes.obtener_cliente(conn, cliente["codigo"])
        if not verificado:
            return fail(f"Cliente se intento crear pero NO se verifico en BD")
        return ok({"message": "Cliente creado y verificado", "codigo": verificado["codigo"], "data": verificado})

    elif accion == "actualizar_cliente":
        nombre = params.get("codigo_o_nombre", "")
        data = filtrar()
        if nombre.startswith("CLI-"):
            cliente = await q_clientes.actualizar_cliente(conn, nombre, data)
        else:
            found = await q_clientes.buscar_cliente_por_nombre(conn, nombre)
            if not found:
                return fail(f"Cliente '{nombre}' no encontrado")
            cliente = await q_clientes.actualizar_cliente(conn, found["codigo"], data)
        if not cliente:
            return fail("No se pudo actualizar el cliente")
        return ok({"message": "Cliente actualizado y verificado", "data": cliente})

    elif accion == "eliminar_cliente":
        nombre = params.get("codigo_o_nombre", "")
        if nombre.startswith("CLI-"):
            cliente = await q_clientes.actualizar_cliente(conn, nombre, {"estado_cliente": "Churned"})
        else:
            found = await q_clientes.buscar_cliente_por_nombre(conn, nombre)
            if not found:
                return fail(f"Cliente '{nombre}' no encontrado")
            cliente = await q_clientes.actualizar_cliente(conn, found["codigo"], {"estado_cliente": "Churned"})
        if not cliente:
            return fail("No se pudo eliminar el cliente")
        return ok({"message": f"Cliente {cliente['nombre_clinica']} eliminado (estado: Churned)", "data": cliente})

    elif accion == "crear_lead":
        data = filtrar()
        lead = await q_leads.crear_lead(conn, data)
        verificado = await q_leads.obtener_lead(conn, lead["codigo"])
        if not verificado:
            return fail("Lead se intento crear pero NO se verifico en BD")
        return ok({"message": "Lead creado y verificado", "codigo": verificado["codigo"], "data": verificado})

    elif accion == "actualizar_lead":
        nombre = params.get("codigo_o_nombre", "")
        data = filtrar()
        if nombre.startswith("LED-"):
            lead = await q_leads.actualizar_lead(conn, nombre, data)
        else:
            found = await q_leads.buscar_lead_por_nombre(conn, nombre)
            if not found:
                return fail(f"Lead '{nombre}' no encontrado")
            lead = await q_leads.actualizar_lead(conn, found["codigo"], data)
        if not lead:
            return fail("No se pudo actualizar el lead")
        return ok({"message": "Lead actualizado y verificado", "data": lead})

    elif accion == "eliminar_lead":
        nombre = params.get("codigo_o_nombre", "")
        if nombre.startswith("LED-"):
            lead = await q_leads.actualizar_lead(conn, nombre, {"estado_lead": "Perdido"})
        else:
            found = await q_leads.buscar_lead_por_nombre(conn, nombre)
            if not found:
                return fail(f"Lead '{nombre}' no encontrado")
            lead = await q_leads.actualizar_lead(conn, found["codigo"], {"estado_lead": "Perdido"})
        if not lead:
            return fail("No se pudo eliminar el lead")
        return ok({"message": f"Lead {lead['nombre_clinica']} eliminado (estado: Perdido)", "data": lead})

    elif accion == "convertir_lead":
        nombre = params.get("codigo_o_nombre", "")
        data = filtrar()
        if nombre.startswith("LED-"):
            codigo_lead = nombre
        else:
            found = await q_leads.buscar_lead_por_nombre(conn, nombre)
            if not found:
                return fail(f"Lead '{nombre}' no encontrado")
            codigo_lead = found["codigo"]
        result = await q_leads.convertir_lead_a_cliente(conn, codigo_lead, data)
        if "error" in result:
            return fail(result["error"])
        return ok({
            "message": "Lead convertido a cliente y verificado",
            "lead_codigo": codigo_lead,
            "cliente_codigo": result["cliente"]["codigo"],
            "data": result["cliente"]
        })

    return fail(f"Accion '{accion}' no reconocida. Usa: crear_cliente, actualizar_cliente, eliminar_cliente, crear_lead, actualizar_lead, eliminar_lead, convertir_lead")


@register("resumen_cliente")
async def resumen_cliente(conn, params):
    """Genera resumen profesional para enviar al cliente."""
    nombre = params["cliente"]
    cliente = await q_clientes.buscar_cliente_por_nombre(conn, nombre)
    if not cliente:
        return fail(f"Cliente '{nombre}' no encontrado")

    items = await conn.fetch(
        """SELECT bi.codigo, bi.titulo, bi.tipo, bi.estado, bi.urgencia_declarada, d.nombre_completo as dev_nombre
           FROM backlog_items bi
           LEFT JOIN desarrolladores d ON bi.dev_id = d.id
           WHERE bi.cliente_id = $1
           AND bi.estado NOT IN ('Cancelado','Archivado')
           ORDER BY CASE estado
               WHEN 'En Desarrollo' THEN 1 WHEN 'En QA' THEN 2
               WHEN 'En Analisis' THEN 3 WHEN 'Backlog' THEN 4
               WHEN 'Desplegado' THEN 5
           END""", cliente["id"])

    activos = [i for i in items if i["estado"] != "Desplegado"]
    resueltos = [i for i in items if i["estado"] == "Desplegado"]

    return ok({
        "cliente": cliente["nombre_clinica"],
        "contacto": cliente.get("contacto_nombre"),
        "items_activos": [{"codigo": i["codigo"], "titulo": i["titulo"], "estado": i["estado"], "tipo": i["tipo"]} for i in activos],
        "items_resueltos_recientes": [{"codigo": i["codigo"], "titulo": i["titulo"]} for i in resueltos[:3]],
        "total_activos": len(activos),
        "total_resueltos": len(resueltos),
        "instruccion": "Genera un texto profesional y cordial para enviar al cliente informando el estado de sus tickets."
    })
