"""
Definiciones de los 14 Tools del bot.

Claude lee estas definiciones para decidir que tool usar.
Cada tool tiene: name, description, input_schema (JSON Schema).

Los tools se filtran por rol del usuario:
  PM: todos (14)
  CEO: consulta + asignar + derivar + gestionar_cliente (10)
  Dev: solo sus tareas (5)
  Autorizado: solo crear_item (1)
"""

# ── Tool definitions ──

TOOL_CONSULTAR_BACKLOG = {
    "name": "consultar_backlog",
    "description": "Busca y filtra items del backlog. Usa cuando preguntan por tareas, bugs, estado del backlog, pendientes, o items de un cliente/dev. Soporta filtros por cliente, tipo, estado, dev, urgencia.",
    "input_schema": {
        "type": "object",
        "properties": {
            "cliente": {"type": "string", "description": "Nombre del cliente (ej: MINSUR)"},
            "tipo": {"type": "string", "enum": ["Bug Critico","Bug Importante","Bug Menor","Solicitud Bloqueante","Solicitud Mejora","Deuda Tecnica Visible","Deuda Tecnica Interna","Requisito Lead","Roadmap"]},
            "estado": {"type": "string", "enum": ["Backlog","En Analisis","En Desarrollo","En QA","Desplegado"]},
            "dev_nombre": {"type": "string", "description": "Nombre del dev asignado"},
            "urgencia": {"type": "string", "enum": ["Critica","Alta","Media","Baja"]},
            "top_n": {"type": "integer", "description": "Cantidad de items (default 5)", "default": 5},
            "busqueda_texto": {"type": "string", "description": "Busqueda libre por titulo o descripcion"},
            "solo_con_deadline": {"type": "boolean", "description": "Solo items con deadline proximo"},
            "fecha_desde": {"type": "string", "description": "Fecha desde (YYYY-MM-DD o 'ayer')"},
        }
    }
}

TOOL_CONSULTAR_ITEM = {
    "name": "consultar_item",
    "description": "Obtiene el detalle completo de un item. Busca por codigo BK-XXXX, por texto, o por cliente+contexto.",
    "input_schema": {
        "type": "object",
        "properties": {
            "codigo": {"type": "string", "description": "Codigo del item (BK-XXXX)"},
            "busqueda_texto": {"type": "string", "description": "Busqueda por titulo/descripcion"},
            "cliente": {"type": "string", "description": "Filtrar por cliente"}
        }
    }
}

TOOL_CONSULTAR_EQUIPO = {
    "name": "consultar_equipo",
    "description": "Estado del equipo de desarrollo: disponibilidad, horas libres, tareas, Bug Guard. Usa cuando preguntan quien esta libre, capacidad, o el Bug Guard.",
    "input_schema": {
        "type": "object",
        "properties": {
            "solo_disponibles": {"type": "boolean", "default": True},
            "incluir_tareas": {"type": "boolean", "description": "Incluir lista de tareas activas", "default": False},
            "skill_requerido": {"type": "string", "description": "Filtrar por skill (Backend, Frontend, etc.)"}
        }
    }
}

TOOL_CONSULTAR_METRICAS = {
    "name": "consultar_metricas",
    "description": "Dashboard y metricas: SLA, lead time, bugs, rendimiento por dev. Usa cuando piden reporte, dashboard, o como van.",
    "input_schema": {
        "type": "object",
        "properties": {
            "periodo": {"type": "string", "description": "esta_semana, este_mes, ultimos_7_dias", "default": "esta_semana"},
            "tipo_metrica": {"type": "string", "enum": ["general", "por_dev", "por_cliente"], "default": "general"}
        }
    }
}

TOOL_CONSULTAR_CLIENTE = {
    "name": "consultar_cliente",
    "description": "Datos de un cliente o lead: MRR, SLA, tickets, riesgo churn, renovacion.",
    "input_schema": {
        "type": "object",
        "properties": {
            "nombre": {"type": "string"},
            "riesgo_churn": {"type": "string", "enum": ["ALTO", "MEDIO", "BAJO"]},
            "listar_todos": {"type": "boolean", "default": False}
        }
    }
}

