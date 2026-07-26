import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DATUM_SECRET_KEY", "dev-insecure-key-not-for-production")
DEBUG = os.environ.get("DATUM_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DATUM_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "datum.kinds",
    "datum.graph",
    "datum.intent",
    "datum.discovery",
    "datum.reconcile",
    "datum.workflow",
    "datum.api",
]

MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "datum.urls"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "datum"),
        "USER": os.environ.get("POSTGRES_USER", "datum"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "datum"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

# Single-tenant constant for Phase 1. Every query is still written tenant-scoped.
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

CELERY_BROKER_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
