import shutil
import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def intent_repo(tmp_path):
    """Copy fixtures/intent-repo into a temp dir and make it a real git repo."""

    def _make(source: str = "fixtures/intent-repo") -> str:
        # One directory per source: a test may build both the good and the
        # malformed repo, and copytree refuses an existing destination.
        repo = tmp_path / Path(source).name
        shutil.copytree(source, repo)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "declare web")
        return str(repo)

    return _make
