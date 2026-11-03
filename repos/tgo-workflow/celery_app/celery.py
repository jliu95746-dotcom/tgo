from celery import Celery
from app.config import settings

celery_app = Celery(
    "workflow_tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "celery_app.tasks", "celery_app.timer_tasks", "celery_app.timer_probe_tasks",
        "celery_app.webhook_tasks",
        "celery_app.event_source_tasks",
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_soft_time_limit=300,  # 5 minutes
    task_time_limit=360,       # 6 minutes
    task_default_queue=settings.WORKFLOW_QUEUE_NAME,
    beat_scheduler="celery_app.timer_scheduler:TimerHealthScheduler",
    task_routes={
        "celery_app.tasks.*": {"queue": settings.WORKFLOW_QUEUE_NAME},
    },
    beat_schedule={
        "audit-waiting-event-sources": {
            "task": "audit_waiting_event_sources", "schedule": 60.0,
            "options": {"queue": settings.WORKFLOW_QUEUE_NAME},
        },
        "recover-webhook-deliveries": {
            "task": "recover_webhook_deliveries", "schedule": 30.0,
            "options": {"queue": settings.WORKFLOW_QUEUE_NAME},
        },
        "collect-and-dispatch-workflow-timers": {
            "task": "sweep_workflow_timers", "schedule": 30.0,
            "options": {"queue": settings.WORKFLOW_QUEUE_NAME},
        },
        "expire-workflow-approvals": {
            "task": "expire_workflow_approvals",
            "schedule": 60.0,
            "options": {"queue": settings.WORKFLOW_QUEUE_NAME},
        },
        "resume-pending-workflow-approvals": {
            "task": "resume_pending_workflow_approvals",
            "schedule": 60.0,
            "options": {"queue": settings.WORKFLOW_QUEUE_NAME},
        },
        "recover-pending-initial-workflows": {
            "task": "recover_pending_initial_workflows",
            "schedule": 60.0,
            "options": {"queue": settings.WORKFLOW_QUEUE_NAME},
        },
        "expire-running-internal-events": {
            "task": "expire_running_internal_events",
            "schedule": 60.0,
            "options": {"queue": settings.WORKFLOW_QUEUE_NAME},
        },
        "expire-pending-source-inputs": {
            "task": "expire_pending_source_inputs",
            "schedule": 60.0,
            "options": {"queue": settings.WORKFLOW_QUEUE_NAME},
        },
    },
)
