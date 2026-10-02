"""Translate separately owned declared schemas and operator policy at the boundary.

No comparison type is persisted twice. A string declaration can select string
or timestamp semantics; the existing FieldConfig validators resolve that choice
from the explicit mode, rather than a second table of mode names.
"""

from collections.abc import Mapping
from typing import Any

from datum.kinds.models import Kind
from datum.reconcile.attribute_types import FIELD_TYPES
from datum.reconcile.models import ComparisonPolicy
from datum.reconcile.schema import ComparisonSchema, FieldConfig, SchemaError


def resolve_declared_field_types(kind: Kind) -> dict[str, tuple[str, ...]]:
    """Current governing schema; future provenance resolution belongs here."""
    return {
        name: tuple(
            field_type for field_type, declared in FIELD_TYPES.items() if declared == type_name
        )
        for name, type_name in kind.attribute_schema.items()
    }


def resolve_comparison_policy(kind: Kind) -> dict[str, Any]:
    """Current operator policy, independently of the declaration's schema."""
    row = ComparisonPolicy.objects.filter(kind=kind).first()
    if row is None:
        return {}
    if not isinstance(row.fields, dict):
        raise SchemaError(f"Kind {kind.name}: comparison policy must be a dict")
    return dict(row.fields)


def comparison_schema(
    kind_name: str,
    field_types: Mapping[str, tuple[str, ...]],
    policy: dict[str, Any],
) -> ComparisonSchema:
    """Validate policy shape and derive the type selected by its explicit mode."""
    if not isinstance(policy, dict):
        raise SchemaError(f"Kind {kind_name}: comparison policy must be a dict")
    raw = {}
    for name, definition in policy.items():
        config = _configured_field(kind_name, name, field_types.get(name, ()), definition)
        raw[name] = {
            "type": config.field_type,
            "comparison": config.comparison,
            "logging": config.logging,
        }
    return ComparisonSchema(kind_name, raw)


def _configured_field(
    kind: str, name: str, candidates: tuple[str, ...], definition: Any
) -> FieldConfig:
    if not isinstance(definition, dict) or set(definition) != {"comparison", "logging"}:
        raise SchemaError(f"Kind {kind}, field {name}: expected comparison and logging only")
    if not isinstance(definition["logging"], str):
        raise SchemaError(f"Kind {kind}, field {name}: logging must be a string")
    comparison = definition["comparison"]
    if not isinstance(comparison, dict) or not isinstance(comparison.get("mode"), str):
        raise SchemaError(f"Kind {kind}, field {name}: comparison needs an explicit string mode")
    error: SchemaError = SchemaError(f"Kind {kind}, field {name}: no compatible declared type")
    for field_type in candidates:
        try:
            return FieldConfig(kind, name, field_type, comparison, definition["logging"])
        except SchemaError as exc:
            error = exc
    raise error


def configure_comparison_policy(kind: Kind, policy: dict[str, Any]) -> ComparisonPolicy:
    """Reject invalid or incomplete policy before it becomes active.

    Makes activating policy with an invented type, absent mode or absent logging
    choice unavailable through the supported write path.
    """
    schema = comparison_schema(kind.name, resolve_declared_field_types(kind), policy)
    for name in kind.attribute_schema:
        schema.get_field_config(name)
    row, _created = ComparisonPolicy.objects.update_or_create(
        kind=kind, defaults={"fields": policy}
    )
    return row
