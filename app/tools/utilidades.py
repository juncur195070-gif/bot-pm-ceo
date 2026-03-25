"""Utilidades tools — Role switching, reminders, history search, quick notes."""

from datetime import date, datetime, timedelta

from app.tools.registry import register
from app.tools.base import ok, fail
from app.db.queries import clientes as q_clientes
from app.db.queries import desarrolladores as q_devs
from app.db.queries import auditoria as q_audit


@register("cambiar_rol")
async def cambiar_rol(conn, params, usuario):
    """Cambia el rol del usuario entre PM y CEO."""
    nuevo_rol = params["nuevo_rol"]
    rol_actual = usuario.get("rol")

    if rol_actual not in ("pm", "ceo"):
        return fail("Solo PM y CEO pueden cambiar de rol")
    if nuevo_rol == rol_actual:
        return fail(f"Ya tienes el rol {rol_actual}")

    await conn.execute(
        "UPDATE usuarios_autorizados SET rol = $1 WHERE id = $2",
        nuevo_rol, usuario["id"]
    )

    verificado = await conn.fetchrow("SELECT rol FROM usuarios_autorizados WHERE id = $1", usuario["id"])
    if not verificado or verificado["rol"] != nuevo_rol:
        return fail("No se pudo cambiar el rol")

    return ok({"message": f"Rol cambiado de {rol_actual} a {nuevo_rol}", "rol_anterior": rol_actual, "rol_nuevo": nuevo_rol})


