"""The one test that needs a real cluster (WBS 1.4.2).

Everything else about the cluster source is tested hermetically in
`test_kubernetes_cluster.py`. What cannot be faked is the part DESIGN section 11
says the live cluster exists to exercise: that the SDK call is spelled
correctly, that `_preload_content=False` really yields a `DeploymentList`
envelope, and that a real Deployment survives the normalizer.

Skipped unless a cluster is actually reachable, because a test that needs a
cluster does not gate the build. Run it against local k3s with:

    DATUM_K8S_LIVE=1 pytest tests/test_live_cluster.py -v
"""

import os

import pytest

from datum.discovery.errors import PartialProviderRead
from datum.discovery.kubernetes import KIND_NAME, ClusterSource, from_cluster

pytestmark = pytest.mark.skipif(
    os.environ.get("DATUM_K8S_LIVE") != "1",
    reason="needs a reachable cluster; set DATUM_K8S_LIVE=1 to run",
)

TENANT = "00000000-0000-0000-0000-000000000001"


def read_cluster() -> list[object]:
    """Every Deployment the cluster reports, tolerating a partial read.

    A cluster large enough to page while a page fails is a real outcome and not
    a test failure -- what is under test is the shape of what came back.
    """
    try:
        return list(ClusterSource().read())
    except PartialProviderRead as partial:
        return list(partial.records)


def test_the_sdk_call_returns_a_deployment_list_envelope():
    """The spelling check. A renamed kwarg or a wrong API group fails here and
    nowhere else, because every other test fakes this call."""
    records = read_cluster()

    assert isinstance(records, list)
    for record in records:
        assert isinstance(
            record, dict
        ), "_preload_content=False should yield raw JSON dicts, not SDK model objects"


def test_a_real_deployment_normalizes():
    """Proves the recorded fixtures are not fiction: the same normalizer that
    CI runs against a replayed payload accepts what a cluster actually sends."""
    records = read_cluster()
    if not records:
        pytest.skip("cluster has no Deployments to normalize")

    snapshot = from_cluster().normalize(records[0], TENANT)

    kind, tenant, scope, name = snapshot.natural_key
    assert kind == KIND_NAME
    assert tenant == TENANT
    assert scope and name
    assert snapshot.provider_id
    assert isinstance(snapshot.attributes["replicas"], int)
