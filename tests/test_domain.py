from datum.reconcile.domain import ResourceSnapshot


def test_natural_key_is_kind_tenant_scope_name():
    snap = ResourceSnapshot(
        kind="Deployment",
        tenant_id="t1",
        scope="default",
        name="web",
        provider_id=None,
        attributes={"replicas": 3},
    )
    assert snap.natural_key == ("Deployment", "t1", "default", "web")


def test_snapshot_is_frozen():
    snap = ResourceSnapshot("Deployment", "t1", "default", "web", None, {})
    try:
        snap.name = "other"  # type: ignore[misc]
    except Exception as exc:
        assert "cannot assign" in str(exc).lower() or "frozen" in str(exc).lower()
    else:
        raise AssertionError("snapshot must be immutable")
