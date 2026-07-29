"""Intent validation failures.

Kept in their own module because both the validator (`documents`) and the
ingestion boundary (`ingest`) raise and catch them, and neither should have to
import the other to do it.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentError:
    """One reason one document was rejected, located well enough to fix."""

    source: str
    line: int | None
    message: str

    def __str__(self) -> str:
        location = self.source if self.line is None else f"{self.source}:{self.line}"
        return f"{location}: {self.message}"


class InvalidDocument(Exception):
    """A single intent document failed validation.

    Carries either a `line` (known directly, as for a syntax error) or a `path`
    -- the dotted key path that failed, such as `("metadata", "name")` -- which
    the caller resolves to a line against the document's own text. Between them
    every rejection can be pointed at a place in a file.
    """

    def __init__(
        self,
        message: str,
        line: int | None = None,
        path: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(message)
        self.line = line
        self.path = path


class RevisionConflict(Exception):
    """Another writer claimed this revision identity first (CF-4, CF-5).

    A concurrency condition, not a document one. It exists as its own exception
    rather than reusing `InvalidRevision` because there is no document to blame:
    `InvalidRevision` carries a list of `DocumentError`s pointing at bad YAML,
    and synthesizing a fake one for a lost race would make the error lie about
    what went wrong.

    Raised in place of the `IntegrityError` a unique constraint would otherwise
    throw across the module boundary. That the constraint catches the race is
    correct; letting the database's abstraction escape into callers written
    against this module's is the defect (DESIGN section 11, ADR-008).

    Not fatal. The revision that won is a valid revision, and the loser's work
    was redundant by definition -- both were projecting the same repository.
    """


class InvalidRevision(Exception):
    """A revision was rejected whole; no state was written.

    Carries every error found across the document set rather than the first,
    so one push surfaces every problem instead of one problem per push.
    """

    def __init__(self, errors: Sequence[DocumentError]) -> None:
        assert errors, "InvalidRevision requires at least one error"
        self.errors: tuple[DocumentError, ...] = tuple(errors)
        super().__init__(self._render())

    def _render(self) -> str:
        heading = f"revision rejected, {len(self.errors)} error(s):"
        return "\n".join([heading, *(f"  {error}" for error in self.errors)])