TOOL_CREAR_ITEM = {
    "name": "crear_item",
    "description": "Crea un nuevo item en el backlog. SIEMPRE incluye el campo 'cliente' si el usuario menciona un nombre de clinica (MINSUR, KOMATSU, etc). SIEMPRE incluye 'esfuerzo_talla' si el usuario menciona talla (XS, S, M, L, XL). Si falta tipo o descripcion, pregunta.",
    "input_schema": {
        "type": "object",
        "properties": {
            "titulo": {"type": "string", "description": "Titulo corto (max 80 chars)"},
            "tipo": {"type": "string", "enum": ["Bug Critico","Bug Importante","Bug Menor","Solicitud Bloqueante","Solicitud Mejora","Deuda Tecnica Visible","Deuda Tecnica Interna","Requisito Lead","Roadmap"]},
            "descripcion": {"type": "string"},
            "cliente": {"type": "string", "description": "Nombre del cliente"},
            "urgencia": {"type": "string", "enum": ["Critica","Alta","Media","Baja"]},
            "skill_requerido": {"type": "string"},
            "esfuerzo_talla": {"type": "string", "enum": ["XS","S","M","L","XL"], "description": "XS=2h, S=4h, M=8h, L=16h, XL=32h"},
            "adjuntos_urls": {"type": "array", "items": {"type": "string"}},
            "auto_asignar": {"type": "boolean", "default": False}
        },
        "required": ["titulo", "tipo"]
    }
}

TOOL_ACTUALIZAR_ITEM = {
    "name": "actualizar_item",
    "description": "Actualiza CUALQUIER campo de un item: titulo, estado, cliente, tipo, urgencia, descripcion, talla, notas, skill. Tambien puede ELIMINAR (Cancelado/Archivado) y QUITAR IMAGENES adjuntas.",
    "input_schema": {
        "type": "object",
        "properties": {
            "codigo_o_busqueda": {"type": "string", "description": "Texto libre para buscar: titulo parcial, cliente, o codigo BK-XXXX del contexto. NUNCA pedir codigo al usuario."},
            "titulo": {"type": "string", "description": "Nuevo titulo del item"},
            "estado": {"type": "string", "enum": ["Backlog","En Analisis","En Desarrollo","En QA","Desplegado","Cancelado","Archivado"]},
            "tipo": {"type": "string", "enum": ["Bug Critico","Bug Importante","Bug Menor","Solicitud Bloqueante","Solicitud Mejora","Deuda Tecnica Visible","Deuda Tecnica Interna","Requisito Lead","Roadmap"]},
            "urgencia": {"type": "string", "enum": ["Critica","Alta","Media","Baja"]},
            "cliente": {"type": "string", "description": "Nombre del cliente o lead a asociar"},
            "descripcion": {"type": "string"},
            "esfuerzo_talla": {"type": "string", "enum": ["XS","S","M","L","XL"]},
            "skill_requerido": {"type": "string", "description": "Skill necesario: Backend, Frontend, etc."},
            "notas_dev": {"type": "string"},
            "notas_pm": {"type": "string"},
            "limpiar_adjuntos": {"type": "boolean", "description": "true para quitar TODAS las imagenes/adjuntos del item"},
            "quitar_cliente": {"type": "boolean", "description": "true para desasociar el cliente del item"}
        },
        "required": ["codigo_o_busqueda"]
    }
}

TOOL_ASIGNAR_TAREA = {
    "name": "asignar_tarea",
    "description": "Asigna, reasigna o DESASIGNA un item. Para desasignar usa desasignar=true (quita dev y vuelve a Backlog). NUNCA pidas el codigo al usuario.",
    "input_schema": {
        "type": "object",
        "properties": {
            "codigo_o_busqueda": {"type": "string", "description": "Texto libre para buscar el item: titulo parcial, nombre del cliente, o codigo BK-XXXX si ya lo sabes del contexto. NUNCA pidas el codigo al usuario."},
            "dev_nombre": {"type": "string", "description": "Nombre del dev (parcial OK: 'Carlos', 'David'). Si no se especifica, usa auto=true"},
            "auto": {"type": "boolean", "description": "Auto-seleccionar el mejor dev disponible por horas y skills", "default": False},
            "desasignar": {"type": "boolean", "description": "true para quitar la asignacion actual y devolver a Backlog"}
        },
        "required": ["codigo_o_busqueda"]
    }
}

TOOL_ESTABLECER_FECHAS = {
    "name": "establecer_fechas",
    "description": "Establece deadlines: interno (PM/CEO), QA estimada, y entrega al cliente.",
    "input_schema": {
        "type": "object",
        "properties": {
            "codigo_o_busqueda": {"type": "string", "description": "Texto libre para buscar: titulo parcial, cliente, o codigo BK-XXXX del contexto. NUNCA pedir codigo al usuario."},
            "deadline_interno": {"type": "string", "description": "YYYY-MM-DD"},
            "fecha_qa_estimada": {"type": "string", "description": "YYYY-MM-DD"},
            "deadline_cliente": {"type": "string", "description": "YYYY-MM-DD"}
        },
        "required": ["codigo_o_busqueda"]
    }
}

