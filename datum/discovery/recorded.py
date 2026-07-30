"""Replaying a recorded provider payload from disk.

Shared by every collector, because "open a file, parse it, find the list of
records, and turn any failure into `ProviderUnavailable`" is the same sequence
for all of them and was already written twice by the time the second collector
arrived.

What is *not* shared is what a record means. Each adapter keeps its own
normalizer; this only hands over the raw list.

Recorded payloads are how CI stays hermetic (DESIGN section 11) and how a
provider whose credentials do not exist yet is still fully testable.
"""

import json
from collections.abc import Sequence

from datum.discovery.errors import ProviderUnavailable


class RecordedSource:
    """A provider response captured from a real API and replayed from disk.

    `envelope_key` is where that provider puts its list of records -- `items`
    for a Kubernetes list, `data` for an OCI one. The difference is the
    provider's, not Datum's, so it is a parameter rather than two classes.
    """

    def __init__(self, path: str, envelope_key: str) -> None:
        self.path = path
        self.envelope_key = envelope_key

    def read(self) -> Sequence[object]:
        """Every record in the payload, raw and unjudged.

        Anything that stops the payload being read at all is
        `ProviderUnavailable`: the run observed nothing, so it must not be
        allowed to imply anything about the estate. Junk *inside* a record is a
        different matter and belongs to the adapter's normalizer.
        """
        try:
            with open(self.path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise ProviderUnavailable(
                f"could not read the recorded payload at {self.path}: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProviderUnavailable(
                f"the recorded payload at {self.path} is not valid JSON: {exc}"
            ) from exc

        return records_in(payload, self.envelope_key, self.path)


def records_in(payload: object, envelope_key: str, origin: str) -> list[object]:
    """The record list out of a provider's envelope.

    An envelope with no list is not an empty estate: there is no record to
    reject, so nothing was observed and the caller learns nothing about what
    exists. That distinction is what keeps a malformed response from reading as
    a deletion.
    """
    if not isinstance(payload, dict):
        raise ProviderUnavailable(f"{origin} is not a provider list envelope")
    records = payload.get(envelope_key)
    if not isinstance(records, list):
        raise ProviderUnavailable(f"{origin} has no {envelope_key!r} list")
    return records


__all__ = ["RecordedSource", "records_in"]
