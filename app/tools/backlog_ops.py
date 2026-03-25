"""Backlog operation tools — write operations on backlog items."""

from datetime import date, datetime, timedelta

from app.tools.registry import register
from app.tools.base import ok, fail, resolver_codigo, sync_item_airtable, _a_json
from app.config.settings import settings
from app.db.queries import backlog as q_backlog
from app.db.queries import clientes as q_clientes
from app.db.queries import desarrolladores as q_devs
from app.db.queries import leads as q_leads
from app.services.airtable_sync import airtable_sync


@register("crear_item")
async def crear_item(conn, params, usuario):
    """Crea un item en el backlog con verificacion."""
    # Buscar cliente o lead si se menciona
    cliente_data = {}
    if params.get("cliente"):
        cliente = await q_clientes.buscar_cliente_por_nombre(conn, params["cliente"])
        if cliente:
            cliente_data = {
                "cliente_id": cliente["id"],
            }
        else:
            lead = await q_leads.buscar_lead_por_nombre(conn, params["cliente"])
            if lead:
                cliente_data = {
                    "es_lead": True,
                    "lead_id": lead["id"],
                }
            else:
                # Cliente no existe — listar existentes para ayudar
                clientes_existentes, _ = await q_clientes.listar_clientes(conn, per_page=50)
                nombres = [c["nombre_clinica"] for c in clientes_existentes]
                return fail(
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

    # Proteccion anti-duplicados (3 niveles):
    # 1. Titulo similar en ultimos 30 min
    # 2. Mismo cliente + mismo tipo en ultimos 30 min
    # 3. Palabras clave del titulo en ultimos 15 min
    titulo_nuevo = params["titulo"]

    # Nivel 1: titulo similar
    duplicado = await conn.fetchrow(
        """SELECT bi.codigo, bi.titulo, d.nombre_completo as dev_nombre
           FROM backlog_items bi
           LEFT JOIN desarrolladores d ON bi.dev_id = d.id
           WHERE unaccent(LOWER(bi.titulo)) LIKE unaccent(LOWER($1))
           AND bi.created_at > NOW() - INTERVAL '30 minutes'
           AND bi.estado NOT IN ('Cancelado','Archivado')
           LIMIT 1""",
        f"%{titulo_nuevo[:20]}%"
    )

    # Nivel 2: mismo cliente + mismo tipo reciente
    if not duplicado and cliente_data.get("cliente_id"):
        duplicado = await conn.fetchrow(
            """SELECT bi.codigo, bi.titulo, d.nombre_completo as dev_nombre
               FROM backlog_items bi
               LEFT JOIN desarrolladores d ON bi.dev_id = d.id
               WHERE bi.cliente_id = $1 AND bi.tipo = $2
               AND bi.created_at > NOW() - INTERVAL '30 minutes'
               AND bi.estado NOT IN ('Cancelado','Archivado')
               LIMIT 1""",
            cliente_data["cliente_id"], params["tipo"]
        )

    if duplicado:
        return fail(
            f"Ya existe un item similar reciente: {duplicado['codigo']} '{duplicado['titulo']}' (dev: {duplicado['dev_nombre'] or 'sin asignar'}). "
            f"Si quieres modificar ese item, usa actualizar_item. Si quieres asignarlo, usa asignar_tarea. No crees duplicados."
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
        return fail(f"El item se intento crear pero NO se verifico en la BD. Codigo esperado: {item.get('codigo')}")

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
                verificado.get("cliente_nombre")  # From JOIN alias
            )
            # Releer item para ver si se asigno
            verificado = await q_backlog.obtener_item(conn, verificado["codigo"])
            if verificado and verificado.get("dev_nombre"):
                emergencia_asignada = True
        except Exception as e:
            print(f"  ⚠ Emergencia fallo: {e}")

    # Sync a Airtable (background — no bloquea respuesta al usuario)
    await sync_item_airtable(conn, verificado["codigo"])

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
    return ok(result)


@register("actualizar_item")
async def actualizar_item(conn, params, usuario):
    """Actualiza cualquier campo de un item con verificacion."""
    # actualizar_item siempre busca incluyendo cancelados/archivados (permite reactivar)
    codigo, err = await resolver_codigo(conn, params["codigo_o_busqueda"], incluir_cancelados=True)
    if err:
        return fail(err)

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

    # Limpiar adjuntos (quitar todas las imagenes)
    if params.get("limpiar_adjuntos"):
        data["adjuntos_urls"] = []

    # Quitar cliente del item
    if params.get("quitar_cliente"):
        data["cliente_id"] = None
        data["es_lead"] = False
        data["lead_id"] = None

    # Si cambia el cliente, buscar en clientes y leads
    if params.get("cliente"):
        cliente = await q_clientes.buscar_cliente_por_nombre(conn, params["cliente"])
        if cliente:
            data["cliente_id"] = cliente["id"]
            data["es_lead"] = False
            data["lead_id"] = None
        else:
            lead = await q_leads.buscar_lead_por_nombre(conn, params["cliente"])
            if lead:
                data["es_lead"] = True
                data["lead_id"] = lead["id"]
                data["cliente_id"] = None
            else:
                return fail(f"No encontre cliente ni lead con nombre '{params['cliente']}'")

    if not data:
        return fail("No se especifico ningun campo para actualizar")

    item = await q_backlog.actualizar_item(conn, codigo, data)
    if not item:
        return fail(f"Item {codigo} no encontrado o no se pudo actualizar")

    # ── VERIFICACION READ-AFTER-WRITE ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado:
        return fail(f"La actualizacion de {codigo} no se verifico en la BD")

    # Verificar que los campos se aplicaron
    campos_no_aplicados = []
    for key, value in data.items():
        if key in ("cliente_id", "lead_id"):
            continue
        db_val = verificado.get(key)
        if db_val is not None and value is not None and str(db_val) != str(value):
            campos_no_aplicados.append(key)

    if campos_no_aplicados:
        return fail(f"Campos no se aplicaron correctamente: {campos_no_aplicados}", item=verificado)

    # Si se marco como Desplegado, actualizar fecha_ultimo_item_resuelto del cliente
    if params.get("estado") == "Desplegado" and verificado.get("cliente_id"):
        await conn.execute(
            "UPDATE clientes SET fecha_ultimo_item_resuelto = NOW() WHERE id = $1",
            verificado["cliente_id"]
        )

    # Actualizar metricas Bug Guard si es bug resuelto
    if params.get("estado") in ("Desplegado", "En QA") and verificado.get("tipo") in ("Bug Critico", "Bug Importante"):
        try:
            from datetime import datetime as dt
            import pytz
            LIMA_TZ = pytz.timezone("America/Lima")
            semana = f"S{dt.now(LIMA_TZ).isocalendar()[1]}-{dt.now(LIMA_TZ).year}"

            # Calcular tiempo de respuesta en minutos
            if verificado.get("fecha_asignacion"):
                resp_min = (dt.now(LIMA_TZ) - verificado["fecha_asignacion"].replace(tzinfo=LIMA_TZ)).total_seconds() / 60
            else:
                resp_min = None

            await conn.execute("""
                UPDATE bug_guard_historial SET
                    bugs_atendidos_total = bugs_atendidos_total + 1,
                    bugs_criticos = bugs_criticos + CASE WHEN $1 = 'Bug Critico' THEN 1 ELSE 0 END,
                    tiempo_promedio_respuesta_min = CASE
                        WHEN tiempo_promedio_respuesta_min IS NULL OR tiempo_promedio_respuesta_min = 0 THEN $2
                        ELSE (tiempo_promedio_respuesta_min + $2) / 2
                    END
                WHERE semana_codigo = $3""",
                verificado["tipo"], resp_min, semana
            )
        except Exception as e:
            print(f"  ⚠ Bug Guard metrics update failed: {e}")

    # Incrementar tareas completadas del dev si se marco como Desplegado
    if params.get("estado") == "Desplegado" and verificado.get("dev_id"):
        await conn.execute("UPDATE desarrolladores SET tareas_completadas_total = tareas_completadas_total + 1 WHERE id = $1", verificado["dev_id"])

    # Si se canceló/archivó → eliminar de Airtable
    if params.get("estado") in ("Cancelado", "Archivado") and verificado.get("airtable_record_id"):
        try:
            await airtable_sync.delete_record(verificado["airtable_record_id"])
            await conn.execute("UPDATE backlog_items SET airtable_record_id = NULL WHERE codigo = $1", codigo)
        except Exception as e:
            print(f"  ⚠ Airtable delete fallo: {e}")
    else:
        await sync_item_airtable(conn, codigo)

    cambios = [k for k in data.keys() if k not in ("cliente_id", "lead_id")]
    return ok({"message": f"Item {codigo} actualizado y verificado: {', '.join(cambios)}", "item": verificado})


@register("asignar_tarea")
async def asignar_tarea(conn, params, usuario):
    """Asigna o desasigna un item a un dev con verificacion."""
    codigo, err = await resolver_codigo(conn, params["codigo_o_busqueda"])
    if err:
        return fail(err)

    item = await q_backlog.obtener_item(conn, codigo)
    if not item:
        return fail(f"Item {codigo} no encontrado")

    # Desasignar: quitar dev y volver a Backlog
    if params.get("desasignar"):
        dev_anterior = item.get("dev_nombre") or "nadie"
        await conn.execute(
            """UPDATE backlog_items SET
                dev_id = NULL,
                estado = 'Backlog', fecha_asignacion = NULL
               WHERE codigo = $1""",
            codigo
        )
        verificado = await q_backlog.obtener_item(conn, codigo)
        if verificado and verificado.get("dev_id") is None:
            await sync_item_airtable(conn, codigo)
            return ok({"message": f"{codigo} desasignado de {dev_anterior} y devuelto a Backlog", "codigo": codigo, "item": verificado})
        return fail(f"No se pudo desasignar {codigo}")

    # Si auto=true y ya tiene dev asignado, no reasignar (proteger asignaciones existentes)
    if params.get("auto") and item.get("dev_id"):
        return ok({
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
            return fail(f"Dev '{params['dev_nombre']}' no encontrado")
        dev_cap = next((d for d in capacidad if d["codigo"] == dev["codigo"]), None)
        if dev_cap and dev_cap["horas_libres"] < horas_item:
            return fail(
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
            return fail(
                "No hay devs con capacidad para esta tarea",
                horas_requeridas=horas_item,
                skills_requeridos=skills_req,
                razones=razon,
                sugerencia="Queda en Backlog hasta que un dev se libere"
            )

        candidatos.sort(key=lambda d: d["horas_libres"], reverse=True)
        dev = await q_devs.obtener_dev(conn, candidatos[0]["codigo"])
    else:
        return fail("Indica dev_nombre o auto=true")

    data = {
        "dev_id": dev["id"],
        "fecha_asignacion": datetime.now(),
    }
    await q_backlog.actualizar_item(conn, codigo, data)

    # ── VERIFICACION READ-AFTER-WRITE ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado or str(verificado.get("dev_id")) != str(dev["id"]):
        return fail(f"La asignacion de {codigo} a {dev['nombre_completo']} NO se verifico en la BD")

    await sync_item_airtable(conn, codigo)
    return ok({
        "message": f"{codigo} asignado a {dev['nombre_completo']} y verificado en BD",
        "dev": dev["nombre_completo"],
        "item": verificado
    })


@register("establecer_fechas")
async def establecer_fechas(conn, params, usuario):
    """Establece deadlines con verificacion."""
    codigo, err = await resolver_codigo(conn, params["codigo_o_busqueda"])
    if err:
        return fail(err)

    data = {}
    if params.get("deadline_interno"):
        data["deadline_interno"] = params["deadline_interno"]
    if params.get("fecha_qa_estimada"):
        data["fecha_qa_estimada"] = params["fecha_qa_estimada"]
    if params.get("deadline_cliente"):
        data["deadline_cliente"] = params["deadline_cliente"]

    item = await q_backlog.actualizar_item(conn, codigo, data)
    if not item:
        return fail(f"Item {codigo} no encontrado")

    # ── VERIFICACION ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado:
        return fail(f"Fechas de {codigo} no se verificaron en la BD")

    await sync_item_airtable(conn, codigo)
    return ok({"message": "Fechas establecidas y verificadas", "item": verificado})


@register("reportar_bloqueo")
async def reportar_bloqueo(conn, params, usuario):
    """Reporta bloqueo en una tarea con verificacion."""
    codigo, err = await resolver_codigo(conn, params["codigo_o_busqueda"])
    if err:
        return fail(err)

    item = await q_backlog.obtener_item(conn, codigo)
    if not item:
        return fail(f"Item {codigo} no encontrado")

    nota_actual = item.get("notas_dev") or ""
    nueva_nota = f"{nota_actual}\n[BLOQUEO {datetime.now().strftime('%d/%m %H:%M')}]: {params['descripcion_bloqueo']}"

    await q_backlog.actualizar_item(conn, codigo, {"notas_dev": nueva_nota.strip()})

    # ── VERIFICACION ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado or params["descripcion_bloqueo"] not in (verificado.get("notas_dev") or ""):
        return fail(f"El bloqueo de {codigo} no se guardo correctamente en la BD")

    await sync_item_airtable(conn, codigo)
    return ok({"message": f"Bloqueo registrado y verificado en {codigo}", "bloqueo": params["descripcion_bloqueo"]})


@register("derivar_a_persona")
async def derivar_a_persona(conn, params, usuario):
    """Deriva un item a otra persona con verificacion."""
    if params.get("codigo_o_busqueda"):
        codigo, err = await resolver_codigo(conn, params["codigo_o_busqueda"])
        if err:
            return fail(err)

        data = {"derivado_a": params["persona_destino"], "derivado_motivo": params["motivo"]}
        await q_backlog.actualizar_item(conn, codigo, data)

        # ── VERIFICACION ──
        verificado = await q_backlog.obtener_item(conn, codigo)
        if not verificado or verificado.get("derivado_a") != params["persona_destino"]:
            return fail(f"La derivacion de {codigo} no se verifico en la BD")

        await sync_item_airtable(conn, codigo)

    return ok({
        "message": f"Derivado a {params['persona_destino']} y verificado",
        "motivo": params["motivo"],
        "requiere_analisis": params.get("requiere_analisis", False)
    })


@register("adjuntar_imagen")
async def adjuntar_imagen(conn, params, usuario):
    """Adjunta imagenes recientes a un item con verificacion."""
    codigo, err = await resolver_codigo(conn, params.get("codigo_o_busqueda", ""))
    if err:
        return fail(err)

    item = await q_backlog.obtener_item(conn, codigo)
    if not item:
        return fail(f"Item {codigo} no encontrado")

    imagenes = await conn.fetch(
        """SELECT media_url FROM mensajes_conversacion
           WHERE usuario_id = $1 AND tipo_contenido = 'imagen'
           AND media_url IS NOT NULL
           AND created_at > NOW() - make_interval(mins => $2)
           ORDER BY created_at DESC LIMIT 5""",
        usuario["id"], settings.IMAGEN_RECIENTE_MINUTOS
    )

    if not imagenes:
        return fail("No encontre imagenes recientes tuyas para adjuntar")

    adjuntos_actuales = item.get("adjuntos_urls") or []
    nuevos = 0
    for img in imagenes:
        if img["media_url"] not in adjuntos_actuales:
            adjuntos_actuales.append(img["media_url"])
            nuevos += 1

    if nuevos == 0:
        return ok({"message": f"Las imagenes ya estan adjuntas en {codigo}"})

    await q_backlog.actualizar_item(conn, codigo, {"adjuntos_urls": adjuntos_actuales})

    # ── VERIFICACION ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado or len(verificado.get("adjuntos_urls") or []) < len(adjuntos_actuales):
        return fail(f"Las imagenes no se adjuntaron correctamente a {codigo}")

    await sync_item_airtable(conn, codigo)
    return ok({"message": f"{nuevos} imagen(es) adjuntada(s) y verificada(s) en {codigo}", "codigo": codigo, "total_adjuntos": len(adjuntos_actuales)})


@register("actualizar_estado_dev")
async def actualizar_estado_dev(conn, params, usuario):
    """
    Tool exclusivo para devs: solo cambia estado y notas de SUS tareas.
    No puede cambiar cliente, urgencia, tipo, asignar a otros, etc.
    """
    codigo, err = await resolver_codigo(conn, params["codigo_o_busqueda"])
    if err:
        return fail(err)

    item = await q_backlog.obtener_item(conn, codigo)
    if not item:
        return fail(f"Item {codigo} no encontrado")

    # Verificar que la tarea le pertenece al dev
    dev_id = usuario.get("desarrollador_id") or usuario.get("id")
    if str(item.get("dev_id")) != str(dev_id):
        return fail(f"No puedes modificar {codigo} porque no esta asignado a ti")

    estado = params["estado"]

    data = {"estado": estado}
    if params.get("notas_dev"):
        data["notas_dev"] = params["notas_dev"]

    await q_backlog.actualizar_item(conn, codigo, data)

    # ── VERIFICACION READ-AFTER-WRITE ──
    verificado = await q_backlog.obtener_item(conn, codigo)
    if not verificado or verificado.get("estado") != estado:
        return fail(f"El cambio de estado de {codigo} NO se verifico en la BD")

    # Actualizar metricas Bug Guard si es bug resuelto
    if params.get("estado") in ("Desplegado", "En QA") and verificado.get("tipo") in ("Bug Critico", "Bug Importante"):
        try:
            from datetime import datetime as dt
            import pytz
            LIMA_TZ = pytz.timezone("America/Lima")
            semana = f"S{dt.now(LIMA_TZ).isocalendar()[1]}-{dt.now(LIMA_TZ).year}"

            # Calcular tiempo de respuesta en minutos
            if verificado.get("fecha_asignacion"):
                resp_min = (dt.now(LIMA_TZ) - verificado["fecha_asignacion"].replace(tzinfo=LIMA_TZ)).total_seconds() / 60
            else:
                resp_min = None

            await conn.execute("""
                UPDATE bug_guard_historial SET
                    bugs_atendidos_total = bugs_atendidos_total + 1,
                    bugs_criticos = bugs_criticos + CASE WHEN $1 = 'Bug Critico' THEN 1 ELSE 0 END,
                    tiempo_promedio_respuesta_min = CASE
                        WHEN tiempo_promedio_respuesta_min IS NULL OR tiempo_promedio_respuesta_min = 0 THEN $2
                        ELSE (tiempo_promedio_respuesta_min + $2) / 2
                    END
                WHERE semana_codigo = $3""",
                verificado["tipo"], resp_min, semana
            )
        except Exception as e:
            print(f"  ⚠ Bug Guard metrics update failed: {e}")

    # Incrementar tareas completadas del dev si se marco como Desplegado
    if params.get("estado") == "Desplegado" and verificado.get("dev_id"):
        await conn.execute("UPDATE desarrolladores SET tareas_completadas_total = tareas_completadas_total + 1 WHERE id = $1", verificado["dev_id"])

    await sync_item_airtable(conn, codigo)
    return ok({
        "message": f"{codigo} cambiado a '{estado}' y verificado en BD",
        "codigo": codigo,
        "estado": estado,
        "item": verificado
    })
