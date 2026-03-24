"""
Scoring WSJF v2 — Recalcula prioridades del backlog cada noche.

Formula:
  SCORE = (A × 0.40) + (B × 0.35) + (C × 0.25)

  A = Valor del Cliente (MRR, tamano, riesgo churn)
  B = Valor de la Tarea (tipo, impacto, bloqueos)
  C = Urgencia en el Tiempo (deadline, antiguedad)

  Si deadline <= 2 dias → score × 2 (multiplicador emergencia)

Corre: 23:00 diario (America/Lima)
"""

from datetime import datetime, date
import pytz

from app.config.database import get_pool
from app.config.settings import settings
from app.services.kapso import kapso_service

LIMA_TZ = pytz.timezone("America/Lima")


def _calcular_score(item: dict, cliente_data: dict | None = None) -> dict:
    """
    Calcula el score WSJF v2 de un item.

    Formula SAFe adaptada:
      WSJF = (Cost of Delay) / Job Size
      Cost of Delay = (A × 0.40) + (B × 0.35) + (C × 0.25)
      Job Size = horas_esfuerzo normalizadas (1-5)

      Si deadline <= 2 dias → score × 2 (multiplicador emergencia)

    Args:
        item: datos del backlog_item
        cliente_data: datos del cliente desde tabla clientes (MRR, churn, renovacion)
    """
    cli = cliente_data or {}

    # ══════════════════════════════════════════════════
    # BLOQUE A: Valor del Cliente (peso 40%)
    # ¿Cuanto perdemos si este cliente se va?
    # ══════════════════════════════════════════════════

    # A1: Score por MRR (ingreso mensual)
    mrr = float(cli.get("mrr_mensual") or item.get("cliente_mrr") or 0)
    a_mrr = 10 if mrr > 8000 else 7 if mrr > 4000 else 4 if mrr > 1500 else 1

    # A1b: Score por ARR (ingreso anual — pesa mas que MRR)
    arr = float(cli.get("arr_calculado") or mrr * 12)
    a_arr = 10 if arr > 100000 else 7 if arr > 50000 else 4 if arr > 20000 else 1

    # A1 compuesto: ARR pesa 60%, MRR pesa 40%
    a1 = (a_mrr * 0.40) + (a_arr * 0.60)

    # A2: Tamano de la clinica
    tamano = cli.get("tamano") or item.get("cliente_tamano") or ""
    a2 = 10 if tamano == "Grande" else 6 if tamano == "Mediana" else 3

    # A3: Riesgo de churn REAL (calculado desde la DB)
    # Cuantos dias sin resolver algo para este cliente
    dias_sin_atencion = 0
    fecha_ultimo = cli.get("fecha_ultimo_item_resuelto")
    if fecha_ultimo:
        if isinstance(fecha_ultimo, datetime):
            dias_sin_atencion = (datetime.now(LIMA_TZ) - fecha_ultimo.replace(tzinfo=LIMA_TZ)).days
        elif isinstance(fecha_ultimo, date):
            dias_sin_atencion = (date.today() - fecha_ultimo).days
    else:
        # Nunca se le resolvio nada — maximo riesgo
        dias_sin_atencion = 999

    a3 = 10 if dias_sin_atencion > 30 else 6 if dias_sin_atencion > 15 else 2

    # A4: Bonus por proximidad de renovacion
    # Si el contrato renueva pronto, priorizar para mantener al cliente contento
    a4_renovacion = 0
    fecha_renovacion = cli.get("fecha_renovacion")
    if fecha_renovacion:
        if isinstance(fecha_renovacion, date):
            dias_para_renovar = (fecha_renovacion - date.today()).days
        else:
            dias_para_renovar = 999
        if 0 < dias_para_renovar <= 30:
            a4_renovacion = 3  # Renueva en <1 mes — critico
        elif 0 < dias_para_renovar <= 60:
            a4_renovacion = 1  # Renueva en <2 meses

    # Penalizacion si es lead (no cliente activo)
    penalty_lead = 0
    if item.get("es_lead"):
        prob = float(item.get("_lead_prob_cierre") or 0)
        if prob >= 80:
            penalty_lead = -1   # Lead casi cerrado — poca penalizacion
        elif prob >= 40:
            penalty_lead = -2   # Lead en proceso
        else:
            penalty_lead = -3   # Lead frio

    score_a = max(0, min(10, ((a1 + a2 + a3) / 3) + a4_renovacion + penalty_lead))

    # ══════════════════════════════════════════════════
    # BLOQUE B: Valor de la Tarea (peso 35%)
    # ¿Que tan grave/impactante es?
    # ══════════════════════════════════════════════════

    tipo_scores = {
        "Bug Critico": 10, "Bug Importante": 8, "Bug Menor": 5,
        "Solicitud Bloqueante": 7, "Solicitud Mejora": 5,
        "Deuda Tecnica Visible": 4, "Deuda Tecnica Interna": 2,
        "Requisito Lead": 1, "Roadmap": 3,
    }
    b1 = tipo_scores.get(item.get("tipo", ""), 3)
    b2 = 3 if item.get("impacto_todos_usuarios") else 1
    b3 = 0  # bloquea_otras_tareas eliminado — se puede agregar en futuro si se necesita

    urgencia_scores = {"Critica": 2, "Alta": 1, "Media": 0, "Baja": -1}
    b4 = urgencia_scores.get(item.get("urgencia_declarada", ""), 0)

    score_b = min(10, ((b1 + b2 + b3 + b4) / 19) * 10)

    # ══════════════════════════════════════════════════
    # BLOQUE C: Urgencia en el Tiempo (peso 25%)
    # ¿Que pasa si esperamos?
    # ══════════════════════════════════════════════════

    deadline = item.get("deadline_interno") or item.get("deadline_cliente")
    if deadline:
        if isinstance(deadline, str):
            deadline = date.fromisoformat(deadline)
        dias_deadline = (deadline - date.today()).days
        # Deadline vencido → tratar como emergencia
        if dias_deadline < 0:
            dias_deadline = 0
    else:
        dias_deadline = 999

    c1 = 10 if dias_deadline <= 2 else 8 if dias_deadline <= 7 else 5 if dias_deadline <= 15 else 2 if dias_deadline <= 30 else 1

    created = item.get("created_at")
    if created and isinstance(created, datetime):
        try:
            dias_backlog = (datetime.now(LIMA_TZ) - created.replace(tzinfo=LIMA_TZ)).days
        except Exception:
            dias_backlog = 0
    else:
        dias_backlog = 0

    c2 = 4 if dias_backlog > 30 else 2 if dias_backlog > 15 else 0
    score_c = min(10, c1 + c2)

    # ══════════════════════════════════════════════════
    # SCORE FINAL: Cost of Delay / Job Size
    # ══════════════════════════════════════════════════

    cost_of_delay = (
        (score_a * settings.WSJF_PESO_CLIENTE) +
        (score_b * settings.WSJF_PESO_TAREA) +
        (score_c * settings.WSJF_PESO_URGENCIA)
    )

    # Job Size: normalizado de 1 (XS) a 5 (XL)
    # Dividir por job size hace que tareas pequenas de alto valor suban
    talla_map = {"XS": 1, "S": 2, "M": 3, "L": 4, "XL": 5}
    job_size = talla_map.get(item.get("esfuerzo_talla", ""), 3)  # Default M si no tiene

    score = cost_of_delay / (job_size * 0.5 + 0.5)  # Suavizado para no penalizar mucho tareas grandes
    # Rango sin emergencia: 0-20 aprox

    # Multiplicador emergencia: deadline vencido o <=2 dias
    if dias_deadline <= 2:
        score *= 2

    return {
        "score_wsjf": round(score, 2),
        "score_bloque_a": round(score_a, 2),
        "score_bloque_b": round(score_b, 2),
        "score_bloque_c": round(score_c, 2),
        "dias_en_backlog": dias_backlog,
        "dias_al_deadline": dias_deadline if dias_deadline != 999 else None,
    }


