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

# One audit line in this many, for fields configured `sampled_audit`. Global on
# purpose: it is an operator's noise dial, not a property of any field, so it
# stays out of the comparison configuration that #71 just settled. Must be at
# least 1, which `AuditLogWriter` enforces where the value is used rather than
# here, where a raised exception would be a failure to import Django settings.
AUDIT_SAMPLE_RATE = int(os.environ.get("DATUM_AUDIT_SAMPLE_RATE", "100"))

# Logging (#83). Whether audit records exist is Datum's decision, not a
# property of whoever runs it. With no LOGGING dict, root sits at WARNING with
# no handlers, so `datum.reconcile.audit` INFO records were discarded one step
# before any handler question arose; what made Datum's other `logger.info` call
# sites visible in practice was Celery configuring the root logger itself.
#
# Both halves of the shape below are load-bearing, and both were measured
# rather than read off the documentation:
#
#   - The level lives on `datum`. Python consults the level of the originating
#     logger only, never its ancestors', so a worker started at
#     `--loglevel=warning` still emits `datum.*` INFO records, and one started
#     at `--loglevel=info` still honours DATUM_LOG_LEVEL=WARNING. Visibility
#     stops being a property of the runner in both directions.
#   - The handler lives only on root, and nothing in the `datum` namespace
#     holds one. A worker clears root's handlers and installs its own, so ours
#     is replaced rather than joined; a handler on `datum` would survive that
#     replacement and print every record twice, which is the regression this
#     issue exists to avoid.
#
# The alternative -- a handler on `datum` with `propagate: False` -- also emits
# once under a worker, and was measured and rejected. pytest attaches its
# capture handler to root, so severing propagation makes `caplog` blind to
# every `datum` logger: it fails four existing tests in tests/kernel/
# test_audit.py, and phase 2H's audit test depends on the same mechanism.
#
# Known and accepted, because both repairs are worse: Django installs
# DEFAULT_LOGGING before this dict, so the `django` logger keeps its own
# console handler *and* propagates, and its framework messages therefore print
# twice while DEBUG is on. Naming `django` here to stop that would strip those
# handlers (dictConfig clears a named logger's handlers) or copy Django's own
# configuration into this file -- one rule with two encodings. No Datum logger
# and no third-party logger is affected.
LOG_LEVEL = os.environ.get("DATUM_LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    # Adds a level and a destination and takes nothing away, which is what
    # leaves every existing logger behaving as it does today.
    "disable_existing_loggers": False,
    "formatters": {"datum": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "datum"}},
    # Root keeps WARNING so third-party libraries are not made verbose by a
    # decision that is only about Datum's own streams.
    "root": {"handlers": ["console"], "level": "WARNING"},
    # No handlers on purpose: the empty list is the rule, not an omission.
    "loggers": {"datum": {"level": LOG_LEVEL, "handlers": [], "propagate": True}},
}

CELERY_BROKER_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
