"""Logging configuration (#83).

Audit records must be a product of Datum's configuration rather than of the
runner that happens to invoke it. Before this configuration existed, every
`datum.*` INFO record was discarded by the effective WARNING level one step
before any handler question arose, and what made them visible in practice was
Celery configuring the root logger itself.

Two of these tests demonstrate that property. Two are guards against
reintroducing the shape that breaks it, and say so rather than borrowing
credit: the duplicate-emission failure is only observable under a real worker,
which no unit test starts.
"""

import logging
import logging.config

from django.conf import settings

AUDIT_LOGGER = "datum.reconcile.audit"

# Every module in the repository that holds a logger, so that widening the
# namespace cannot quietly leave one of them behind. Some of these currently
# log only above INFO; they are listed because the level rule is about the
# namespace, not about which call sites happen to exist today.
DATUM_LOGGERS = (
    "datum.discovery.collector",
    "datum.discovery.tasks",
    "datum.discovery.absence",
    "datum.discovery.retry",
    "datum.discovery.kubernetes",
    "datum.intent.tasks",
    "datum.reconcile.service",
    "datum.locks",
    AUDIT_LOGGER,
)


def test_every_datum_logger_is_enabled_for_info():
    """The bug: settings leave `datum.*` at the inherited WARNING level.

    Under that bug an audit record is discarded before reaching a handler, and
    no amount of handler configuration recovers it. Measured against the real
    settings module before this configuration existed, `isEnabledFor(INFO)`
    was False for every name below, so this fixture does distinguish the two.
    """
    for name in DATUM_LOGGERS:
        assert logging.getLogger(name).isEnabledFor(logging.INFO), name


def test_the_configured_level_is_what_decides_visibility():
    """The bug: visibility is hardcoded, so no configuration can withdraw it.

    A stream that cannot be turned off by configuration cannot be turned on by
    it either -- it is on by accident. Re-applies the dict at WARNING and
    checks the audit logger goes quiet, which is the same property phase 2H's
    acceptance test needs: it must be capable of failing for a configuration
    reason.
    """
    quiet = {**settings.LOGGING, "loggers": {"datum": {"level": "WARNING", "handlers": []}}}
    try:
        logging.config.dictConfig(quiet)
        assert not logging.getLogger(AUDIT_LOGGER).isEnabledFor(logging.INFO)
    finally:
        logging.config.dictConfig(settings.LOGGING)

    assert logging.getLogger(AUDIT_LOGGER).isEnabledFor(logging.INFO)


def test_no_logger_in_the_datum_namespace_holds_a_handler():
    """Guard, not a demonstration: the doubling it prevents needs a worker.

    A worker clears root's handlers and installs its own but leaves named
    loggers alone, so a handler anywhere under `datum` survives that
    replacement and every record prints twice -- once from the surviving
    handler, once from the worker's. Verified under an actual worker rather
    than here; this only fails the moment someone reintroduces the shape.
    """
    for name in ("datum", *DATUM_LOGGERS):
        logger = logging.getLogger(name)
        assert logger.handlers == [], f"{name} holds its own handler"
        assert logger.propagate, f"{name} does not propagate"


def test_django_keeps_the_logging_it_configured_for_itself():
    """Guard against the repair that looks tidy: naming `django` in the dict.

    dictConfig clears a named logger's handlers before applying the entry, so
    declaring `django` here to stop it double-printing would silence Django's
    own logging instead -- or copy Django's configuration into our settings,
    giving one rule two encodings. Neither is worth it, so `django` must stay
    absent from the dict and keep the handlers Django gave it.
    """
    assert "django" not in settings.LOGGING["loggers"]

    django_logger = logging.getLogger("django")
    assert django_logger.handlers, "Django's own handlers were stripped"
    assert not django_logger.disabled