TOOL_REPORTAR_BLOQUEO = {
    "name": "reportar_bloqueo",
    "description": "Reporta un bloqueo en una tarea. Notifica automaticamente al PM.",
    "input_schema": {
        "type": "object",
        "properties": {
            "codigo_o_busqueda": {"type": "string", "description": "Texto libre para buscar: titulo parcial, cliente, o codigo BK-XXXX del contexto. NUNCA pedir codigo al usuario."},
            "descripcion_bloqueo": {"type": "string"}
        },
        "required": ["codigo_o_busqueda", "descripcion_bloqueo"]
    }
}

TOOL_DERIVAR_A_PERSONA = {
    "name": "derivar_a_persona",
    "description": "Deriva un item o tema a otra persona con contexto. Envia WhatsApp automatico.",
    "input_schema": {
        "type": "object",
        "properties": {
            "codigo_o_busqueda": {"type": "string", "description": "Texto libre para buscar: titulo parcial, cliente, o codigo BK-XXXX del contexto. NUNCA pedir codigo al usuario."},
            "persona_destino": {"type": "string"},
            "motivo": {"type": "string"},
            "requiere_analisis": {"type": "boolean", "default": False}
        },
        "required": ["persona_destino", "motivo"]
    }
}

TOOL_REASIGNAR_BUG_GUARD = {
    "name": "reasignar_bug_guard",
    "description": "Cambia el Bug Guard. Quita al anterior y asigna al nuevo. Muestra quien sigue en rotacion. Solo PM puede ejecutar.",
    "input_schema": {
        "type": "object",
        "properties": {
            "dev_nombre": {"type": "string", "description": "Nombre del nuevo Bug Guard (ej: 'David')"},
            "siguiente": {"type": "boolean", "description": "true para auto-seleccionar al siguiente en rotacion (el que tiene menos semanas)"}
        }
    }
}

TOOL_GESTIONAR_CLIENTE = {
    "name": "gestionar_cliente",
    "description": "Crea, actualiza o ELIMINA un cliente o lead. Para eliminar: usa eliminar_cliente o eliminar_lead (soft delete, cambia estado a Churned/Perdido).",
    "input_schema": {
        "type": "object",
        "properties": {
            "accion": {"type": "string", "enum": ["crear_cliente","actualizar_cliente","eliminar_cliente","crear_lead","actualizar_lead","eliminar_lead","convertir_lead"]},
            "codigo_o_nombre": {"type": "string"},
            "nombre_clinica": {"type": "string"},
            "mrr_mensual": {"type": "number"},
            "tamano": {"type": "string", "enum": ["Grande","Mediana","Pequena"]},
            "sla_dias": {"type": "integer"},
            "segmento": {"type": "string"},
            "estado_cliente": {"type": "string", "enum": ["Activo","En riesgo","Suspendido","Churned"]},
            "contacto_nombre": {"type": "string"},
            "contacto_cargo": {"type": "string"},
            "contacto_whatsapp": {"type": "string"},
            "contacto_email": {"type": "string"},
            "fecha_renovacion": {"type": "string", "description": "YYYY-MM-DD"},
            "renovacion_estado": {"type": "string", "enum": ["pendiente","contactado","renovado","perdido"], "description": "Estado del seguimiento de renovacion"},
            "renovacion_notas": {"type": "string", "description": "Notas sobre el seguimiento de renovacion"},
            "probabilidad_cierre": {"type": "number", "description": "0-100, solo para leads"},
            "notas": {"type": "string", "description": "Se guarda como notas_comerciales"}
        },
        "required": ["accion"]
    }
}

TOOL_GESTIONAR_DEV = {
    "name": "gestionar_dev",
    "description": "Crea, actualiza o DESACTIVA un desarrollador. Para desactivar: usa desactivar_dev (no lo elimina, solo marca como no disponible). Para crear necesitas: nombre_completo, nivel, jornada, skills, whatsapp.",
    "input_schema": {
        "type": "object",
        "properties": {
            "accion": {"type": "string", "enum": ["crear_dev","actualizar_dev","desactivar_dev"]},
            "codigo_o_nombre": {"type": "string", "description": "Para actualizar: nombre o codigo del dev"},
            "nombre_completo": {"type": "string"},
            "nivel": {"type": "string", "enum": ["Junior","Mid","Senior"]},
            "jornada": {"type": "string", "enum": ["full_time","medio_tiempo","part_time"], "description": "full_time=40h/sem, medio_tiempo=30h/sem, part_time=20h/sem"},
            "disponible": {"type": "boolean"},
            "fecha_regreso": {"type": "string", "description": "YYYY-MM-DD si no disponible"},
            "skills": {"type": "array", "items": {"type": "string"}},
            "whatsapp": {"type": "string"},
            "email": {"type": "string"},
            "notas": {"type": "string"}
        },
        "required": ["accion"]
    }
}


