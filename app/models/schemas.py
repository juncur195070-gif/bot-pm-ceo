"""
Pydantic Schemas — Definen la forma de los datos en la API REST.

Cada clase es un "contrato" de datos:
  - *Create = datos para CREAR un registro (POST)
  - *Update = datos para ACTUALIZAR un registro (PATCH) — todos opcionales
  - *Response = datos que RETORNA la API (GET)

FastAPI usa estos schemas para:
  1. Validar automaticamente los datos de entrada
  2. Generar documentacion Swagger
  3. Serializar la respuesta a JSON
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime
from uuid import UUID


# ============================================================
# CLIENTES
# ============================================================

class ClienteCreate(BaseModel):
    """Datos para crear un cliente nuevo."""
    nombre_clinica: str = Field(..., max_length=200, description="Nombre de la clinica")
    mrr_mensual: float = Field(0, ge=0, description="MRR mensual en soles")
    tamano: str = Field(..., description="Grande, Mediana o Pequena")
    sla_dias: int = Field(..., description="Dias maximo para atender solicitudes")
    segmento: Optional[str] = None
    contacto_nombre: Optional[str] = None
    contacto_cargo: Optional[str] = None
    contacto_whatsapp: Optional[str] = None
    contacto_email: Optional[str] = None
    fecha_inicio_contrato: Optional[date] = None
    fecha_renovacion: Optional[date] = None
    notas_comerciales: Optional[str] = None


class ClienteUpdate(BaseModel):
    """Datos para actualizar un cliente. Todos opcionales."""
    nombre_clinica: Optional[str] = None
    mrr_mensual: Optional[float] = Field(None, ge=0)
    tamano: Optional[str] = None
    sla_dias: Optional[int] = None
    segmento: Optional[str] = None
    estado_cliente: Optional[str] = None
    contacto_nombre: Optional[str] = None
    contacto_whatsapp: Optional[str] = None
    contacto_email: Optional[str] = None
    fecha_renovacion: Optional[date] = None
    notas_comerciales: Optional[str] = None


class ClienteResponse(BaseModel):
    """Datos que retorna la API al consultar un cliente."""
    id: UUID
    codigo: str
    nombre_clinica: str
    mrr_mensual: float
    arr_calculado: Optional[float] = None
    tamano: str
    sla_dias: int
    segmento: Optional[str] = None
    estado_cliente: str
    contacto_nombre: Optional[str] = None
    contacto_whatsapp: Optional[str] = None
    fecha_renovacion: Optional[date] = None
    fecha_ultimo_item_resuelto: Optional[datetime] = None
    created_at: datetime


# ============================================================
# LEADS
# ============================================================

class LeadCreate(BaseModel):
    nombre_clinica: str = Field(..., max_length=200)
    contacto_nombre: Optional[str] = None
    contacto_whatsapp: Optional[str] = None
    mrr_estimado: float = 0
    tamano_estimado: Optional[str] = None
    probabilidad_cierre: Optional[float] = Field(None, ge=0, le=100)
    requisitos_solicitados: Optional[str] = None
    notas: Optional[str] = None


class LeadUpdate(BaseModel):
    estado_lead: Optional[str] = None
    mrr_estimado: Optional[float] = None
    probabilidad_cierre: Optional[float] = Field(None, ge=0, le=100)
    requisitos_solicitados: Optional[str] = None
    notas: Optional[str] = None


class LeadResponse(BaseModel):
    id: UUID
    codigo: str
    nombre_clinica: str
    estado_lead: str
    mrr_estimado: float
    probabilidad_cierre: Optional[float] = None
    created_at: datetime


# ============================================================
# DESARROLLADORES
# ============================================================

class DevCreate(BaseModel):
    nombre_completo: str = Field(..., max_length=200)
    nivel: str = Field(..., description="Junior, Mid o Senior")
    horas_semana_base: int = Field(..., ge=1, le=50)
    skills: list[str] = Field(default_factory=list)
    whatsapp: str
    email: Optional[str] = None
    notas: Optional[str] = None


class DevUpdate(BaseModel):
    nivel: Optional[str] = None
    horas_semana_base: Optional[int] = Field(None, ge=1, le=50)
    disponible: Optional[bool] = None
    fecha_regreso: Optional[date] = None
    skills: Optional[list[str]] = None
    notas: Optional[str] = None


class DevResponse(BaseModel):
    id: UUID
    codigo: str
    nombre_completo: str
    nivel: str
    horas_semana_base: int
    disponible: bool
    skills: list[str]
    whatsapp: str
    wip_limit: int
    horas_sprint_semana: int
    bug_guard_semana_actual: bool
    tareas_completadas_total: int
    created_at: datetime


class DevCapacidadResponse(BaseModel):
    """Capacidad actual de un dev — incluye tareas activas."""
    codigo: str
    nivel: str
    disponible: bool
    horas_semana_base: int
    horas_sprint_semana: int
    horas_asignadas: float = 0
    horas_libres: float = 0
    wip_usado: int = 0
    wip_limit: int
    bug_guard: bool = False
    tareas_activas: list[dict] = Field(default_factory=list)


# ============================================================
# BACKLOG
# ============================================================

class BacklogCreate(BaseModel):
    titulo: str = Field(..., max_length=200)
    tipo: str
    descripcion: Optional[str] = None
    cliente_id: Optional[UUID] = None
    lead_id: Optional[UUID] = None
    urgencia_declarada: Optional[str] = None
    deadline_interno: Optional[date] = None
    fecha_qa_estimada: Optional[date] = None
    deadline_cliente: Optional[date] = None
    impacto_todos_usuarios: bool = False
    skill_requerido: list[str] = Field(default_factory=list)
    esfuerzo_talla: Optional[str] = None
    adjuntos_urls: list[str] = Field(default_factory=list)
    notas_pm: Optional[str] = None


class BacklogUpdate(BaseModel):
    titulo: Optional[str] = None
    tipo: Optional[str] = None
    descripcion: Optional[str] = None
    estado: Optional[str] = None
    urgencia_declarada: Optional[str] = None
    deadline_interno: Optional[date] = None
    fecha_qa_estimada: Optional[date] = None
    deadline_cliente: Optional[date] = None
    esfuerzo_talla: Optional[str] = None
    dev_id: Optional[UUID] = None
    notas_dev: Optional[str] = None
    notas_pm: Optional[str] = None
    adjuntos_urls: Optional[list[str]] = None


class BacklogResponse(BaseModel):
    id: UUID
    codigo: str
    titulo: str
    tipo: str
    descripcion: Optional[str] = None
    estado: str
    urgencia_declarada: Optional[str] = None
    cliente_nombre: Optional[str] = None
    dev_nombre: Optional[str] = None
    score_wsjf: float = 0
    posicion_backlog: int = 9999
    esfuerzo_talla: Optional[str] = None
    horas_esfuerzo: Optional[int] = None
    deadline_interno: Optional[date] = None
    deadline_cliente: Optional[date] = None
    adjuntos_urls: list[str] = Field(default_factory=list)
    lead_time_horas: Optional[float] = None
    cumplio_sla: Optional[bool] = None
    created_at: datetime
    updated_at: datetime


class BacklogListResponse(BaseModel):
    """Respuesta paginada de lista de backlog."""
    items: list[BacklogResponse]
    total: int
    page: int
    per_page: int
    pages: int


# ============================================================
# METRICAS
# ============================================================

class DashboardResponse(BaseModel):
    """Dashboard general con KPIs."""
    items_completados: int = 0
    items_en_progreso: int = 0
    items_backlog: int = 0
    bugs_criticos_abiertos: int = 0
    sla_cumplido_pct: float = 0
    lead_time_promedio_horas: float = 0
    rendimiento_por_dev: list[dict] = Field(default_factory=list)
    items_en_riesgo: list[dict] = Field(default_factory=list)


# ============================================================
# GENERICOS
# ============================================================

class MessageResponse(BaseModel):
    """Respuesta generica de exito."""
    message: str
    codigo: Optional[str] = None


class ErrorResponse(BaseModel):
    """Respuesta de error."""
    error: str
    detail: Optional[str] = None
