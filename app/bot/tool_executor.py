"""
Tool Executor — Ejecuta los tools que Claude solicita.

Claude dice: "quiero usar consultar_backlog con {cliente: 'MINSUR'}"
Este modulo: ejecuta la query y retorna el resultado como string.

El resultado se le devuelve a Claude para que formule la respuesta natural.
"""

import json
import asyncpg
from datetime import date, datetime

from app.db.queries.backlog import _normalizar_codigo
from app.db.queries import clientes as q_clientes
from app.db.queries import desarrolladores as q_devs
from app.db.queries import backlog as q_backlog
from app.db.queries import metricas as q_metricas
from app.db.queries import auditoria as q_audit
from app.db.queries import leads as q_leads
from app.services.airtable_sync import airtable_sync


def _serializar(obj):
    """Convierte objetos no-JSON a string (UUID, date, etc.)."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if hasattr(obj, '__str__'):
        return str(obj)
    return obj


def _a_json(data) -> str:
    """Convierte resultado a JSON string para devolver a Claude."""
    return json.dumps(data, default=_serializar, ensure_ascii=False, indent=2)


async def _sync_item_airtable(conn, codigo: str):
    """
    Sincroniza un item a Airtable en background (no bloquea respuesta).
    """
    import asyncio

    async def _do_sync():
        try:
            from app.config.database import get_pool
            pool = get_pool()
            async with pool.acquire() as sync_conn:
                item = await q_backlog.obtener_item(sync_conn, codigo)
                if not item:
                    return
                record_id = await airtable_sync.sync_backlog_item(dict(item))
                if record_id and not item.get("airtable_record_id"):
                    await q_backlog.actualizar_item(sync_conn, codigo, {"airtable_record_id": record_id})
        except Exception as e:
            print(f"  ⚠ Airtable sync failed for {codigo}: {e}")

    # Lanzar en background — no esperar resultado
    asyncio.create_task(_do_sync())


async def ejecutar_tool(
    nombre: str,
    params: dict,
    conn: asyncpg.Connection,
    usuario: dict
) -> str:
    """
    Ejecuta un tool y retorna el resultado como string.

    Args:
        nombre: Nombre del tool (ej: "consultar_backlog")
        params: Parametros que Claude envio
        conn: Conexion a PostgreSQL
        usuario: Datos del usuario que hizo la peticion

    Returns:
        String con el resultado (JSON o mensaje de texto)
    """
    try:
        if nombre == "consultar_backlog":
            return await _consultar_backlog(conn, params)
        elif nombre == "consultar_item":
            return await _consultar_item(conn, params)
        elif nombre == "consultar_equipo":
            return await _consultar_equipo(conn, params)
        elif nombre == "consultar_metricas":
            return await _consultar_metricas(conn, params)
        elif nombre == "consultar_cliente":
            return await _consultar_cliente(conn, params)
        elif nombre == "crear_item":
            return await _crear_item(conn, params, usuario)
        elif nombre == "actualizar_item":
            return await _actualizar_item(conn, params, usuario)
        elif nombre == "asignar_tarea":
            return await _asignar_tarea(conn, params)
        elif nombre == "establecer_fechas":
            return await _establecer_fechas(conn, params)
        elif nombre == "reportar_bloqueo":
            return await _reportar_bloqueo(conn, params, usuario)
        elif nombre == "derivar_a_persona":
            return await _derivar_a_persona(conn, params)
        elif nombre == "reasignar_bug_guard":
            return await _reasignar_bug_guard(conn, params)
        elif nombre == "gestionar_cliente":
            return await _gestionar_cliente(conn, params)
        elif nombre == "gestionar_dev":
            return await _gestionar_dev(conn, params)
        elif nombre == "adjuntar_imagen":
            return await _adjuntar_imagen(conn, params, usuario)
        else:
            return f"Tool '{nombre}' no reconocido"
    except Exception as e:
        return f"Error ejecutando {nombre}: {str(e)}"


# ── Implementacion de cada tool ──

async def _consultar_backlog(conn, params) -> str:
    """Busca items en el backlog con filtros."""
    # Si hay busqueda por texto
    if params.get("busqueda_texto"):
        items = await q_backlog.buscar_items(conn, params["busqueda_texto"], params.get("top_n", 5))
        return _a_json({"items": items, "total": len(items)})

    # Si hay busqueda por cliente nombre
    cliente_id = None
    if params.get("cliente"):
        cliente = await q_clientes.buscar_cliente_por_nombre(conn, params["cliente"])
        if cliente:
            cliente_id = cliente["id"]

    items, total = await q_backlog.listar_backlog(
        conn,
        estado=params.get("estado"),
        cliente_id=cliente_id,
        tipo=params.get("tipo"),
        urgencia=params.get("urgencia"),
        per_page=params.get("top_n", 5)
    )
    return _a_json({"items": items, "total": total})


async def _consultar_item(conn, params) -> str:
    """Detalle de un item por codigo o busqueda."""
    if params.get("codigo"):
        item = await q_backlog.obtener_item(conn, params["codigo"])
        if item:
            return _a_json(item)
        return "Item no encontrado con ese codigo"

    if params.get("busqueda_texto") or params.get("cliente"):
        texto = params.get("busqueda_texto", params.get("cliente", ""))
        items = await q_backlog.buscar_items(conn, texto, 3)
        if items:
            return _a_json(items[0])  # Retorna el mas relevante
        return "No se encontraron items con esa busqueda"

    return "Necesito un codigo BK-XXXX o texto para buscar"


async def _consultar_equipo(conn, params) -> str:
    """Estado del equipo."""
    if params.get("skill_requerido"):
        capacidad = await q_devs.obtener_capacidad_equipo(conn)
        filtrados = [d for d in capacidad if params["skill_requerido"] in d.get("skills", [])]
        return _a_json(filtrados)

    capacidad = await q_devs.obtener_capacidad_equipo(conn)

    # Agregar Bug Guard info
    bug_guard = await q_devs.obtener_bug_guard(conn)
    return _a_json({
        "equipo": capacidad,
        "bug_guard": bug_guard,
    })


async def _consultar_metricas(conn, params) -> str:
    """Dashboard y metricas."""
    tipo = params.get("tipo_metrica", "general")
    periodo = params.get("periodo", "esta_semana")

    if tipo == "por_dev":
        data = await q_metricas.rendimiento_por_dev(conn, periodo)
        return _a_json(data)

    dashboard = await q_metricas.dashboard_general(conn, periodo)
    return _a_json(dashboard)


async def _consultar_cliente(conn, params) -> str:
    """Datos de un cliente."""
    if params.get("riesgo_churn"):
        clientes = await q_clientes.obtener_clientes_riesgo_churn(conn)
        return _a_json(clientes)

    if params.get("nombre"):
        cliente = await q_clientes.buscar_cliente_por_nombre(conn, params["nombre"])
        if cliente:
            return _a_json(cliente)
        return f"Cliente '{params['nombre']}' no encontrado"

    if params.get("listar_todos"):
        clientes, _ = await q_clientes.listar_clientes(conn, per_page=50)
        return _a_json(clientes)

    return "Indica el nombre del cliente o usa listar_todos=true"


async def _crear_item(conn, params, usuario) -> str:
    """Crea un item en el backlog."""
    # Buscar cliente o lead si se menciona
    cliente_data = {}
    if params.get("cliente"):
        # Primero buscar en clientes
        cliente = await q_clientes.buscar_cliente_por_nombre(conn, params["cliente"])
        if cliente:
            cliente_data = {
                "cliente_id": cliente["id"],
                "cliente_nombre": cliente["nombre_clinica"],
                "cliente_mrr": cliente["mrr_mensual"],
                "cliente_tamano": cliente["tamano"],
                "cliente_sla_dias": cliente["sla_dias"],
            }
        else:
            # Si no es cliente, buscar en leads
            lead = await q_leads.buscar_lead_por_nombre(conn, params["cliente"])
            if lead:
                cliente_data = {
                    "es_lead": True,
                    "lead_id": lead["id"],
                    "cliente_nombre": lead["nombre_clinica"],
                }

    # Recoger imagenes recientes del usuario para adjuntar automaticamente
    adjuntos = params.get("adjuntos_urls", [])
    imagenes_recientes = await conn.fetch(
        """SELECT media_url FROM mensajes_conversacion
           WHERE usuario_id = $1 AND tipo_contenido = 'imagen'
           AND media_url IS NOT NULL
           AND created_at > NOW() - INTERVAL '10 minutes'
           ORDER BY created_at DESC LIMIT 5""",
        usuario["id"]
    )
    for img in imagenes_recientes:
        if img["media_url"] and img["media_url"] not in adjuntos:
            adjuntos.append(img["media_url"])

    data = {
        "titulo": params["titulo"],
        "tipo": params["tipo"],
        "descripcion": params.get("descripcion", ""),
        "urgencia_declarada": params.get("urgencia"),
        "esfuerzo_talla": params.get("esfuerzo_talla"),
        "skill_requerido": [params["skill_requerido"]] if params.get("skill_requerido") else [],
        "adjuntos_urls": adjuntos,
        "reportado_por_id": usuario["id"],
        **cliente_data,
    }

    # Auto-deadline para Bug Critico y Solicitud Bloqueante (+48h)
    if params["tipo"] in ("Bug Critico", "Solicitud Bloqueante") and not data.get("deadline_interno"):
        from datetime import timedelta
        data["deadline_interno"] = (date.today() + timedelta(days=2)).isoformat()

    item = await q_backlog.crear_item(conn, data)

    # Si es Bug Critico → asignacion de emergencia al Bug Guard
    if params["tipo"] in ("Bug Critico", "Solicitud Bloqueante"):
        try:
            from app.scheduled.emergencia import asignar_emergencia
            await asignar_emergencia(
                conn, item["id"], item["codigo"], item["titulo"],
                cliente_data.get("cliente_nombre")
            )
        except Exception as e:
            print(f"  ⚠ Emergencia fallo: {e}")

    # Sync a Airtable
    record_id = await airtable_sync.sync_backlog_item(item)
    if record_id:
        await q_backlog.actualizar_item(conn, item["codigo"], {"airtable_record_id": record_id})

    return _a_json({"message": "Item creado", "codigo": item["codigo"], "item": item})


async def _actualizar_item(conn, params, usuario) -> str:
    """Actualiza cualquier campo de un item: estado, cliente, tipo, urgencia, talla, notas, etc."""
    codigo = params["codigo_o_busqueda"]

    codigo = _normalizar_codigo(codigo)
    if not codigo.startswith("BK-"):
        items = await q_backlog.buscar_items(conn, codigo, 1)
        if not items:
            return f"No encontre item con '{codigo}'"
        codigo = items[0]["codigo"]

    # Construir data con los campos que vinieron
    data = {}
    if params.get("titulo"):
        data["titulo"] = params["titulo"]
    if params.get("estado"):
        data["estado"] = params["estado"]
    if params.get("tipo"):
        data["tipo"] = params["tipo"]
    if params.get("urgencia"):
        data["urgencia_declarada"] = params["urgencia"]
    if params.get("descripcion"):
        data["descripcion"] = params["descripcion"]
    if params.get("esfuerzo_talla"):
        data["esfuerzo_talla"] = params["esfuerzo_talla"]
    if params.get("notas_dev"):
        data["notas_dev"] = params["notas_dev"]
    if params.get("notas_pm"):
        data["notas_pm"] = params["notas_pm"]
    if params.get("skill_requerido"):
        data["skill_requerido"] = [params["skill_requerido"]] if isinstance(params["skill_requerido"], str) else params["skill_requerido"]

    # Si cambia el cliente, buscar en clientes y leads
    if params.get("cliente"):
        cliente = await q_clientes.buscar_cliente_por_nombre(conn, params["cliente"])
        if cliente:
            data["cliente_id"] = cliente["id"]
            data["cliente_nombre"] = cliente["nombre_clinica"]
            data["cliente_mrr"] = cliente["mrr_mensual"]
            data["cliente_tamano"] = cliente["tamano"]
            data["cliente_sla_dias"] = cliente["sla_dias"]
            data["es_lead"] = False
            data["lead_id"] = None
        else:
            lead = await q_leads.buscar_lead_por_nombre(conn, params["cliente"])
            if lead:
                data["cliente_nombre"] = lead["nombre_clinica"]
                data["es_lead"] = True
                data["lead_id"] = lead["id"]
                data["cliente_id"] = None
            else:
                return f"No encontre cliente ni lead con nombre '{params['cliente']}'"

    if not data:
        return "No se especifico ningun campo para actualizar"

    item = await q_backlog.actualizar_item(conn, codigo, data)
    if not item:
        return f"Item {codigo} no encontrado"

    # Si se marco como Desplegado, actualizar fecha_ultimo_item_resuelto del cliente
    if params.get("estado") == "Desplegado" and item.get("cliente_id"):
        await conn.execute(
            "UPDATE clientes SET fecha_ultimo_item_resuelto = NOW() WHERE id = $1",
            item["cliente_id"]
        )

    await _sync_item_airtable(conn, codigo)

    cambios = [k for k in data.keys() if k not in ("cliente_id", "cliente_mrr", "cliente_tamano", "cliente_sla_dias", "lead_id")]
    return _a_json({"message": f"Item {codigo} actualizado: {', '.join(cambios)}", "item": item})


async def _asignar_tarea(conn, params) -> str:
    """Asigna un item a un dev basado en CAPACIDAD DE HORAS, no WIP count."""
    codigo = params["codigo_o_busqueda"]
    codigo = _normalizar_codigo(codigo)
    if not codigo.startswith("BK-"):
        items = await q_backlog.buscar_items(conn, codigo, 1)
        if not items:
            return f"No encontre item con '{codigo}'"
        codigo = items[0]["codigo"]

    item = await q_backlog.obtener_item(conn, codigo)
    skills_req = item.get("skill_requerido", []) if item else []
    horas_item = item.get("horas_esfuerzo") or 4  # Default M si no tiene talla

    capacidad = await q_devs.obtener_capacidad_equipo(conn)

    if params.get("dev_nombre"):
        dev = await q_devs.buscar_dev_por_nombre(conn, params["dev_nombre"])
        if not dev:
            return f"Dev '{params['dev_nombre']}' no encontrado"
        # Verificar capacidad en horas
        dev_cap = next((d for d in capacidad if d["codigo"] == dev["codigo"]), None)
        if dev_cap and dev_cap["horas_libres"] < horas_item:
            return _a_json({
                "error": f"{dev['nombre_completo']} no tiene horas suficientes ({dev_cap['horas_libres']}h libres, tarea requiere {horas_item}h)",
                "carga_actual": f"{dev_cap['porcentaje_carga']}%",
                "sugerencia": "Espera a que termine una tarea o asigna a otro dev",
                "devs_con_capacidad": [
                    f"{d['nombre_completo']} ({d['horas_libres']}h libres, {d['porcentaje_carga']}%)"
                    for d in capacidad if d["puede_recibir"] and d["horas_libres"] >= horas_item
                ]
            })
    elif params.get("auto"):
        # Filtrar por capacidad de horas (no WIP count)
        candidatos = [d for d in capacidad if d["puede_recibir"] and d["horas_libres"] >= horas_item]

        # Filtrar por skill
        if skills_req and candidatos:
            con_skill = [d for d in candidatos if any(s in (d.get("skills") or []) for s in skills_req)]
            if con_skill:
                candidatos = con_skill

        # Excluir Bug Guard de tareas normales (excepto bugs criticos)
        tipo_item = item.get("tipo", "") if item else ""
        if tipo_item not in ("Bug Critico", "Solicitud Bloqueante"):
            candidatos = [d for d in candidatos if not d.get("bug_guard_semana_actual")]

        if not candidatos:
            razon = []
            for d in capacidad:
                if not d["puede_recibir"]:
                    razon.append(f"{d['nombre_completo']}: carga al {d['porcentaje_carga']}% ({d['horas_libres']}h libres, necesita {horas_item}h)")
                elif skills_req and not any(s in (d.get("skills") or []) for s in skills_req):
                    razon.append(f"{d['nombre_completo']}: no tiene skill {skills_req}")
                elif d.get("bug_guard_semana_actual"):
                    razon.append(f"{d['nombre_completo']}: es Bug Guard (reservado para bugs)")
            return _a_json({
                "error": "No hay devs con capacidad para esta tarea",
                "horas_requeridas": horas_item,
                "skills_requeridos": skills_req,
                "razones": razon,
                "sugerencia": "Queda en Backlog hasta que un dev se libere"
            })

        # Preferir el con mas horas libres (balanceo de carga)
        candidatos.sort(key=lambda d: d["horas_libres"], reverse=True)
        dev = await q_devs.obtener_dev(conn, candidatos[0]["codigo"])
    else:
        return "Indica dev_nombre o auto=true"

    from datetime import datetime as dt
    data = {
        "dev_id": dev["id"],
        "dev_nombre": dev["nombre_completo"],
        "estado": "En Analisis",
        "fecha_asignacion": dt.now().isoformat(),
    }
    item = await q_backlog.actualizar_item(conn, codigo, data)
    await _sync_item_airtable(conn, codigo)
    return _a_json({"message": f"Asignado a {dev['nombre_completo']}", "dev": dev["nombre_completo"], "item": item})


async def _establecer_fechas(conn, params) -> str:
    """Establece deadlines."""
    codigo = params["codigo_o_busqueda"]
    codigo = _normalizar_codigo(codigo)
    if not codigo.startswith("BK-"):
        items = await q_backlog.buscar_items(conn, codigo, 1)
        if not items:
            return f"No encontre item con '{codigo}'"
        codigo = items[0]["codigo"]

    data = {}
    if params.get("deadline_interno"):
        data["deadline_interno"] = params["deadline_interno"]
    if params.get("fecha_qa_estimada"):
        data["fecha_qa_estimada"] = params["fecha_qa_estimada"]
    if params.get("deadline_cliente"):
        data["deadline_cliente"] = params["deadline_cliente"]

    item = await q_backlog.actualizar_item(conn, codigo, data)
    await _sync_item_airtable(conn, codigo)
    return _a_json({"message": "Fechas establecidas", "item": item})


async def _reportar_bloqueo(conn, params, usuario) -> str:
    """Reporta bloqueo en una tarea."""
    codigo = params["codigo_o_busqueda"]
    codigo = _normalizar_codigo(codigo)
    if not codigo.startswith("BK-"):
        items = await q_backlog.buscar_items(conn, codigo, 1)
        if not items:
            return f"No encontre item con '{codigo}'"
        codigo = items[0]["codigo"]

    item = await q_backlog.obtener_item(conn, codigo)
    if not item:
        return f"Item {codigo} no encontrado"

    nota_actual = item.get("notas_dev") or ""
    from datetime import datetime as dt
    nueva_nota = f"{nota_actual}\n[BLOQUEO {dt.now().strftime('%d/%m %H:%M')}]: {params['descripcion_bloqueo']}"

    await q_backlog.actualizar_item(conn, codigo, {"notas_dev": nueva_nota.strip()})
    await _sync_item_airtable(conn, codigo)
    return _a_json({"message": f"Bloqueo registrado en {codigo}", "bloqueo": params["descripcion_bloqueo"]})


async def _derivar_a_persona(conn, params) -> str:
    """Deriva un item a otra persona."""
    data = {}
    if params.get("codigo_o_busqueda"):
        codigo = params["codigo_o_busqueda"]
        codigo = _normalizar_codigo(codigo)
        if not codigo.startswith("BK-"):
            items = await q_backlog.buscar_items(conn, codigo, 1)
            if items:
                codigo = items[0]["codigo"]
        data = {"derivado_a": params["persona_destino"], "derivado_motivo": params["motivo"]}
        await q_backlog.actualizar_item(conn, codigo, data)
        await _sync_item_airtable(conn, codigo)

    return _a_json({
        "message": f"Derivado a {params['persona_destino']}",
        "motivo": params["motivo"],
        "requiere_analisis": params.get("requiere_analisis", False)
    })


async def _reasignar_bug_guard(conn, params) -> str:
    """Cambia el Bug Guard."""
    if params.get("dev_nombre"):
        dev = await q_devs.buscar_dev_por_nombre(conn, params["dev_nombre"])
        if not dev:
            return f"Dev '{params['dev_nombre']}' no encontrado"
    else:
        devs = await q_devs.listar_devs(conn, solo_disponibles=True)
        dev = devs[0] if devs else None
        if not dev:
            return "No hay devs disponibles"

    await q_devs.actualizar_dev(conn, dev["codigo"], {"bug_guard_semana_actual": True})
    return _a_json({"message": f"Bug Guard cambiado a {dev['nombre_completo']}", "dev": dev["nombre_completo"]})


async def _gestionar_cliente(conn, params) -> str:
    """CRUD completo de clientes y leads."""
    accion = params["accion"]

    def filtrar():
        """Filtra params y mapea nombres de campos tool→DB."""
        data = {}
        for k, v in params.items():
            if k in ("accion", "codigo_o_nombre") or v is None:
                continue
            # Mapear campos del tool a campos de la DB
            if k == "notas":
                data["notas_comerciales"] = v
            else:
                data[k] = v
        return data

    if accion == "crear_cliente":
        data = filtrar()
        cliente = await q_clientes.crear_cliente(conn, data)
        return _a_json({"message": "Cliente creado", "codigo": cliente["codigo"], "data": cliente})

    elif accion == "actualizar_cliente":
        nombre = params.get("codigo_o_nombre", "")
        data = filtrar()
        if nombre.startswith("CLI-"):
            cliente = await q_clientes.actualizar_cliente(conn, nombre, data)
        else:
            found = await q_clientes.buscar_cliente_por_nombre(conn, nombre)
            if not found:
                return f"Cliente '{nombre}' no encontrado"
            cliente = await q_clientes.actualizar_cliente(conn, found["codigo"], data)
        if not cliente:
            return "No se pudo actualizar"
        return _a_json({"message": "Cliente actualizado", "data": cliente})

    elif accion == "crear_lead":
        data = filtrar()
        lead = await q_leads.crear_lead(conn, data)
        return _a_json({"message": "Lead creado", "codigo": lead["codigo"], "data": lead})

    elif accion == "actualizar_lead":
        nombre = params.get("codigo_o_nombre", "")
        data = filtrar()
        if nombre.startswith("LED-"):
            lead = await q_leads.actualizar_lead(conn, nombre, data)
        else:
            found = await q_leads.buscar_lead_por_nombre(conn, nombre)
            if not found:
                return f"Lead '{nombre}' no encontrado"
            lead = await q_leads.actualizar_lead(conn, found["codigo"], data)
        if not lead:
            return "No se pudo actualizar"
        return _a_json({"message": "Lead actualizado", "data": lead})

    elif accion == "convertir_lead":
        nombre = params.get("codigo_o_nombre", "")
        data = filtrar()
        if nombre.startswith("LED-"):
            codigo_lead = nombre
        else:
            found = await q_leads.buscar_lead_por_nombre(conn, nombre)
            if not found:
                return f"Lead '{nombre}' no encontrado"
            codigo_lead = found["codigo"]
        result = await q_leads.convertir_lead_a_cliente(conn, codigo_lead, data)
        if "error" in result:
            return result["error"]
        return _a_json({
            "message": f"Lead convertido a cliente",
            "lead_codigo": codigo_lead,
            "cliente_codigo": result["cliente"]["codigo"],
            "data": result["cliente"]
        })

    return f"Accion '{accion}' no reconocida. Usa: crear_cliente, actualizar_cliente, crear_lead, actualizar_lead, convertir_lead"


async def _gestionar_dev(conn, params) -> str:
    """CRUD de desarrolladores."""
    accion = params["accion"]

    if accion == "crear_dev":
        data = {k: v for k, v in params.items() if k not in ("accion", "dar_acceso_bot") and v is not None}
        dev = await q_devs.crear_dev(conn, data)
        return _a_json({"message": "Dev creado", "codigo": dev["codigo"], "data": dev})

    elif accion == "actualizar_dev":
        nombre = params.get("codigo_o_nombre", "")
        data = {k: v for k, v in params.items() if k not in ("accion", "codigo_o_nombre", "dar_acceso_bot") and v is not None}
        # Convertir jornada a horas si viene
        if "jornada" in data:
            from app.db.queries.desarrolladores import JORNADA_HORAS
            data["horas_semana_base"] = JORNADA_HORAS.get(data.pop("jornada"), 40)
        if nombre.startswith("DEV-"):
            dev = await q_devs.actualizar_dev(conn, nombre, data)
        else:
            found = await q_devs.buscar_dev_por_nombre(conn, nombre)
            if not found:
                return f"Dev '{nombre}' no encontrado"
            dev = await q_devs.actualizar_dev(conn, found["codigo"], data)
        return _a_json({"message": "Dev actualizado", "data": dev})

    return f"Accion '{accion}' no implementada aun"


async def _adjuntar_imagen(conn, params, usuario) -> str:
    """
    Adjunta imagenes recientes del usuario a un item del backlog.
    Busca imagenes enviadas en los ultimos 10 minutos.
    """
    codigo = params.get("codigo_o_busqueda", "")
    codigo = _normalizar_codigo(codigo)
    if not codigo.startswith("BK-"):
        items = await q_backlog.buscar_items(conn, codigo, 1)
        if not items:
            return f"No encontre item con '{codigo}'"
        codigo = items[0]["codigo"]

    item = await q_backlog.obtener_item(conn, codigo)
    if not item:
        return f"Item {codigo} no encontrado"

    # Buscar imagenes recientes del usuario
    imagenes = await conn.fetch(
        """SELECT media_url FROM mensajes_conversacion
           WHERE usuario_id = $1 AND tipo_contenido = 'imagen'
           AND media_url IS NOT NULL
           AND created_at > NOW() - INTERVAL '10 minutes'
           ORDER BY created_at DESC LIMIT 5""",
        usuario["id"]
    )

    if not imagenes:
        return "No encontre imagenes recientes tuyas para adjuntar"

    # Agregar URLs al array existente
    adjuntos_actuales = item.get("adjuntos_urls") or []
    nuevos = 0
    for img in imagenes:
        if img["media_url"] not in adjuntos_actuales:
            adjuntos_actuales.append(img["media_url"])
            nuevos += 1

    if nuevos == 0:
        return f"Las imagenes ya estan adjuntas en {codigo}"

    await q_backlog.actualizar_item(conn, codigo, {"adjuntos_urls": adjuntos_actuales})
    await _sync_item_airtable(conn, codigo)

    return _a_json({
        "message": f"{nuevos} imagen(es) adjuntada(s) a {codigo}",
        "codigo": codigo,
        "total_adjuntos": len(adjuntos_actuales)
    })