TOOL_ACTUALIZAR_ESTADO_DEV = {
    "name": "actualizar_estado_dev",
    "description": "Cambia el estado de UNA de TUS tareas asignadas. Solo puedes cambiar tareas que te pertenecen.",
    "input_schema": {
        "type": "object",
        "properties": {
            "codigo_o_busqueda": {"type": "string", "description": "Titulo parcial o codigo BK-XXXX de tu tarea."},
            "estado": {"type": "string", "enum": ["Backlog", "En Analisis", "En Desarrollo", "En QA", "Desplegado"], "description": "Nuevo estado"},
            "notas_dev": {"type": "string", "description": "Nota opcional sobre el avance"}
        },
        "required": ["codigo_o_busqueda", "estado"]
    }
}

TOOL_PREDECIR_ENTREGA = {
    "name": "predecir_entrega",
    "description": "Predice cuándo se completará una tarea o el sprint completo. Usa datos históricos reales del equipo (Monte Carlo). Usa para: '¿cuándo estará listo X?', '¿cuándo terminamos el sprint?', 'fecha estimada de entrega'.",
    "input_schema": {
        "type": "object",
        "properties": {
            "codigo_o_busqueda": {"type": "string", "description": "Titulo o codigo de una tarea específica. Si vacío, predice el sprint completo."},
            "modo": {"type": "string", "enum": ["item", "sprint"], "description": "item=una tarea, sprint=todas las tareas activas", "default": "item"}
        }
    }
}

TOOL_CAMBIAR_ROL = {
    "name": "cambiar_rol",
    "description": "Cambia el rol del usuario actual entre PM y CEO. Solo PM y CEO pueden cambiar entre si.",
    "input_schema": {
        "type": "object",
        "properties": {
            "nuevo_rol": {"type": "string", "enum": ["pm", "ceo"], "description": "Nuevo rol deseado"}
        },
        "required": ["nuevo_rol"]
    }
}

TOOL_ADJUNTAR_IMAGEN = {
    "name": "adjuntar_imagen",
    "description": "Adjunta las imagenes recientes del usuario a un item del backlog. Usa cuando el usuario envia una imagen y quiere asociarla a un bug o tarea. Busca automaticamente imagenes enviadas en los ultimos 10 minutos.",
    "input_schema": {
        "type": "object",
        "properties": {
            "codigo_o_busqueda": {"type": "string", "description": "Texto libre para buscar: titulo parcial, cliente, o codigo BK-XXXX del contexto. NUNCA pedir codigo al usuario."}
        },
        "required": ["codigo_o_busqueda"]
    }
}

# ── Todos los tools en una lista ──
ALL_TOOLS = [
    TOOL_CONSULTAR_BACKLOG,
    TOOL_CONSULTAR_ITEM,
    TOOL_CONSULTAR_EQUIPO,
    TOOL_CONSULTAR_METRICAS,
    TOOL_CONSULTAR_CLIENTE,
    TOOL_CREAR_ITEM,
    TOOL_ACTUALIZAR_ITEM,
    TOOL_ASIGNAR_TAREA,
    TOOL_ESTABLECER_FECHAS,
    TOOL_REPORTAR_BLOQUEO,
    TOOL_DERIVAR_A_PERSONA,
    TOOL_REASIGNAR_BUG_GUARD,
    TOOL_GESTIONAR_CLIENTE,
    TOOL_GESTIONAR_DEV,
    TOOL_ADJUNTAR_IMAGEN,
    TOOL_ACTUALIZAR_ESTADO_DEV,
    TOOL_CAMBIAR_ROL,
    TOOL_PREDECIR_ENTREGA,
]

# ── Tools filtrados por rol ──
# Cada rol solo ve los tools que puede usar

TOOLS_POR_ROL = {
    "pm": [t["name"] for t in ALL_TOOLS if t["name"] not in ("actualizar_estado_dev",)],  # Todos excepto tool de dev
    "ceo": [
        "consultar_backlog", "consultar_item", "consultar_equipo",
        "consultar_metricas", "consultar_cliente",
        "crear_item", "asignar_tarea", "derivar_a_persona",
        "gestionar_cliente", "adjuntar_imagen", "cambiar_rol", "predecir_entrega",
    ],
    "desarrollador": [
        "consultar_backlog", "consultar_item", "consultar_equipo",
        "actualizar_estado_dev",
    ],
    "autorizado": [
        "crear_item",
    ],
}


def get_tools_por_rol(rol: str) -> list[dict]:
    """
    Retorna las definiciones completas de tools permitidos para un rol.
    Claude solo vera estos tools — no puede usar los que no estan aqui.
    """
    nombres_permitidos = TOOLS_POR_ROL.get(rol, TOOLS_POR_ROL["autorizado"])
    return [t for t in ALL_TOOLS if t["name"] in nombres_permitidos]
