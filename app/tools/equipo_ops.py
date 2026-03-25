"""Equipo tools — Bug Guard rotation, dev management."""

from datetime import date, datetime, timedelta

from app.tools.registry import register
from app.tools.base import ok, fail
from app.db.queries import desarrolladores as q_devs
from app.config.settings import settings


@register("reasignar_bug_guard")
async def reasignar_bug_guard(conn, params):
    """Cambia el Bug Guard: quita al anterior, asigna al nuevo, registra historial."""
    from datetime import date, datetime, timedelta
    import pytz
    LIMA_TZ = pytz.timezone("America/Lima")

    # Determinar nuevo Bug Guard
    if params.get("dev_nombre"):
        dev = await q_devs.buscar_dev_por_nombre(conn, params["dev_nombre"])
        if not dev:
            return fail(f"Dev '{params['dev_nombre']}' no encontrado")
        if not dev.get("disponible"):
            return fail(f"{dev['nombre_completo']} no esta disponible")
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
            return fail("No hay devs disponibles para Bug Guard")
    else:
        return fail("Indica dev_nombre o siguiente=true")

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
        return fail(f"Bug Guard no se cambio correctamente a {dev['nombre_completo']}")

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
    return ok({
        "message": f"Bug Guard cambiado de {anterior_nombre} a {dev['nombre_completo']}",
        "bug_guard_actual": dev["nombre_completo"],
        "bug_guard_anterior": anterior_nombre,
        "siguiente_en_rotacion": siguiente or ["No hay mas devs disponibles"],
    })


@register("gestionar_dev")
async def gestionar_dev(conn, params):
    """CRUD de desarrolladores con verificacion."""
    accion = params["accion"]

    if accion == "crear_dev":
        data = {k: v for k, v in params.items() if k not in ("accion", "dar_acceso_bot") and v is not None}
        dev = await q_devs.crear_dev(conn, data)
        # ── VERIFICACION ──
        verificado = await q_devs.obtener_dev(conn, dev["codigo"])
        if not verificado:
            return fail("Dev se intento crear pero NO se verifico en BD")
        return ok({"message": "Dev creado y verificado", "codigo": verificado["codigo"], "data": verificado})

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
                return fail(f"Dev '{nombre}' no encontrado")
            dev = await q_devs.actualizar_dev(conn, found["codigo"], data)
        if not dev:
            return fail("No se pudo actualizar el dev")
        return ok({"message": "Dev actualizado y verificado", "data": dev})

    elif accion == "desactivar_dev":
        nombre = params.get("codigo_o_nombre", "")
        if nombre.startswith("DEV-"):
            dev = await q_devs.actualizar_dev(conn, nombre, {"disponible": False})
        else:
            found = await q_devs.buscar_dev_por_nombre(conn, nombre)
            if not found:
                return fail(f"Dev '{nombre}' no encontrado")
            dev = await q_devs.actualizar_dev(conn, found["codigo"], {"disponible": False})
        if not dev:
            return fail("No se pudo desactivar el dev")
        return ok({"message": f"Dev {dev['nombre_completo']} desactivado (no disponible)", "data": dev})

    return fail(f"Accion '{accion}' no reconocida")
