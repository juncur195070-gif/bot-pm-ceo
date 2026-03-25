"""
Tool Executor — Ejecuta los tools que Claude solicita.

Patron de verificacion (Read-After-Write):
  1. Ejecutar operacion en DB
  2. Leer de vuelta el registro para CONFIRMAR que existe
  3. Retornar envelope {ok: true/false} para que Claude sepa si funciono

Claude SOLO puede confirmar acciones si ok=true.
"""

import json
import asyncpg
from datetime import date, datetime, timedelta

from app.config.settings import settings
from app.db.queries.backlog import _normalizar_codigo
from app.db.queries import clientes as q_clientes
from app.db.queries import desarrolladores as q_devs
from app.db.queries import backlog as q_backlog
from app.db.queries import metricas as q_metricas
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


def _ok(data: dict) -> str:
    """Retorna envelope de exito. Claude SOLO confirma si ve ok=true."""
    return _a_json({"ok": True, **data})


def _fail(error: str, **extra) -> str:
    """Retorna envelope de error. Claude debe informar el fallo al usuario."""
    return _a_json({"ok": False, "error": error, **extra})


def _buscar_codigo(params, key="codigo_o_busqueda") -> str:
    """Extrae y normaliza codigo de los params."""
    return _normalizar_codigo(params[key])


async def _resolver_codigo(conn, codigo: str) -> tuple[str | None, str | None]:
    """Resuelve un texto/codigo a un codigo BK-XXXX valido. Retorna (codigo, error)."""
    codigo = _normalizar_codigo(codigo)
    if codigo.startswith("BK-"):
        return codigo, None
    items = await q_backlog.buscar_items(conn, codigo, 1)
    if not items:
        return None, f"No encontre item con '{codigo}'"
    return items[0]["codigo"], None


async def _sync_item_airtable(conn, codigo: str):
    """Sincroniza un item a Airtable en background (no bloquea respuesta)."""
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

    asyncio.create_task(_do_sync())


# Campos que el dev NO debe ver (datos financieros)
_CAMPOS_OCULTOS_DEV = {
    "cliente_mrr", "mrr_mensual", "arr_anual", "arr_calculado",
    "score_financiero", "notas_comerciales",
}


