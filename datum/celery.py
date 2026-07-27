import os

from celery import Celery
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datum.settings")
app = Celery("datum")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "poll-intent-repository": {
        "task": "datum.intent.poll_intent_repository",
        "schedule": settings.INTENT_POLL_SECONDS,
    },
}