@register("recordatorio")
async def crear_recordatorio(conn, params, usuario):
    """Crea un recordatorio para fecha futura."""
    from datetime import datetime as dt, timedelta
    import pytz
    LIMA_TZ = pytz.timezone("America/Lima")

    texto = params["texto"]
    fecha_str = params["fecha"].lower().strip()

    hoy = date.today()
    dias_semana = {"lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6}

    import re
    if fecha_str in ("hoy", "ahora", "ya", "today"):
        fecha = hoy
    elif fecha_str in ("mañana", "manana", "tomorrow"):
        fecha = hoy + timedelta(days=1)
    elif fecha_str in ("pasado mañana", "pasado manana"):
        fecha = hoy + timedelta(days=2)
    elif fecha_str in dias_semana:
        target = dias_semana[fecha_str]
        diff = (target - hoy.weekday()) % 7
        if diff == 0:
            diff = 7
        fecha = hoy + timedelta(days=diff)
    elif re.match(r"en (\d+) dias?", fecha_str):
        dias = int(re.match(r"en (\d+) dias?", fecha_str).group(1))
        fecha = hoy + timedelta(days=dias)
    elif re.match(r"en (\d+) semanas?", fecha_str):
        semanas = int(re.match(r"en (\d+) semanas?", fecha_str).group(1))
        fecha = hoy + timedelta(weeks=semanas)
    elif re.match(r"\d+/\d+", fecha_str):
        # Formato dd/mm o dd/mm/yyyy
        partes = fecha_str.split("/")
        dia = int(partes[0])
        mes = int(partes[1])
        anio = int(partes[2]) if len(partes) > 2 else hoy.year
        fecha = date(anio, mes, dia)
    else:
        try:
            fecha = date.fromisoformat(fecha_str[:10])
        except Exception:
            return fail(f"No entendí la fecha '{fecha_str}'. Usa: hoy, mañana, lunes, en 3 dias, 25/03, o YYYY-MM-DD")

    if fecha < hoy:
        return fail(f"La fecha {fecha} ya pasó. Usa: hoy, mañana, lunes, en 3 dias, o YYYY-MM-DD")

    fecha_dt = dt.combine(fecha, dt.min.time().replace(hour=9)).replace(tzinfo=LIMA_TZ)

    item_id = None
    if params.get("codigo_item"):
        item = await conn.fetchrow("SELECT id FROM backlog_items WHERE codigo = $1", params["codigo_item"])
        if item:
            item_id = item["id"]

    await conn.execute(
        "INSERT INTO recordatorios (usuario_id, whatsapp, texto, fecha_recordar, backlog_item_id) VALUES ($1, $2, $3, $4, $5)",
        usuario.get("id"), usuario.get("whatsapp", ""), texto, fecha_dt, item_id
    )

    return ok({"message": f"Recordatorio creado para {fecha.strftime('%A %d/%m/%Y')}", "texto": texto, "fecha": fecha.isoformat()})


@register("buscar_historial")
async def buscar_historial(conn, params):
    """Busca en historial de conversaciones y auditoría."""
    busqueda = params["busqueda"]
    codigo = params.get("codigo_item")

    if codigo:
        logs = await conn.fetch(
            """SELECT accion, detalle, origen, created_at FROM auditoria_log
               WHERE backlog_item_id = (SELECT id FROM backlog_items WHERE codigo = $1)
               ORDER BY created_at DESC LIMIT 10""", codigo)
        if logs:
            return ok({"historial_item": codigo, "eventos": [
                {"accion": l["accion"], "detalle": l["detalle"], "origen": l["origen"], "fecha": str(l["created_at"])}
                for l in logs
            ]})
        return fail(f"Sin historial para {codigo}")

    # Buscar en conversaciones
    msgs = await conn.fetch(
        """SELECT direccion, contenido, created_at FROM mensajes_conversacion
           WHERE unaccent(LOWER(contenido)) LIKE unaccent(LOWER($1))
           ORDER BY created_at DESC LIMIT 10""", f"%{busqueda}%")

    if msgs:
        return ok({"resultados": [
            {"tipo": "conversacion", "direccion": m["direccion"], "contenido": m["contenido"][:200], "fecha": str(m["created_at"])}
            for m in msgs
        ]})

    # Buscar en auditoría
    logs = await conn.fetch(
        """SELECT accion, detalle, origen, created_at FROM auditoria_log
           WHERE unaccent(LOWER(detalle)) LIKE unaccent(LOWER($1))
           ORDER BY created_at DESC LIMIT 10""", f"%{busqueda}%")

    if logs:
        return ok({"resultados": [
            {"tipo": "auditoria", "accion": l["accion"], "detalle": l["detalle"], "fecha": str(l["created_at"])}
            for l in logs
        ]})

    return fail(f"No encontré nada sobre '{busqueda}' en el historial")


@register("nota_rapida")
async def nota_rapida(conn, params):
    """Guarda nota rápida asociada a cliente, item o dev."""
    import pytz
    LIMA_TZ = pytz.timezone("America/Lima")
    nota = params["nota"]
    fecha = datetime.now(LIMA_TZ).strftime("%d/%m %H:%M")
    destino = ""

    if params.get("codigo_item"):
        item = await conn.fetchrow("SELECT codigo, notas_pm FROM backlog_items WHERE codigo = $1", params["codigo_item"])
        if item:
            nueva = f"{item['notas_pm'] or ''}\n[{fecha}] {nota}".strip()
            await conn.execute("UPDATE backlog_items SET notas_pm = $1 WHERE codigo = $2", nueva, item["codigo"])
            destino = f"item {item['codigo']}"

    elif params.get("cliente"):
        cli = await q_clientes.buscar_cliente_por_nombre(conn, params["cliente"])
        if cli:
            nueva = f"{cli.get('notas_comerciales') or ''}\n[{fecha}] {nota}".strip()
            await q_clientes.actualizar_cliente(conn, cli["codigo"], {"notas_comerciales": nueva})
            destino = f"cliente {cli['nombre_clinica']}"

    elif params.get("dev"):
        dev = await q_devs.buscar_dev_por_nombre(conn, params["dev"])
        if dev:
            nueva = f"{dev.get('notas') or ''}\n[{fecha}] {nota}".strip()
            await q_devs.actualizar_dev(conn, dev["codigo"], {"notas": nueva})
            destino = f"dev {dev['nombre_completo']}"

    if not destino:
        await q_audit.registrar_accion(conn, origen="nota_rapida", accion="nota_creada", detalle=nota)
        destino = "registro general"

    return ok({"message": f"Nota guardada en {destino}", "nota": nota})
