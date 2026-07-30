"""The Oracle Cloud collector: reads compute instances, normalizes them.

The second collector, and the first test of whether the framework generalizes or
merely fit its first adapter. It supplies a `fetch` and a `normalize` and owns
nothing else -- no loop, no run record, no idea what a discrepancy is.

**Recorded payloads only, for now.** DESIGN section 11 defers the live OCI read
until credentials exist, and says plainly why that is acceptable: the normalizer
is the part that can be wrong, and it is fully testable against a captured
response. When credentials arrive this gains a `ClusterSource`-shaped sibling and
`normalize` does not change -- the same split that let the Kubernetes collector
move to a live cluster without touching its normalizer.

**Provider vocabulary stops here.** OCI says compartment; Datum says scope. OCI
says display-name; Datum says name. The translation is this module's whole job,
and it is why a Datum intent document declaring an instance looks nothing like an
OCI API call (section 10).
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from datum.discovery.errors import MalformedProviderData
from datum.discovery.recorded import RecordedSource
from datum.reconcile.domain import ResourceSnapshot

KIND_NAME = "ComputeInstance"

# Where OCI puts the list in a ListInstances response.
ENVELOPE_KEY = "data"

# The natural-key components plus this kind's attributes, paired with where they
# live in an OCI instance record. A table so a rejection can name every field it
# is missing rather than only the first.
#
# Every one is required, which is what keeps the optionality simplification
# deferred rather than merely postponed -- see PROJECT_PLAN, WBS 1.5.0. If a
# realistic payload ever cannot be modelled this way, that deferral is over.
_REQUIRED_FIELDS = (
    ("display-name", ("display-name",)),
    ("compartment-id", ("compartment-id",)),
    ("id", ("id",)),
    ("shape", ("shape",)),
    ("shape-config.ocpus", ("shape-config", "ocpus")),
)


class InstanceSource(Protocol):
    """Where the records come from, and nothing about what they mean."""

    def read(self) -> Sequence[object]: ...


class OracleCloudCollector:
    """Reads compute instances from a recorded ListInstances response."""

    name = "oci"
    kind = KIND_NAME

    def __init__(self, source: InstanceSource) -> None:
        self.source = source

    def fetch(self) -> Sequence[object]:
        """Delegated wholesale. Nothing here judges a record."""
        return self.source.read()

    def normalize(self, record: object, tenant_id: str) -> ResourceSnapshot:
        """One OCI instance to one snapshot, or a rejection.

        The discovery barricade (ADR-008) for this provider. A record missing any
        natural-key component is structurally invalid rather than incomplete: it
        would match nothing, so the same instance would surface as one declared
        orphan plus one discovered orphan.

        Note what is *not* carried across: `lifecycle-state` is in the payload
        and is deliberately not an attribute. `attribute_schema` is shared by
        both planes, so every attribute must be something intent can declare, and
        no author declares that an instance is currently RUNNING. Modelling
        observed-only fields is a precedence question, not a collector one.
        """
        if not isinstance(record, Mapping):
            raise MalformedProviderData(
                f"instance record is {type(record).__name__}, not a mapping: {record!r}"
            )

        found = {label: _dig(record, path) for label, path in _REQUIRED_FIELDS}
        missing = sorted(label for label, value in found.items() if value is None)
        if missing:
            raise MalformedProviderData(
                f"incomplete instance record, missing {', '.join(missing)}: {record!r}"
            )

        return ResourceSnapshot(
            kind=KIND_NAME,
            tenant_id=tenant_id,
            # A compartment is what OCI calls the thing Datum calls a scope.
            scope=str(found["compartment-id"]),
            name=str(found["display-name"]),
            provider_id=str(found["id"]),
            attributes={
                "shape": str(found["shape"]),
                "ocpus": found["shape-config.ocpus"],
            },
        )


def from_recording(path: str) -> OracleCloudCollector:
    """The recorded-payload collector, which is all of them for now."""
    return OracleCloudCollector(RecordedSource(path, ENVELOPE_KEY))


def _dig(record: Mapping[str, object], path: tuple[str, ...]) -> object | None:
    """Follow a key path, returning None the moment it stops being a mapping.

    An absent key and a key whose parent is the wrong type are the same answer
    here -- the field is not readable -- and both are the caller's to report.
    """
    current: object = record
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


__all__ = ["ENVELOPE_KEY", "KIND_NAME", "InstanceSource", "OracleCloudCollector", "from_recording"]
