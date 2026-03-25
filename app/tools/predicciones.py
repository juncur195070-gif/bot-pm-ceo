"""Predicciones tools — Delivery date prediction via Monte Carlo."""

from app.tools.registry import register
from app.tools.base import ok, fail, resolver_codigo
from app.db.queries import backlog as q_backlog
from app.db.queries import metricas as q_metricas


@register("predecir_entrega")
async def predecir_entrega(conn, params):
    """Predice fecha de entrega de un item o del sprint completo."""
    modo = params.get("modo", "item")

    if modo == "sprint":
        resultado = await q_metricas.predecir_sprint(conn)
        if "error" in resultado:
            return fail(resultado["error"])
        return ok({
            "message": f"Prediccion del sprint ({resultado['n_items']} items activos)",
            "probable": f"{resultado['p50_fecha']} ({resultado['p50_dias']} dias laborales)",
            "peor_caso": f"{resultado['p85_fecha']} ({resultado['p85_dias']} dias)",
            "extremo": f"{resultado['p95_fecha']} ({resultado['p95_dias']} dias)",
            "capacidad_equipo": f"{resultado['horas_equipo_dia']}h/dia",
            "metodo": resultado["basado_en"],
        })

    # Modo item: predecir una tarea específica
    busqueda = params.get("codigo_o_busqueda", "")
    if not busqueda:
        # Sin item específico → predecir sprint
        resultado = await q_metricas.predecir_sprint(conn)
        if "error" in resultado:
            return fail(resultado["error"])
        return ok({
            "message": f"Prediccion del sprint ({resultado['n_items']} items)",
            "probable": f"{resultado['p50_fecha']} ({resultado['p50_dias']} dias)",
            "peor_caso": f"{resultado['p85_fecha']} ({resultado['p85_dias']} dias)",
            "metodo": resultado["basado_en"],
        })

    # Buscar el item
    codigo, err = await resolver_codigo(conn, busqueda)
    if err:
        return fail(err)

    item = await q_backlog.obtener_item(conn, codigo)
    if not item:
        return fail(f"Item {codigo} no encontrado")

    if item.get("estado") in ("Desplegado", "Cancelado", "Archivado"):
        return ok({"message": f"{codigo} ya está en estado {item['estado']}", "fecha_desplegado": str(item.get("fecha_desplegado", ""))})

    talla = item.get("esfuerzo_talla") or "M"
    prediccion = await q_metricas.predecir_entrega(
        conn,
        esfuerzo_talla=talla,
        dev_id=item.get("dev_id"),
        tipo=item.get("tipo")
    )

    return ok({
        "message": f"Prediccion para {codigo} ({item['titulo'][:40]})",
        "talla": talla,
        "dev": item.get("dev_nombre") or "sin asignar",
        "probable": f"{prediccion['p50_fecha']} ({prediccion['p50_horas']}h)",
        "peor_caso": f"{prediccion['p85_fecha']} ({prediccion['p85_horas']}h)",
        "basado_en": prediccion["basado_en"],
        "n_historico": prediccion["n_historico"],
        "nota": prediccion.get("nota", ""),
    })