def _filtrar_para_dev(resultado: str) -> str:
    """Elimina campos financieros del resultado antes de enviarlo al dev."""
    try:
        data = json.loads(resultado)
        _limpiar_recursivo(data)
        return json.dumps(data, default=_serializar, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        return resultado


def _limpiar_recursivo(obj):
    """Elimina campos sensibles de dicts y listas recursivamente."""
    if isinstance(obj, dict):
        for campo in _CAMPOS_OCULTOS_DEV:
            obj.pop(campo, None)
        for v in obj.values():
            _limpiar_recursivo(v)
    elif isinstance(obj, list):
        for item in obj:
            _limpiar_recursivo(item)


async def ejecutar_tool(
    nombre: str,
    params: dict,
    conn: asyncpg.Connection,
    usuario: dict
) -> str:
    """
    Ejecuta un tool y retorna el resultado como JSON string.

    SIEMPRE retorna envelope {ok: true/false, ...}
    - ok=true: Claude puede confirmar al usuario
    - ok=false: Claude debe informar el error

    Si el usuario es dev, filtra campos financieros del resultado.
    """
    es_dev = usuario.get("rol") == "desarrollador"

    try:
        if nombre == "consultar_backlog":
            result = await _consultar_backlog(conn, params, usuario)
        elif nombre == "consultar_item":
            result = await _consultar_item(conn, params, usuario)
        elif nombre == "consultar_equipo":
            result = await _consultar_equipo(conn, params)
        elif nombre == "consultar_metricas":
            result = await _consultar_metricas(conn, params)
        elif nombre == "consultar_cliente":
            result = await _consultar_cliente(conn, params)
        elif nombre == "crear_item":
            result = await _crear_item(conn, params, usuario)
        elif nombre == "actualizar_item":
            result = await _actualizar_item(conn, params, usuario)
        elif nombre == "asignar_tarea":
            result = await _asignar_tarea(conn, params)
        elif nombre == "establecer_fechas":
            result = await _establecer_fechas(conn, params)
        elif nombre == "reportar_bloqueo":
            result = await _reportar_bloqueo(conn, params, usuario)
        elif nombre == "derivar_a_persona":
            result = await _derivar_a_persona(conn, params)
        elif nombre == "reasignar_bug_guard":
            result = await _reasignar_bug_guard(conn, params)
        elif nombre == "gestionar_cliente":
            result = await _gestionar_cliente(conn, params)
        elif nombre == "gestionar_dev":
            result = await _gestionar_dev(conn, params)
        elif nombre == "adjuntar_imagen":
            result = await _adjuntar_imagen(conn, params, usuario)
        elif nombre == "actualizar_estado_dev":
            result = await _actualizar_estado_dev(conn, params, usuario)
        else:
            result = _fail(f"Tool '{nombre}' no reconocido")
    except Exception as e:
        result = _fail(f"Error ejecutando {nombre}: {str(e)}")

    # Filtrar campos financieros para devs
    if es_dev:
        result = _filtrar_para_dev(result)
    return result


# ── Consultas (read-only, no necesitan verificacion) ──

async def _consultar_backlog(conn, params, usuario) -> str:
    """Busca items en el backlog con filtros. Devs solo ven sus tareas."""
    # Si es dev, forzar filtro por su dev_id
    dev_id_filtro = None
    if usuario.get("rol") == "desarrollador":
        dev_id_filtro = usuario.get("desarrollador_id") or usuario.get("id")

    if params.get("busqueda_texto"):
        items = await q_backlog.buscar_items(conn, params["busqueda_texto"], params.get("top_n", 5))
        # Filtrar por dev si aplica
        if dev_id_filtro:
            items = [i for i in items if str(i.get("dev_id")) == str(dev_id_filtro)]
        return _ok({"items": items, "total": len(items)})

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
    return _ok({"items": items, "total": total})


async def _consultar_item(conn, params, usuario) -> str:
    """Detalle de un item por codigo o busqueda. Devs solo ven sus tareas."""
    dev_id_filtro = None
    if usuario.get("rol") == "desarrollador":
        dev_id_filtro = usuario.get("desarrollador_id") or usuario.get("id")

    if params.get("codigo"):
        item = await q_backlog.obtener_item(conn, params["codigo"])
        if not item:
            return _fail("Item no encontrado con ese codigo")
        if dev_id_filtro and str(item.get("dev_id")) != str(dev_id_filtro):
            return _fail("Ese item no esta asignado a ti")
        return _ok({"item": item})

    if params.get("busqueda_texto") or params.get("cliente"):
        texto = params.get("busqueda_texto", params.get("cliente", ""))
        items = await q_backlog.buscar_items(conn, texto, 3)
        if dev_id_filtro:
            items = [i for i in items if str(i.get("dev_id")) == str(dev_id_filtro)]
        if items:
            return _ok({"item": items[0]})
        return _fail("No se encontraron items asignados a ti con esa busqueda")

    return _fail("Necesito un codigo BK-XXXX o texto para buscar")


async def _consultar_equipo(conn, params) -> str:
    """Estado del equipo."""
    if params.get("skill_requerido"):
        capacidad = await q_devs.obtener_capacidad_equipo(conn)
        filtrados = [d for d in capacidad if params["skill_requerido"] in d.get("skills", [])]
        return _ok({"equipo": filtrados})

    capacidad = await q_devs.obtener_capacidad_equipo(conn)
    bug_guard = await q_devs.obtener_bug_guard(conn)
    return _ok({"equipo": capacidad, "bug_guard": bug_guard})


async def _consultar_metricas(conn, params) -> str:
    """Dashboard y metricas."""
    tipo = params.get("tipo_metrica", "general")
    periodo = params.get("periodo", "esta_semana")

    if tipo == "por_dev":
        data = await q_metricas.rendimiento_por_dev(conn, periodo)
        return _ok({"metricas": data})

    dashboard = await q_metricas.dashboard_general(conn, periodo)
    return _ok({"metricas": dashboard})


async def _consultar_cliente(conn, params) -> str:
    """Datos de un cliente."""
    if params.get("riesgo_churn"):
        clientes = await q_clientes.obtener_clientes_riesgo_churn(conn)
        return _ok({"clientes": clientes})

    if params.get("nombre"):
        cliente = await q_clientes.buscar_cliente_por_nombre(conn, params["nombre"])
        if cliente:
            return _ok({"cliente": cliente})
        return _fail(f"Cliente '{params['nombre']}' no encontrado")

    if params.get("listar_todos"):
        clientes, _ = await q_clientes.listar_clientes(conn, per_page=50)
        return _ok({"clientes": clientes})

    return _fail("Indica el nombre del cliente o usa listar_todos=true")


# ── Operaciones de escritura (con verificacion read-after-write) ──

async def _crear_item(conn, params, usuario) -> str:
    """Crea un item en el backlog con verificacion."""
    # Buscar cliente o lead si se menciona
    cliente_data = {}
    if params.get("cliente"):
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
            lead = await q_leads.buscar_lead_por_nombre(conn, params["cliente"])
            if lead:
                cliente_data = {
                    "es_lead": True,
                    "lead_id": lead["id"],
                    "cliente_nombre": lead["nombre_clinica"],
                }
            else:
                # Cliente no existe — listar existentes para ayudar
                clientes_existentes, _ = await q_clientes.listar_clientes(conn, per_page=50)
                nombres = [c["nombre_clinica"] for c in clientes_existentes]
                return _fail(
                    f"Cliente '{params['cliente']}' no existe en la BD. Primero crealo con gestionar_cliente.",
                    clientes_existentes=nombres if nombres else "No hay clientes registrados",
                    sugerencia="Usa gestionar_cliente con accion='crear_cliente' para registrarlo primero, luego crea el item."
                )

    # Recoger imagenes recientes
    adjuntos = params.get("adjuntos_urls", [])
    imagenes_recientes = await conn.fetch(
        """SELECT media_url FROM mensajes_conversacion
           WHERE usuario_id = $1 AND tipo_contenido = 'imagen'
           AND media_url IS NOT NULL
           AND created_at > NOW() - make_interval(mins => $2)
           ORDER BY created_at DESC LIMIT 5""",
        usuario["id"], settings.IMAGEN_RECIENTE_MINUTOS
    )
    for img in imagenes_recientes:
        if img["media_url"] and img["media_url"] not in adjuntos:
            adjuntos.append(img["media_url"])

    # Proteccion anti-duplicados: si ya existe un item muy similar creado hace <5min, advertir
    titulo_nuevo = params["titulo"]
    duplicado = await conn.fetchrow(
        """SELECT codigo, titulo, dev_nombre FROM backlog_items
           WHERE unaccent(LOWER(titulo)) LIKE unaccent(LOWER($1))
           AND created_at > NOW() - INTERVAL '5 minutes'
           LIMIT 1""",
        f"%{titulo_nuevo[:20]}%"
    )
    if duplicado:
        return _fail(
            f"Ya existe un item similar creado hace menos de 5 minutos: {duplicado['codigo']} '{duplicado['titulo']}' (dev: {duplicado['dev_nombre'] or 'sin asignar'}). "
            f"Si quieres REASIGNAR ese item, usa asignar_tarea. No crees duplicados."
        )

    data = {
        "titulo": params["titulo"],
        "tipo": params["tipo"],
        "estado": "Backlog",  # Siempre se crea en Backlog
        "descripcion": params.get("descripcion", ""),
        "urgencia_declarada": params.get("urgencia"),
        "esfuerzo_talla": params.get("esfuerzo_talla"),
        "skill_requerido": [params["skill_requerido"]] if params.get("skill_requerido") else [],
        "adjuntos_urls": adjuntos,
        "reportado_por_id": usuario["id"],
        **cliente_data,
    }

    # Auto-deadline para Bug Critico y Solicitud Bloqueante
    if params["tipo"] in ("Bug Critico", "Solicitud Bloqueante") and not data.get("deadline_interno"):
        data["deadline_interno"] = (date.today() + timedelta(days=settings.DEADLINE_AUTO_DIAS)).isoformat()

    item = await q_backlog.crear_item(conn, data)

    # ── VERIFICACION READ-AFTER-WRITE ──
    verificado = await q_backlog.obtener_item(conn, item["codigo"])
    if not verificado:
        return _fail(f"El item se intento crear pero NO se verifico en la BD. Codigo esperado: {item.get('codigo')}")

    # ── SCORING WSJF INICIAL (triage inmediato) ──
    try:
        from app.scheduled.scoring import _calcular_score
        # Cargar datos del cliente para scoring completo
        scoring_cliente = None
        if verificado.get("cliente_id"):
            scoring_cliente = await conn.fetchrow(
                "SELECT * FROM clientes WHERE id = $1", verificado["cliente_id"]
            )
            if scoring_cliente:
                scoring_cliente = dict(scoring_cliente)
        scores = _calcular_score(dict(verificado), scoring_cliente)
        await conn.execute(
            """UPDATE backlog_items SET
                score_wsjf = $1, score_bloque_a = $2, score_bloque_b = $3, score_bloque_c = $4
               WHERE codigo = $5""",
            scores["score_wsjf"], scores["score_bloque_a"],
            scores["score_bloque_b"], scores["score_bloque_c"],
            verificado["codigo"]
        )
        # Releer con score actualizado
        verificado = await q_backlog.obtener_item(conn, verificado["codigo"])
    except Exception as e:
        print(f"  ⚠ Scoring inicial fallo (no bloquea): {e}")

    # Si es Bug Critico → asignacion de emergencia al Bug Guard
    emergencia_asignada = False
    if params["tipo"] in ("Bug Critico", "Solicitud Bloqueante"):
        try:
            from app.scheduled.emergencia import asignar_emergencia
            await asignar_emergencia(
                conn, verificado["id"], verificado["codigo"], verificado["titulo"],
                cliente_data.get("cliente_nombre")
            )
            # Releer item para ver si se asigno
            verificado = await q_backlog.obtener_item(conn, verificado["codigo"])
            if verificado and verificado.get("dev_nombre"):
                emergencia_asignada = True
        except Exception as e:
            print(f"  ⚠ Emergencia fallo: {e}")

    # Sync a Airtable (background — no bloquea respuesta al usuario)
    await _sync_item_airtable(conn, verificado["codigo"])

    # ── SUGERENCIA DE ASIGNACION ──
    sugerencia = None

    # Si la emergencia ya asigno al Bug Guard, informar directamente (no sugerir otro)
    if emergencia_asignada:
        sugerencia = {
            "dev_sugerido": verificado["dev_nombre"],
            "razon": f"Asignado automaticamente al Bug Guard ({verificado['dev_nombre']}) por ser {params['tipo']}",
            "estado": "asignado_emergencia",
        }
    if not emergencia_asignada:
      try:
        capacidad = await q_devs.obtener_capacidad_equipo(conn)
        horas_item = {"XS": 2, "S": 4, "M": 8, "L": 16, "XL": 32}.get(params.get("esfuerzo_talla", ""), 4)
        tipo_item = params["tipo"]
        skills_req = [params["skill_requerido"]] if params.get("skill_requerido") else []

        # Filtrar candidatos
        candidatos = []
        for d in capacidad:
            if not d.get("disponible", True):
                continue
            # Bug Guard solo recibe bugs/bloqueantes
            if d.get("bug_guard_semana_actual") and tipo_item not in ("Bug Critico", "Solicitud Bloqueante"):
                continue
            # Verificar skills
            if skills_req:
                dev_skills = d.get("skills") or []
                if not any(s in dev_skills for s in skills_req):
                    continue
            candidatos.append(d)

        if candidatos:
            # Separar: con capacidad vs sin capacidad
            con_horas = [d for d in candidatos if d.get("horas_libres", 0) >= horas_item]
            sin_horas = [d for d in candidatos if d.get("horas_libres", 0) < horas_item]

            if con_horas:
                # Elegir el de más horas libres (balanceo)
                con_horas.sort(key=lambda d: d.get("horas_libres", 0), reverse=True)
                mejor = con_horas[0]
                sugerencia = {
                    "dev_sugerido": mejor["nombre_completo"],
                    "horas_libres": mejor.get("horas_libres", 0),
                    "porcentaje_carga": mejor.get("porcentaje_carga", 0),
                    "razon": f"Mas horas libres ({mejor.get('horas_libres', 0)}h), carga al {mejor.get('porcentaje_carga', 0)}%",
                    "alternativas": [
                        f"{d['nombre_completo']} ({d.get('horas_libres', 0)}h libres, {d.get('porcentaje_carga', 0)}%)"
                        for d in con_horas[1:3]
                    ],
                    "estado": "disponible",
                }
                # Si es Bug Critico y hay Bug Guard, priorizar Bug Guard
                if tipo_item in ("Bug Critico", "Solicitud Bloqueante"):
                    bg = next((d for d in con_horas if d.get("bug_guard_semana_actual")), None)
                    if bg:
                        sugerencia["dev_sugerido"] = bg["nombre_completo"]
                        sugerencia["razon"] = f"Es Bug Guard esta semana ({bg.get('horas_libres', 0)}h libres)"
            elif sin_horas:
                # Todos al limite — informar
                menos_cargado = min(sin_horas, key=lambda d: d.get("porcentaje_carga", 100))
                sugerencia = {
                    "dev_sugerido": menos_cargado["nombre_completo"],
                    "horas_libres": menos_cargado.get("horas_libres", 0),
                    "porcentaje_carga": menos_cargado.get("porcentaje_carga", 0),
                    "razon": f"Todos al limite. {menos_cargado['nombre_completo']} es el menos cargado ({menos_cargado.get('porcentaje_carga', 0)}%)",
                    "estado": "sobrecargado",
                    "advertencia": f"Requiere {horas_item}h pero solo tiene {menos_cargado.get('horas_libres', 0)}h libres",
                }
      except Exception as e:
        print(f"  ⚠ Sugerencia de asignacion fallo (no bloquea): {e}")

    result = {"message": "Item creado y verificado en BD", "codigo": verificado["codigo"], "score_wsjf": verificado.get("score_wsjf"), "item": verificado}
    if sugerencia:
        result["sugerencia_asignacion"] = sugerencia
    return _ok(result)


async def _actualizar_item(conn, params, usuario) -> str:
    """Actualiza cualquier campo de un item con verificacion."""
    codigo, err = await _resolver_codigo(conn, params["codigo_o_busqueda"])
    if err:
        return _fail(err)

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
                return _fail(f"No encontre cliente ni lead con nombre '{params['cliente']}'")

    if not data:
        return _fail("No se especifico ningun campo para actualizar")

    item = await q_backlog.actualizar_item(conn, codigo, data)
    if not item:
        return _fail(f"Item {codigo} no encontrado o no se pudo actualizar")

    # ── VERIFICACION READ-AFTER-WRITE ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado:
        return _fail(f"La actualizacion de {codigo} no se verifico en la BD")

    # Verificar que los campos se aplicaron
    campos_no_aplicados = []
    for key, value in data.items():
        if key in ("cliente_id", "cliente_mrr", "cliente_tamano", "cliente_sla_dias", "lead_id"):
            continue
        db_val = verificado.get(key)
        if db_val is not None and value is not None and str(db_val) != str(value):
            campos_no_aplicados.append(key)

    if campos_no_aplicados:
        return _fail(f"Campos no se aplicaron correctamente: {campos_no_aplicados}", item=verificado)

    # Si se marco como Desplegado, actualizar fecha_ultimo_item_resuelto del cliente
    if params.get("estado") == "Desplegado" and verificado.get("cliente_id"):
        await conn.execute(
            "UPDATE clientes SET fecha_ultimo_item_resuelto = NOW() WHERE id = $1",
            verificado["cliente_id"]
        )

    await _sync_item_airtable(conn, codigo)

    cambios = [k for k in data.keys() if k not in ("cliente_id", "cliente_mrr", "cliente_tamano", "cliente_sla_dias", "lead_id")]
    return _ok({"message": f"Item {codigo} actualizado y verificado: {', '.join(cambios)}", "item": verificado})


async def _asignar_tarea(conn, params) -> str:
    """Asigna o desasigna un item a un dev con verificacion."""
    codigo, err = await _resolver_codigo(conn, params["codigo_o_busqueda"])
    if err:
        return _fail(err)

    item = await q_backlog.obtener_item(conn, codigo)
    if not item:
        return _fail(f"Item {codigo} no encontrado")

    # Desasignar: quitar dev y volver a Backlog
    if params.get("desasignar"):
        dev_anterior = item.get("dev_nombre") or "nadie"
        await conn.execute(
            """UPDATE backlog_items SET
                dev_id = NULL, dev_nombre = NULL,
                estado = 'Backlog', fecha_asignacion = NULL
               WHERE codigo = $1""",
            codigo
        )
        verificado = await q_backlog.obtener_item(conn, codigo)
        if verificado and verificado.get("dev_id") is None:
            await _sync_item_airtable(conn, codigo)
            return _ok({"message": f"{codigo} desasignado de {dev_anterior} y devuelto a Backlog", "codigo": codigo, "item": verificado})
        return _fail(f"No se pudo desasignar {codigo}")

    # Si auto=true y ya tiene dev asignado, no reasignar (proteger asignaciones existentes)
    if params.get("auto") and item.get("dev_id"):
        return _ok({
            "message": f"{codigo} ya esta asignado a {item.get('dev_nombre')}. No se reasigno.",
            "codigo": codigo,
            "dev_actual": item.get("dev_nombre"),
            "ya_asignado": True
        })

    skills_req = item.get("skill_requerido", [])
    horas_item = item.get("horas_esfuerzo") or 4

    capacidad = await q_devs.obtener_capacidad_equipo(conn)

    if params.get("dev_nombre"):
        dev = await q_devs.buscar_dev_por_nombre(conn, params["dev_nombre"])
        if not dev:
            return _fail(f"Dev '{params['dev_nombre']}' no encontrado")
        dev_cap = next((d for d in capacidad if d["codigo"] == dev["codigo"]), None)
        if dev_cap and dev_cap["horas_libres"] < horas_item:
            return _fail(
                f"{dev['nombre_completo']} no tiene horas suficientes ({dev_cap['horas_libres']}h libres, tarea requiere {horas_item}h)",
                carga_actual=f"{dev_cap['porcentaje_carga']}%",
                sugerencia="Espera a que termine una tarea o asigna a otro dev",
                devs_con_capacidad=[
                    f"{d['nombre_completo']} ({d['horas_libres']}h libres, {d['porcentaje_carga']}%)"
                    for d in capacidad if d["puede_recibir"] and d["horas_libres"] >= horas_item
                ]
            )
    elif params.get("auto"):
        candidatos = [d for d in capacidad if d["puede_recibir"] and d["horas_libres"] >= horas_item]

        if skills_req and candidatos:
            con_skill = [d for d in candidatos if any(s in (d.get("skills") or []) for s in skills_req)]
            if con_skill:
                candidatos = con_skill

        tipo_item = item.get("tipo", "")
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
            return _fail(
                "No hay devs con capacidad para esta tarea",
                horas_requeridas=horas_item,
                skills_requeridos=skills_req,
                razones=razon,
                sugerencia="Queda en Backlog hasta que un dev se libere"
            )

        candidatos.sort(key=lambda d: d["horas_libres"], reverse=True)
        dev = await q_devs.obtener_dev(conn, candidatos[0]["codigo"])
    else:
        return _fail("Indica dev_nombre o auto=true")

    data = {
        "dev_id": dev["id"],
        "dev_nombre": dev["nombre_completo"],
        "fecha_asignacion": datetime.now(),
    }
    await q_backlog.actualizar_item(conn, codigo, data)

    # ── VERIFICACION READ-AFTER-WRITE ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado or str(verificado.get("dev_id")) != str(dev["id"]):
        return _fail(f"La asignacion de {codigo} a {dev['nombre_completo']} NO se verifico en la BD")

    await _sync_item_airtable(conn, codigo)
    return _ok({
        "message": f"{codigo} asignado a {dev['nombre_completo']} y verificado en BD",
        "dev": dev["nombre_completo"],
        "item": verificado
    })


async def _establecer_fechas(conn, params) -> str:
    """Establece deadlines con verificacion."""
    codigo, err = await _resolver_codigo(conn, params["codigo_o_busqueda"])
    if err:
        return _fail(err)

    data = {}
    if params.get("deadline_interno"):
        data["deadline_interno"] = params["deadline_interno"]
    if params.get("fecha_qa_estimada"):
        data["fecha_qa_estimada"] = params["fecha_qa_estimada"]
    if params.get("deadline_cliente"):
        data["deadline_cliente"] = params["deadline_cliente"]

    item = await q_backlog.actualizar_item(conn, codigo, data)
    if not item:
        return _fail(f"Item {codigo} no encontrado")

    # ── VERIFICACION ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado:
        return _fail(f"Fechas de {codigo} no se verificaron en la BD")

    await _sync_item_airtable(conn, codigo)
    return _ok({"message": "Fechas establecidas y verificadas", "item": verificado})


async def _reportar_bloqueo(conn, params, usuario) -> str:
    """Reporta bloqueo en una tarea con verificacion."""
    codigo, err = await _resolver_codigo(conn, params["codigo_o_busqueda"])
    if err:
        return _fail(err)

    item = await q_backlog.obtener_item(conn, codigo)
    if not item:
        return _fail(f"Item {codigo} no encontrado")

    nota_actual = item.get("notas_dev") or ""
    nueva_nota = f"{nota_actual}\n[BLOQUEO {datetime.now().strftime('%d/%m %H:%M')}]: {params['descripcion_bloqueo']}"

    await q_backlog.actualizar_item(conn, codigo, {"notas_dev": nueva_nota.strip()})

    # ── VERIFICACION ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado or params["descripcion_bloqueo"] not in (verificado.get("notas_dev") or ""):
        return _fail(f"El bloqueo de {codigo} no se guardo correctamente en la BD")

    await _sync_item_airtable(conn, codigo)
    return _ok({"message": f"Bloqueo registrado y verificado en {codigo}", "bloqueo": params["descripcion_bloqueo"]})


async def _derivar_a_persona(conn, params) -> str:
    """Deriva un item a otra persona con verificacion."""
    if params.get("codigo_o_busqueda"):
        codigo, err = await _resolver_codigo(conn, params["codigo_o_busqueda"])
        if err:
            return _fail(err)

        data = {"derivado_a": params["persona_destino"], "derivado_motivo": params["motivo"]}
        await q_backlog.actualizar_item(conn, codigo, data)

        # ── VERIFICACION ──
        verificado = await q_backlog.obtener_item(conn, codigo)
        if not verificado or verificado.get("derivado_a") != params["persona_destino"]:
            return _fail(f"La derivacion de {codigo} no se verifico en la BD")

        await _sync_item_airtable(conn, codigo)

    return _ok({
        "message": f"Derivado a {params['persona_destino']} y verificado",
        "motivo": params["motivo"],
        "requiere_analisis": params.get("requiere_analisis", False)
    })


async def _reasignar_bug_guard(conn, params) -> str:
    """Cambia el Bug Guard: quita al anterior, asigna al nuevo, registra historial."""
    from datetime import date, datetime, timedelta
    import pytz
    LIMA_TZ = pytz.timezone("America/Lima")

    # Determinar nuevo Bug Guard
    if params.get("dev_nombre"):
        dev = await q_devs.buscar_dev_por_nombre(conn, params["dev_nombre"])
        if not dev:
            return _fail(f"Dev '{params['dev_nombre']}' no encontrado")
        if not dev.get("disponible"):
            return _fail(f"{dev['nombre_completo']} no esta disponible")
    elif params.get("siguiente"):
        # Auto-seleccionar siguiente en rotacion (menor historial, no consecutivo)
        devs = await conn.fetch(
            """SELECT * FROM desarrolladores
               WHERE disponible = TRUE
               ORDER BY historial_semanas_bug_guard ASC, nombre_completo ASC"""
        )
        hace_7_dias = date.today() - timedelta(days=7)
        dev = None
        for d in devs:
            if d["ultima_semana_bug_guard"] and d["ultima_semana_bug_guard"] > hace_7_dias:
                continue
            dev = dict(d)
            break
        if not dev and devs:
            dev = dict(devs[0])
        if not dev:
            return _fail("No hay devs disponibles para Bug Guard")
    else:
        return _fail("Indica dev_nombre o siguiente=true")

    # Quitar Bug Guard anterior
    bg_anterior = await conn.fetchrow(
        "SELECT codigo, nombre_completo FROM desarrolladores WHERE bug_guard_semana_actual = TRUE"
    )
    if bg_anterior:
        await conn.execute(
            "UPDATE desarrolladores SET bug_guard_semana_actual = FALSE WHERE bug_guard_semana_actual = TRUE"
        )

    # Asignar nuevo Bug Guard
    await conn.execute(
        """UPDATE desarrolladores SET
            bug_guard_semana_actual = TRUE,
            ultima_semana_bug_guard = $1,
            historial_semanas_bug_guard = historial_semanas_bug_guard + 1
           WHERE id = $2""",
        date.today(), dev["id"]
    )

    # Registrar en historial
    semana = f"S{datetime.now(LIMA_TZ).isocalendar()[1]}-{datetime.now(LIMA_TZ).year}"
    await conn.execute(
        """INSERT INTO bug_guard_historial
           (semana_codigo, fecha_inicio_semana, dev_id, dev_nombre, horas_reservadas)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (semana_codigo) DO UPDATE SET
           dev_id = EXCLUDED.dev_id, dev_nombre = EXCLUDED.dev_nombre,
           fecha_inicio_semana = EXCLUDED.fecha_inicio_semana""",
        semana, date.today(), dev["id"], dev["nombre_completo"],
        int(dev.get("horas_semana_base", 30) * settings.BUG_GUARD_RATIO)
    )

    # Verificacion
    verificado = await q_devs.obtener_dev(conn, dev["codigo"])
    if not verificado or not verificado.get("bug_guard_semana_actual"):
        return _fail(f"Bug Guard no se cambio correctamente a {dev['nombre_completo']}")

    # Calcular quien sigue en rotacion
    siguiente_devs = await conn.fetch(
        """SELECT nombre_completo, historial_semanas_bug_guard
           FROM desarrolladores
           WHERE disponible = TRUE AND id != $1
           ORDER BY historial_semanas_bug_guard ASC, nombre_completo ASC
           LIMIT 3""",
        dev["id"]
    )
    siguiente = [f"{d['nombre_completo']} ({d['historial_semanas_bug_guard']} semanas)" for d in siguiente_devs]

    anterior_nombre = bg_anterior["nombre_completo"] if bg_anterior else "ninguno"
    return _ok({
        "message": f"Bug Guard cambiado de {anterior_nombre} a {dev['nombre_completo']}",
        "bug_guard_actual": dev["nombre_completo"],
        "bug_guard_anterior": anterior_nombre,
        "siguiente_en_rotacion": siguiente or ["No hay mas devs disponibles"],
    })


async def _gestionar_cliente(conn, params) -> str:
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
            return _fail(f"Cliente se intento crear pero NO se verifico en BD")
        return _ok({"message": "Cliente creado y verificado", "codigo": verificado["codigo"], "data": verificado})

    elif accion == "actualizar_cliente":
        nombre = params.get("codigo_o_nombre", "")
        data = filtrar()
        if nombre.startswith("CLI-"):
            cliente = await q_clientes.actualizar_cliente(conn, nombre, data)
        else:
            found = await q_clientes.buscar_cliente_por_nombre(conn, nombre)
            if not found:
                return _fail(f"Cliente '{nombre}' no encontrado")
            cliente = await q_clientes.actualizar_cliente(conn, found["codigo"], data)
        if not cliente:
            return _fail("No se pudo actualizar el cliente")
        return _ok({"message": "Cliente actualizado y verificado", "data": cliente})

    elif accion == "crear_lead":
        data = filtrar()
        lead = await q_leads.crear_lead(conn, data)
        verificado = await q_leads.obtener_lead(conn, lead["codigo"])
        if not verificado:
            return _fail("Lead se intento crear pero NO se verifico en BD")
        return _ok({"message": "Lead creado y verificado", "codigo": verificado["codigo"], "data": verificado})

    elif accion == "actualizar_lead":
        nombre = params.get("codigo_o_nombre", "")
        data = filtrar()
        if nombre.startswith("LED-"):
            lead = await q_leads.actualizar_lead(conn, nombre, data)
        else:
            found = await q_leads.buscar_lead_por_nombre(conn, nombre)
            if not found:
                return _fail(f"Lead '{nombre}' no encontrado")
            lead = await q_leads.actualizar_lead(conn, found["codigo"], data)
        if not lead:
            return _fail("No se pudo actualizar el lead")
        return _ok({"message": "Lead actualizado y verificado", "data": lead})

    elif accion == "convertir_lead":
        nombre = params.get("codigo_o_nombre", "")
        data = filtrar()
        if nombre.startswith("LED-"):
            codigo_lead = nombre
        else:
            found = await q_leads.buscar_lead_por_nombre(conn, nombre)
            if not found:
                return _fail(f"Lead '{nombre}' no encontrado")
            codigo_lead = found["codigo"]
        result = await q_leads.convertir_lead_a_cliente(conn, codigo_lead, data)
        if "error" in result:
            return _fail(result["error"])
        return _ok({
            "message": "Lead convertido a cliente y verificado",
            "lead_codigo": codigo_lead,
            "cliente_codigo": result["cliente"]["codigo"],
            "data": result["cliente"]
        })

    return _fail(f"Accion '{accion}' no reconocida. Usa: crear_cliente, actualizar_cliente, crear_lead, actualizar_lead, convertir_lead")


async def _gestionar_dev(conn, params) -> str:
    """CRUD de desarrolladores con verificacion."""
    accion = params["accion"]

    if accion == "crear_dev":
        data = {k: v for k, v in params.items() if k not in ("accion", "dar_acceso_bot") and v is not None}
        dev = await q_devs.crear_dev(conn, data)
        # ── VERIFICACION ──
        verificado = await q_devs.obtener_dev(conn, dev["codigo"])
        if not verificado:
            return _fail("Dev se intento crear pero NO se verifico en BD")
        return _ok({"message": "Dev creado y verificado", "codigo": verificado["codigo"], "data": verificado})

    elif accion == "actualizar_dev":
        nombre = params.get("codigo_o_nombre", "")
        data = {k: v for k, v in params.items() if k not in ("accion", "codigo_o_nombre", "dar_acceso_bot") and v is not None}
        if "jornada" in data:
            from app.db.queries.desarrolladores import JORNADA_HORAS
            data["horas_semana_base"] = JORNADA_HORAS.get(data.pop("jornada"), 40)
        if nombre.startswith("DEV-"):
            dev = await q_devs.actualizar_dev(conn, nombre, data)
        else:
            found = await q_devs.buscar_dev_por_nombre(conn, nombre)
            if not found:
                return _fail(f"Dev '{nombre}' no encontrado")
            dev = await q_devs.actualizar_dev(conn, found["codigo"], data)
        if not dev:
            return _fail("No se pudo actualizar el dev")
        return _ok({"message": "Dev actualizado y verificado", "data": dev})

    return _fail(f"Accion '{accion}' no implementada aun")


async def _adjuntar_imagen(conn, params, usuario) -> str:
    """Adjunta imagenes recientes a un item con verificacion."""
    codigo, err = await _resolver_codigo(conn, params.get("codigo_o_busqueda", ""))
    if err:
        return _fail(err)

    item = await q_backlog.obtener_item(conn, codigo)
    if not item:
        return _fail(f"Item {codigo} no encontrado")

    imagenes = await conn.fetch(
        """SELECT media_url FROM mensajes_conversacion
           WHERE usuario_id = $1 AND tipo_contenido = 'imagen'
           AND media_url IS NOT NULL
           AND created_at > NOW() - make_interval(mins => $2)
           ORDER BY created_at DESC LIMIT 5""",
        usuario["id"], settings.IMAGEN_RECIENTE_MINUTOS
    )

    if not imagenes:
        return _fail("No encontre imagenes recientes tuyas para adjuntar")

    adjuntos_actuales = item.get("adjuntos_urls") or []
    nuevos = 0
    for img in imagenes:
        if img["media_url"] not in adjuntos_actuales:
            adjuntos_actuales.append(img["media_url"])
            nuevos += 1

    if nuevos == 0:
        return _ok({"message": f"Las imagenes ya estan adjuntas en {codigo}"})

    await q_backlog.actualizar_item(conn, codigo, {"adjuntos_urls": adjuntos_actuales})

    # ── VERIFICACION ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado or len(verificado.get("adjuntos_urls") or []) < len(adjuntos_actuales):
        return _fail(f"Las imagenes no se adjuntaron correctamente a {codigo}")

    await _sync_item_airtable(conn, codigo)
    return _ok({"message": f"{nuevos} imagen(es) adjuntada(s) y verificada(s) en {codigo}", "codigo": codigo, "total_adjuntos": len(adjuntos_actuales)})


async def _actualizar_estado_dev(conn, params, usuario) -> str:
    """
    Tool exclusivo para devs: solo cambia estado y notas de SUS tareas.
    No puede cambiar cliente, urgencia, tipo, asignar a otros, etc.
    """
    codigo, err = await _resolver_codigo(conn, params["codigo_o_busqueda"])
    if err:
        return _fail(err)

    item = await q_backlog.obtener_item(conn, codigo)
    if not item:
        return _fail(f"Item {codigo} no encontrado")

    # Verificar que la tarea le pertenece al dev
    dev_id = usuario.get("desarrollador_id") or usuario.get("id")
    if str(item.get("dev_id")) != str(dev_id):
        return _fail(f"No puedes modificar {codigo} porque no esta asignado a ti")

    estado = params["estado"]

    data = {"estado": estado}
    if params.get("notas_dev"):
        data["notas_dev"] = params["notas_dev"]

    await q_backlog.actualizar_item(conn, codigo, data)

    # ── VERIFICACION READ-AFTER-WRITE ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado or verificado.get("estado") != estado:
        return _fail(f"El cambio de estado de {codigo} NO se verifico en la BD")

    await _sync_item_airtable(conn, codigo)
    return _ok({
        "message": f"{codigo} cambiado a '{estado}' y verificado en BD",
        "codigo": codigo,
        "estado": estado,
        "item": verificado
    })
