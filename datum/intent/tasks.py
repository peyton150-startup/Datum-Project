"""Scheduled ingestion (WBS 1.3.2).

Polling is the phase 2 trigger; a Git webhook is deferred to an expansion. Both
call `ingest_revision`, which is idempotent on `(tenant, commit_sha)`, so
neither trigger has to be delivered exactly once and adding the second one
later changes nothing here.
"""

import logging

from celery import shared_task
from django.conf import settings

from datum.intent.errors import InvalidRevision, RevisionConflict
from datum.intent.ingest import ingest_revision
from datum.intent.repository import RepositoryUnavailable, sync_worktree

logger = logging.getLogger(__name__)


@shared_task(name="datum.intent.poll_intent_repository")
def poll_intent_repository() -> str | None:
    """Fetch the intent repository and ingest its HEAD.

    Returns the active commit SHA, or None if there was nothing to ingest.
    Never raises: a scheduled task that dies takes the next poll with it, and
    both failure modes here leave the previous revision correctly in force.
    """
    if not settings.INTENT_REPO_URL:
        logger.info("intent repository not configured; skipping poll")
        return None

    tenant_id = settings.DEFAULT_TENANT_ID
    try:
        sync_worktree(
            settings.INTENT_REPO_URL,
            settings.INTENT_REPO_BRANCH,
            settings.INTENT_WORKTREE_DIR,
        )
        revision = ingest_revision(tenant_id, settings.INTENT_WORKTREE_DIR)
    except RepositoryUnavailable:
        # Transient by nature. The next poll retries; the active revision stands.
        logger.exception("intent repository unavailable for tenant %s", tenant_id)
        return None
    except RevisionConflict:
        # Another trigger got there first, which is the ordinary outcome when a
        # poll and a webhook fire together rather than a failure. The winner's
        # revision stands and there is nothing to retry.
        logger.info("intent revision already claimed by another writer for tenant %s", tenant_id)
        return None
    except InvalidRevision as exc:
        # Not transient: retrying will not fix malformed YAML. Log every error
        # so the author can see all of them, and wait for the next commit.
        logger.error("intent revision rejected for tenant %s:\n%s", tenant_id, exc, exc_info=False)
        return None

    return revision.commit_sha
