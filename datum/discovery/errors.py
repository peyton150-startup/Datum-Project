"""Discovery failures, split by what they mean for a run.

Kept in their own module because both the framework (`collector`) and every
adapter (`kubernetes`, and the OCI collector to come) raise and catch them, and
no adapter should have to import the framework to signal a bad record.

The split is the whole point. These two exceptions are not degrees of the same
problem -- they are the two halves of DESIGN section 11's robustness rule:

- `MalformedProviderData` is *data*. One record was junk. The run continues,
  the rejection is counted, and everything else is still persisted.
- `ProviderUnavailable` is *absence of data*. Nothing was read, so there is
  nothing to persist and no basis for any claim about the estate.
- `PartialProviderRead` is *incomplete data*. Some of the estate was read and
  the rest was not, which is a third outcome rather than a shade of either.
"""

from collections.abc import Sequence


class MalformedProviderData(Exception):
    """One provider record could not be normalized to a snapshot.

    Raised by an adapter's `normalize`, per record, and caught by the framework
    loop. It never aborts a read: DESIGN section 11 states that one malformed
    record is data, not an exception path, and CF-1 was precisely the defect of
    treating it as one.

    Messages carry the identifiers needed to find the offending record.
    """


class ProviderUnavailable(Exception):
    """The provider could not be read at all, so the run has no observations.

    Raised by an adapter's `fetch`. Ends the run as FAILED with zero counts.
    Distinct from a partial read on purpose: a FAILED run is never allowed to
    imply that anything is absent from the estate, because it never looked.
    """


class PartialProviderRead(Exception):
    """The read began, returned some records, and then stopped early.

    The case DESIGN section 11 describes for exhausted retries mid-pagination:
    page one arrived, page four did not. It is neither of the other two
    outcomes, and collapsing it into either loses something real.

    - Treated as `ProviderUnavailable`, the records already in hand are thrown
      away, which contradicts "always persist what parsed".
    - Treated as a clean read, the pages never fetched become resources that
      appear to have vanished from the estate.

    So it carries the records it did read, and the framework persists them and
    marks the run PARTIAL **with a gap**. A gap is not the same as a rejection:
    a rejected record was seen and refused, while a gap is an unknown number of
    resources never seen at all. That is why it cannot simply be counted into
    `errors` -- nobody knows what to add.
    """

    def __init__(self, records: "Sequence[object]", reason: str) -> None:
        super().__init__(f"provider read stopped early after {len(records)} record(s): {reason}")
        self.records = tuple(records)
        self.reason = reason
