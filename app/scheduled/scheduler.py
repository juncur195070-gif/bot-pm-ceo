"""
Scheduler — Configura las tareas automaticas con APScheduler.

Tareas programadas:
  23:00 diario  → scoring_wsjf (recalcula prioridades)
  08:00 lunes   → asignacion_semanal (Bug Guard + sprint)
  09:00 L-V     → monitoreo_alertas (deadlines + estancadas)

Se inicia en main.py al arrancar la app.
Corre en el mismo proceso — no necesita servicios externos.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler(timezone="America/Lima")


def configurar_tareas():
    """
    Registra todas las tareas programadas.
    Llamar UNA vez al arrancar la app.
    """
    from app.scheduled.scoring import ejecutar_scoring
    from app.scheduled.asignacion import ejecutar_asignacion
    from app.scheduled.monitoreo import ejecutar_monitoreo

    # Scoring WSJF — cada noche a las 11pm Lima
    scheduler.add_job(
        ejecutar_scoring,
        CronTrigger(hour=23, minute=0),
        id="scoring_nocturno",
        name="Scoring WSJF nocturno",
        replace_existing=True,
    )

    # Asignacion semanal — lunes 8am Lima
    scheduler.add_job(
        ejecutar_asignacion,
        CronTrigger(day_of_week="mon", hour=8, minute=0),
        id="asignacion_lunes",
        name="Asignacion semanal",
        replace_existing=True,
    )

    # Monitoreo alertas — lunes a viernes 9am Lima
    scheduler.add_job(
        ejecutar_monitoreo,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0),
        id="monitoreo_diario",
        name="Monitoreo alertas diarias",
        replace_existing=True,
    )

    scheduler.start()
    print("   ⏰ Scheduler iniciado (scoring 23:00, asignacion lun 08:00, monitoreo L-V 09:00)")
