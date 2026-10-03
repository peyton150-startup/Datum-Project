from hypothesis import given, settings
from hypothesis import strategies as st

from datum.reconcile.audit import AuditLogWriter
from datum.reconcile.diff import reconcile
from datum.reconcile.domain import ResourceSnapshot
from datum.reconcile.matcher import match_resources
from datum.reconcile.schema import ComparisonSchema


@settings(deadline=None)
@given(
    st.dictionaries(st.sampled_from(["a", "b", "c"]), st.integers(-10, 10)),
    st.dictionaries(st.sampled_from(["a", "b", "c"]), st.integers(-10, 10)),
)
def test_configured_diff_is_deterministic_across_field_order_and_audit_rates(left, right):
    """Sampling and insertion order cannot decide drift; logging I/O is not timed."""
    schemas = {
        "K": ComparisonSchema(
            "K",
            {
                name: {
                    "type": "numeric",
                    "comparison": {"mode": "tolerance(2)"},
                    "logging": "sampled_audit",
                }
                for name in ("a", "b", "c")
            },
        )
    }

    def result(declared, discovered, rate):
        d = ResourceSnapshot("K", "t", "s", "r", None, declared)
        x = ResourceSnapshot("K", "t", "s", "r", "id", discovered)
        return reconcile(match_resources([d], [x]), schemas, AuditLogWriter(rate))

    forward = result(left, right, 1)
    reversed_ = result(dict(reversed(list(left.items()))), dict(reversed(list(right.items()))), 3)
    assert forward == reversed_
    assert [item.field_name for item in forward.field_discrepancies] == sorted(
        item.field_name for item in forward.field_discrepancies
    )
