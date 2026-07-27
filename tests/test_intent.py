"""Ingestion at the boundary: Git, the document tree, and projection (WBS 1.3.2, 1.3.3)."""

import subprocess

import pytest

from datum.graph.models import DeclaredResource
from datum.intent.errors import InvalidRevision
from datum.intent.ingest import ingest_revision
from datum.intent.models import IntentRevision
from datum.intent.repository import RepositoryUnavailable

TENANT = "00000000-0000-0000-0000-000000000001"
pytestmark = pytest.mark.django_db


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def active_declared(tenant_id=TENANT):
    return DeclaredResource.objects.filter(tenant_id=tenant_id, revision__is_active=True)


def test_ingest_projects_declared_resource_traceable_to_commit(intent_repo):
    revision = ingest_revision(TENANT, intent_repo())
    assert revision.is_active
    assert len(revision.commit_sha) == 40
    resource = DeclaredResource.objects.get(tenant_id=TENANT, name="web")
    assert resource.attributes == {"replicas": 3}
    assert resource.revision_id == revision.id


def test_malformed_document_rejects_whole_revision(intent_repo):
    ingest_revision(TENANT, intent_repo())
    with pytest.raises(InvalidRevision):
        ingest_revision(TENANT, intent_repo("fixtures/intent-repo-malformed"))
    # Previous revision still active, and the bad one left no partial state.
    assert IntentRevision.objects.filter(tenant_id=TENANT, is_active=True).count() == 1
    assert active_declared().count() == 1


def test_duplicate_commit_is_idempotent(intent_repo):
    repo = intent_repo()
    first = ingest_revision(TENANT, repo)
    second = ingest_revision(TENANT, repo)
    assert first.id == second.id
    assert DeclaredResource.objects.filter(tenant_id=TENANT).count() == 1


def test_a_path_that_is_not_a_repository_raises_this_layers_exception(tmp_path):
    """ingest_revision raises exactly RepositoryUnavailable or InvalidRevision.

    It is a public entry point -- the poll task is one caller, and DESIGN 10
    anticipates a webhook as another -- and poll_intent_repository promises it
    never raises while catching only those two. A raw CalledProcessError
    escaping here would break that promise.
    """
    not_a_repo = tmp_path / "empty"
    not_a_repo.mkdir()
    with pytest.raises(RepositoryUnavailable):
        ingest_revision(TENANT, str(not_a_repo))
    assert IntentRevision.objects.count() == 0


def test_a_directory_inside_another_repository_is_not_mistaken_for_one(tmp_path):
    """`git -C <dir> rev-parse HEAD` searches upward.

    Without a root check this returns the *enclosing* repository's HEAD, and
    ingestion records a revision against a commit from a repository that never
    declared these resources -- silently, with a plausible-looking 40-character
    SHA. D2 requires every resource to trace to the commit that declared it.
    """
    outer = tmp_path / "outer"
    (outer / "inner").mkdir(parents=True)
    git(outer, "init", "-q")
    git(outer, "config", "user.email", "t@t")
    git(outer, "config", "user.name", "t")
    (outer / "README").write_text("x", encoding="utf-8")
    git(outer, "add", "-A")
    git(outer, "commit", "-qm", "outer repo")

    with pytest.raises(RepositoryUnavailable, match="not the root"):
        ingest_revision(TENANT, str(outer / "inner"))
    assert IntentRevision.objects.count() == 0


def test_duplicate_identity_is_rejected_by_the_validator_not_the_database(intent_repo):
    """CF-2. The failure must be a domain exception, not IntegrityError."""
    with pytest.raises(InvalidRevision) as caught:
        ingest_revision(TENANT, intent_repo("fixtures/intent-repo-duplicate"))
    (error,) = caught.value.errors
    assert "is declared by 2 documents" in error.message
    assert "deployments/web-again.yaml" in error.message
    assert "deployments/web.yaml" in error.message
    # Nothing was written: the revision was refused before projection.
    assert IntentRevision.objects.filter(tenant_id=TENANT).count() == 0
    assert DeclaredResource.objects.count() == 0


# --------------------------------------------------------------------------
# Full-rebuild projection (DESIGN section 10, open question 3)
# --------------------------------------------------------------------------


def test_second_revision_rebuilds_the_plane_and_deactivates_the_first(intent_repo):
    first = ingest_revision(TENANT, intent_repo())
    second = ingest_revision(TENANT, intent_repo("fixtures/intent-repo-v2"))

    assert first.id != second.id
    first.refresh_from_db()
    assert not first.is_active
    assert second.is_active

    # The active plane is exactly revision 2's document set.
    active = {(r.name, r.attributes["replicas"]) for r in active_declared()}
    assert active == {("web", 5), ("api", 2)}


def test_prior_revision_rows_are_retained_not_overwritten(intent_repo):
    first = ingest_revision(TENANT, intent_repo())
    ingest_revision(TENANT, intent_repo("fixtures/intent-repo-v2"))

    # Full rebuild keeps history: revision 1 still holds web at replicas=3.
    historic = DeclaredResource.objects.get(revision=first, name="web")
    assert historic.attributes == {"replicas": 3}
    assert DeclaredResource.objects.filter(tenant_id=TENANT).count() == 3


def test_only_one_revision_is_ever_active(intent_repo):
    ingest_revision(TENANT, intent_repo())
    ingest_revision(TENANT, intent_repo("fixtures/intent-repo-v2"))
    assert IntentRevision.objects.filter(tenant_id=TENANT, is_active=True).count() == 1


def test_documents_are_found_anywhere_in_the_tree(intent_repo, tmp_path):
    """Layout carries no meaning: a document names its own kind."""
    repo = intent_repo()
    nested = f"{repo}/team-a/prod"
    import os
    import subprocess

    os.makedirs(nested)
    with open(f"{nested}/api.yaml", "w", encoding="utf-8") as handle:
        handle.write(
            "apiVersion: datum.dev/v1\nkind: Deployment\n"
            "metadata:\n  name: api\n  scope: default\n"
            "attributes:\n  replicas: 2\n"
        )
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "add api"], check=True, capture_output=True)

    ingest_revision(TENANT, repo)
    assert {r.name for r in active_declared()} == {"web", "api"}