async def ejecutar_scoring():
    """
    Tarea programada: recalcula WSJF de todo el backlog.
    Corre cada noche a las 23:00.
    """
    print("📊 Iniciando scoring WSJF nocturno...")
    pool = get_pool()

    async with pool.acquire() as conn:
        # 1. Leer todos los items activos
        items = await conn.fetch(
            """SELECT * FROM backlog_items
               WHERE estado NOT IN ('Desplegado','Cancelado','Archivado')
               ORDER BY created_at"""
        )

        if not items:
            print("   No hay items activos para scoring")
            return

        # 2. Leer datos frescos de TODOS los clientes (para churn, ARR, renovacion)
        clientes_rows = await conn.fetch("SELECT * FROM clientes")
        clientes_map = {}
        for c in clientes_rows:
            clientes_map[c["id"]] = dict(c)

        # 3. Leer datos de leads (para probabilidad de cierre)
        leads_rows = await conn.fetch("SELECT * FROM leads")
        leads_map = {}
        for l in leads_rows:
            leads_map[l["id"]] = dict(l)

        print(f"   Calculando scores para {len(items)} items ({len(clientes_map)} clientes, {len(leads_map)} leads)...")

        # 4. Calcular score para cada item con datos frescos
        scored = []
        for item in items:
            item_dict = dict(item)

            # Enriquecer con datos del cliente real
            cliente_data = clientes_map.get(item["cliente_id"])

            # Si es lead, agregar probabilidad de cierre al item
            if item["es_lead"] and item["lead_id"]:
                lead_data = leads_map.get(item["lead_id"])
                if lead_data:
                    item_dict["_lead_prob_cierre"] = lead_data.get("probabilidad_cierre", 0)

            # Refrescar cache de datos del cliente en backlog_items
            if cliente_data:
                await conn.execute(
                    """UPDATE backlog_items SET
                        cliente_nombre = $1, cliente_mrr = $2,
                        cliente_tamano = $3, cliente_sla_dias = $4
                       WHERE id = $5""",
                    cliente_data["nombre_clinica"], cliente_data["mrr_mensual"],
                    cliente_data["tamano"], cliente_data["sla_dias"],
                    item["id"]
                )

            scores = _calcular_score(item_dict, cliente_data)
            scored.append({
                "id": item["id"],
                "codigo": item["codigo"],
                "titulo": item["titulo"],
                "tipo": item["tipo"],
                "cliente_nombre": item["cliente_nombre"],
                **scores,
            })

        # 3. Ordenar por score DESC y asignar posicion
        scored.sort(key=lambda x: x["score_wsjf"], reverse=True)
        for i, s in enumerate(scored):
            s["posicion_backlog"] = i + 1

        # 4. Actualizar cada item en la DB
        for s in scored:
            await conn.execute(
                """UPDATE backlog_items SET
                    score_wsjf = $1, posicion_backlog = $2,
                    score_bloque_a = $3, score_bloque_b = $4, score_bloque_c = $5
                   WHERE id = $6""",
                s["score_wsjf"], s["posicion_backlog"],
                s["score_bloque_a"], s["score_bloque_b"], s["score_bloque_c"],
                s["id"]
            )

            # 5. Guardar en historial
            await conn.execute(
                """INSERT INTO scoring_historial
                   (backlog_item_id, score_wsjf, posicion_backlog,
                    score_bloque_a, score_bloque_b, score_bloque_c,
                    dias_en_backlog, dias_al_deadline)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                s["id"], s["score_wsjf"], s["posicion_backlog"],
                s["score_bloque_a"], s["score_bloque_b"], s["score_bloque_c"],
                s["dias_en_backlog"], s["dias_al_deadline"]
            )

        # 6. Generar resumen para PM
        fecha = datetime.now(LIMA_TZ).strftime("%d/%m/%Y")
        top5 = scored[:5]
        iconos = {
            "Bug Critico": "🔴", "Bug Importante": "🟠", "Bug Menor": "🟡",
            "Solicitud Bloqueante": "🔴", "Solicitud Mejora": "🟡",
            "Deuda Tecnica Visible": "🔵", "Deuda Tecnica Interna": "⚪",
            "Requisito Lead": "🟢", "Roadmap": "🟣",
        }

        lineas = []
        for i, s in enumerate(top5):
            icono = iconos.get(s["tipo"], "⚫")
            cliente = s.get("cliente_nombre") or "Interno"
            lineas.append(
                f"{i+1}. {icono} [{s['codigo']}] {s['titulo']}\n"
                f"   Score: {s['score_wsjf']} | {s['tipo']} | {cliente}"
            )

        resumen = (
            f"📊 *Backlog priorizado — {fecha}*\n\n"
            + "\n\n".join(lineas)
            + f"\n\nTotal activos: {len(scored)}"
        )

        # 7. Enviar al PM y CEO
        if settings.WHATSAPP_PM:
            await kapso_service.enviar_texto_seguro(settings.WHATSAPP_PM, resumen)

        if settings.WHATSAPP_CEO:
            # Version ejecutiva mas corta para el CEO
            top1 = scored[0] if scored else None
            bugs_criticos = len([s for s in scored if s["tipo"] == "Bug Critico"])
            resumen_ceo = (
                f"📊 *Resumen ejecutivo — {fecha}*\n\n"
                f"• {len(scored)} items activos | 🔴 {bugs_criticos} bugs criticos\n"
                + (f"• Prioridad #1: [{top1['codigo']}] {top1['titulo']}\n" if top1 else "")
            )
            await kapso_service.enviar_texto_seguro(settings.WHATSAPP_CEO, resumen_ceo)

        # 8. Sync TODOS los items a Airtable (scores actualizados)
        synced = 0
        for s in scored:
            item_full = await conn.fetchrow("SELECT * FROM backlog_items WHERE id = $1", s["id"])
            if item_full and item_full["airtable_record_id"]:
                from app.services.airtable_sync import airtable_sync
                await airtable_sync.sync_backlog_item(dict(item_full))
                synced += 1

        # 9. Log
        await conn.execute(
            """INSERT INTO auditoria_log (origen, accion, detalle, resultado)
               VALUES ('scoring', 'score_calculado', $1, 'Exito')""",
            f"Scoring completado: {len(scored)} items, {synced} sincronizados a Airtable"
        )

        print(f"   ✅ Scoring completado: {len(scored)} items, {synced} synced a Airtable. Top: {scored[0]['codigo']} ({scored[0]['score_wsjf']})")
