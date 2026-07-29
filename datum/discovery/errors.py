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
"""


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
