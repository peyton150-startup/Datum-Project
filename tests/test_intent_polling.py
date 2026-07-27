"""Git sync and the scheduled poll (WBS 1.3.2)."""

import shutil
import subprocess
from pathlib import Path

import pytest
from django.test import override_settings

from datum.graph.models import DeclaredResource
from datum.intent.models import IntentRevision
from datum.intent.repository import RepositoryUnavailable, sync_worktree
from datum.intent.tasks import poll_intent_repository

TENANT = "00000000-0000-0000-0000-000000000001"
BRANCH = "main"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def origin_repo(tmp_path):
    """A real Git repository standing in for the remote intent repo."""

    def _make(source: str = "fixtures/intent-repo") -> Path:
        repo = tmp_path / "origin"
        shutil.copytree(source, repo)
        git(repo, "init", "-q", "-b", BRANCH)
        git(repo, "config", "user.email", "t@t")
        git(repo, "config", "user.name", "t")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "declare web")
        return repo

    return _make


def head_of(repo) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


# --------------------------------------------------------------------------
# sync_worktree
# --------------------------------------------------------------------------


def test_first_sync_clones_and_returns_head(origin_repo, tmp_path):
    origin = origin_repo()
    worktree = tmp_path / "worktree"
    assert sync_worktree(str(origin), BRANCH, str(worktree)) == head_of(origin)
    assert (worktree / "deployments" / "web.yaml").exists()


def test_second_sync_of_an_unchanged_remote_is_a_no_op(origin_repo, tmp_path):
    origin = origin_repo()
    worktree = str(tmp_path / "worktree")
    first = sync_worktree(str(origin), BRANCH, worktree)
    assert sync_worktree(str(origin), BRANCH, worktree) == first


def test_sync_picks_up_a_new_commit(origin_repo, tmp_path):
    origin = origin_repo()
    worktree = tmp_path / "worktree"
    first = sync_worktree(str(origin), BRANCH, str(worktree))

    (origin / "deployments" / "web.yaml").write_text(
        "apiVersion: datum.dev/v1\nkind: Deployment\n"
        "metadata:\n  name: web\n  scope: default\n"
        "attributes:\n  replicas: 7\n",
        encoding="utf-8",
    )
    git(origin, "commit", "-aqm", "scale web")

    second = sync_worktree(str(origin), BRANCH, str(worktree))
    assert second != first
    assert second == head_of(origin)
    assert "replicas: 7" in (worktree / "deployments" / "web.yaml").read_text(encoding="utf-8")


def test_local_edits_to_the_worktree_are_discarded(origin_repo, tmp_path):
    # The worktree is a cache of the remote, not a place to author intent.
    origin = origin_repo()
    worktree = tmp_path / "worktree"
    sync_worktree(str(origin), BRANCH, str(worktree))
    document = worktree / "deployments" / "web.yaml"
    document.write_text("apiVersion: datum.dev/v1\n# tampered\n", encoding="utf-8")

    sync_worktree(str(origin), BRANCH, str(worktree))
    assert "tampered" not in document.read_text(encoding="utf-8")


def test_unreachable_remote_raises_this_modules_exception(tmp_path):
    # Not CalledProcessError: exceptions carry the abstraction of the module
    # they cross out of.
    with pytest.raises(RepositoryUnavailable):
        sync_worktree(str(tmp_path / "no-such-repo"), BRANCH, str(tmp_path / "worktree"))


def test_missing_git_executable_is_reported_in_this_modules_terms(monkeypatch, tmp_path):
    import datum.intent.repository as repository

    def no_git_on_path(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(repository.subprocess, "run", no_git_on_path)
    with pytest.raises(RepositoryUnavailable, match="git executable not found"):
        sync_worktree("https://example.invalid/repo.git", BRANCH, str(tmp_path / "worktree"))


def test_unknown_branch_raises_repository_unavailable(origin_repo, tmp_path):
    origin = origin_repo()
    with pytest.raises(RepositoryUnavailable):
        sync_worktree(str(origin), "no-such-branch", str(tmp_path / "worktree"))


# --------------------------------------------------------------------------
# The scheduled poll
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_poll_without_a_configured_repository_does_nothing():
    with override_settings(INTENT_REPO_URL=""):
        assert poll_intent_repository() is None
    assert IntentRevision.objects.count() == 0


@pytest.mark.django_db
def test_poll_ingests_the_remote_head(origin_repo, tmp_path):
    origin = origin_repo()
    with override_settings(
        INTENT_REPO_URL=str(origin),
        INTENT_REPO_BRANCH=BRANCH,
        INTENT_WORKTREE_DIR=str(tmp_path / "worktree"),
    ):
        commit_sha = poll_intent_repository()

    assert commit_sha == head_of(origin)
    assert IntentRevision.objects.get(tenant_id=TENANT, is_active=True).commit_sha == commit_sha
    assert DeclaredResource.objects.filter(tenant_id=TENANT, name="web").count() == 1


@pytest.mark.django_db
def test_polling_twice_on_one_commit_creates_one_revision(origin_repo, tmp_path):
    # The poll runs every few minutes; almost every run sees a commit it has
    # already ingested.
    origin = origin_repo()
    with override_settings(
        INTENT_REPO_URL=str(origin),
        INTENT_REPO_BRANCH=BRANCH,
        INTENT_WORKTREE_DIR=str(tmp_path / "worktree"),
    ):
        first = poll_intent_repository()
        second = poll_intent_repository()

    assert first == second
    assert IntentRevision.objects.count() == 1
    assert DeclaredResource.objects.count() == 1


@pytest.mark.django_db
def test_poll_swallows_a_rejected_revision_and_keeps_the_previous_one(origin_repo, tmp_path):
    origin = origin_repo()
    worktree = str(tmp_path / "worktree")
    settings_kwargs = {
        "INTENT_REPO_URL": str(origin),
        "INTENT_REPO_BRANCH": BRANCH,
        "INTENT_WORKTREE_DIR": worktree,
    }
    with override_settings(**settings_kwargs):
        good = poll_intent_repository()

        # Break the intent repo, then poll again.
        (origin / "deployments" / "web.yaml").write_text(
            "apiVersion: datum.dev/v1\nkind: Deployment\n"
            "metadata:\n  name: web\n  scope: default\n"
            "attributes: {}\n",
            encoding="utf-8",
        )
        git(origin, "commit", "-aqm", "break web")

        # A scheduled task that raises takes the next poll with it.
        assert poll_intent_repository() is None

    active = IntentRevision.objects.get(tenant_id=TENANT, is_active=True)
    assert active.commit_sha == good
    assert DeclaredResource.objects.get(tenant_id=TENANT, name="web").attributes == {"replicas": 3}


@pytest.mark.django_db
def test_poll_swallows_an_unreachable_repository(tmp_path):
    with override_settings(
        INTENT_REPO_URL=str(tmp_path / "no-such-repo"),
        INTENT_REPO_BRANCH=BRANCH,
        INTENT_WORKTREE_DIR=str(tmp_path / "worktree"),
    ):
        assert poll_intent_repository() is None
    assert IntentRevision.objects.count() == 0
