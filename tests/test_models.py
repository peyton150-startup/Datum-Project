import pytest
from django.db import IntegrityError
from django.db.transaction import atomic

from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.enums import CollectorRunStatus
from datum.graph.models import DeclaredResource
from datum.intent.models import IntentRevision
from datum.kinds.models import Kind

TENANT = "00000000-0000-0000-0000-000000000001"
pytestmark = pytest.mark.django_db


def _kind():
    """The Deployment kind, seeded by kinds.0002 and present in every test database."""
    return Kind.objects.get(name="Deployment")


def test_seeded_deployment_kind_persists():
    assert _kind().attribute_schema == {"replicas": "int"}


def test_discovered_natural_key_is_unique_per_tenant():
    k = _kind()
    run = CollectorRun.objects.create(
        tenant_id=TENANT, collector_name="kubernetes", status=CollectorRunStatus.SUCCESS
    )
    DiscoveredResource.objects.create(
        tenant_id=TENANT,
        kind=k,
        name="web",
        scope="default",
        provider_id="uid-1",
        attributes={"replicas": 5},
        run=run,
    )
    with pytest.raises(IntegrityError), atomic():
        DiscoveredResource.objects.create(
            tenant_id=TENANT,
            kind=k,
            name="web",
            scope="default",
            provider_id="uid-2",
            attributes={"replicas": 5},
            run=run,
        )


def test_declared_natural_key_is_unique_within_a_revision():
    k = _kind()
    rev = IntentRevision.objects.create(tenant_id=TENANT, commit_sha="c" * 40)
    DeclaredResource.objects.create(
        tenant_id=TENANT, kind=k, name="web", scope="default", attributes={}, revision=rev
    )
    with pytest.raises(IntegrityError), atomic():
        DeclaredResource.objects.create(
            tenant_id=TENANT, kind=k, name="web", scope="default", attributes={}, revision=rev
        )


def test_same_declared_natural_key_may_recur_in_a_later_revision():
    """The constraint is per revision: history of one resource across commits is legal."""
    k = _kind()
    first = IntentRevision.objects.create(tenant_id=TENANT, commit_sha="d" * 40)
    second = IntentRevision.objects.create(tenant_id=TENANT, commit_sha="e" * 40)
    for rev in (first, second):
        DeclaredResource.objects.create(
            tenant_id=TENANT, kind=k, name="web", scope="default", attributes={}, revision=rev
        )
    assert DeclaredResource.objects.filter(tenant_id=TENANT, name="web").count() == 2


def test_only_one_active_revision_per_tenant():
    IntentRevision.objects.create(tenant_id=TENANT, commit_sha="a" * 40, is_active=True)
    with pytest.raises(IntegrityError), atomic():
        IntentRevision.objects.create(tenant_id=TENANT, commit_sha="b" * 40, is_active=True)
