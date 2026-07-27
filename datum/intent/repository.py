"""Keep a local checkout of the intent repository in step with its remote.

Git is the origin of declared intent (ADR-004), so this module owns the one
place Datum talks to it. It only ever reads: clone, fetch, and hard reset onto
the remote branch. Nothing here pushes, commits, or writes a file into the
worktree -- the local checkout is a cache of the remote, and any local edit to
it is discarded on the next sync rather than preserved.
"""

import subprocess
from pathlib import Path

# The checkout is disposable, so there is no reason to carry history for it.
CLONE_DEPTH = "1"


class RepositoryUnavailable(Exception):
    """The intent repository could not be read. The previous revision stands."""


def sync_worktree(repo_url: str, branch: str, worktree_dir: str) -> str:
    """Bring `worktree_dir` to the tip of `branch` and return that commit SHA.

    Safe to call when the worktree is absent, stale, or locally dirty: the
    first clones, the second fast-forwards, and the third is reset away.
    """
    worktree = Path(worktree_dir)
    if (worktree / ".git").exists():
        _fetch_and_reset(branch, worktree)
    else:
        _clone(repo_url, branch, worktree)
    return _head_sha(worktree)


def _clone(repo_url: str, branch: str, worktree: Path) -> None:
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(
        None,
        "clone",
        "--depth",
        CLONE_DEPTH,
        "--branch",
        branch,
        repo_url,
        str(worktree),
    )


def _fetch_and_reset(branch: str, worktree: Path) -> None:
    _git(worktree, "fetch", "--depth", CLONE_DEPTH, "origin", branch)
    # Hard reset rather than merge: the worktree is a cache of the remote, and a
    # merge could invent a tree that no commit in the repository ever held.
    _git(worktree, "reset", "--hard", f"origin/{branch}")


def _head_sha(worktree: Path) -> str:
    return _git(worktree, "rev-parse", "HEAD")


def _git(worktree: Path | None, *args: str) -> str:
    location = [] if worktree is None else ["-C", str(worktree)]
    try:
        result = subprocess.run(
            ["git", *location, *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        # Exceptions crossing this boundary carry this module's abstraction, not
        # subprocess's, and name the operation that failed.
        raise RepositoryUnavailable(
            f"git {args[0]} failed with status {exc.returncode}: {exc.stderr.strip()}"
        ) from exc
    except FileNotFoundError as exc:
        raise RepositoryUnavailable("git executable not found on PATH") from exc
    return result.stdout.strip()
