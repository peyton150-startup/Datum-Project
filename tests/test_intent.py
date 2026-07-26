import pytest

from datum.graph.models import DeclaredResource
from datum.intent.ingest import InvalidRevision, ingest_revision
from datum.intent.models import IntentRevision

TENANT = "00000000-0000-0000-0000-000000000001"
pytestmark = pytest.mark.django_db


def test_ingest_projects_declared_resource_traceable_to_commit(intent_repo):
    repo = intent_repo()
    revision = ingest_revision(TENANT, repo)
    assert revision.is_active
    assert len(revision.commit_sha) == 40
    res = DeclaredResource.objects.get(tenant_id=TENANT, name="web")
    assert res.attributes == {"replicas": 3}
    assert res.revision_id == revision.id


def test_malformed_document_rejects_whole_revision(intent_repo):
    good = intent_repo()
    ingest_revision(TENANT, good)
    bad = intent_repo("fixtures/intent-repo-malformed")
    with pytest.raises(InvalidRevision):
        ingest_revision(TENANT, bad)
    # previous revision still active, no partial state from the bad one
    assert IntentRevision.objects.filter(tenant_id=TENANT, is_active=True).count() == 1
    assert DeclaredResource.objects.filter(tenant_id=TENANT).count() == 1


def test_duplicate_commit_is_idempotent(intent_repo):
    repo = intent_repo()
    first = ingest_revision(TENANT, repo)
    second = ingest_revision(TENANT, repo)
    assert first.id == second.id
    assert DeclaredResource.objects.filter(tenant_id=TENANT).count() == 1
