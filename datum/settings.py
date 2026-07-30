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

# Intent repository (ADR-004). Empty URL means "not configured": the poll task
# logs and does nothing rather than failing every five minutes.
INTENT_REPO_URL = os.environ.get("DATUM_INTENT_REPO_URL", "")
INTENT_REPO_BRANCH = os.environ.get("DATUM_INTENT_REPO_BRANCH", "main")
INTENT_WORKTREE_DIR = os.environ.get("DATUM_INTENT_WORKTREE", str(BASE_DIR / ".intent-worktree"))
# Bounded staleness: drift between a push and its revision is at most this long.
INTENT_POLL_SECONDS = int(os.environ.get("DATUM_INTENT_POLL_SECONDS", "300"))

# Discovery (DESIGN section 11). Empty source means "no cluster to read": the
# collection task logs and does nothing rather than failing every interval,
# which is the same not-configured contract the intent poller honours.
# Phase 3 reads a recorded payload; WBS 1.4.2 points this at a live cluster.
KUBERNETES_SOURCE = os.environ.get("DATUM_K8S_SOURCE", "")
# "recorded" replays the payload at DATUM_K8S_SOURCE; "cluster" reads a live
# cluster via in-cluster credentials or a kubeconfig. Recorded is the default
# so nothing reaches for a cluster that was never configured.
KUBERNETES_MODE = os.environ.get("DATUM_K8S_MODE", "recorded")
# Empty means every namespace, which is the scope discovery wants: a resource
# nobody declared is exactly what it exists to find.
KUBERNETES_NAMESPACE = os.environ.get("DATUM_K8S_NAMESPACE", "")

# Oracle Cloud. Recorded payloads only until credentials exist (DESIGN section
# 11), so there is no mode switch yet -- an unset source means "no OCI estate to
# read" and the task logs and does nothing, the same not-configured contract the
# other two schedules honour.
OCI_SOURCE = os.environ.get("DATUM_OCI_SOURCE", "")
# Bounded staleness for the estate, the discovery-side twin of the poll interval.
COLLECT_SECONDS = int(os.environ.get("DATUM_COLLECT_SECONDS", "300"))

CELERY_BROKER_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
