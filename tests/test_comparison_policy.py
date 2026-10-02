from importlib import import_module

import pytest
from django.apps import apps

from datum.kinds.models import Kind
from datum.reconcile.models import ComparisonPolicy
from datum.reconcile.policy import (
    comparison_schema,
    configure_comparison_policy,
    resolve_declared_field_types,
)
from datum.reconcile.schema import MissingFieldConfig, SchemaError
from datum.reconcile.service import _load_comparison_schemas

pytestmark = pytest.mark.django_db


def policy(mode="exact_value", logging="discrepancy"):
    return {"replicas": {"comparison": {"mode": mode}, "logging": logging}}


def test_seed_policy_uses_declared_types_without_repeating_them():
    schemas = _load_comparison_schemas({"Deployment", "ComputeInstance"})
    assert schemas["Deployment"].get_field_config("replicas").field_type == "numeric"
    assert schemas["ComputeInstance"].get_field_config("shape").field_type == "string"
    assert schemas["ComputeInstance"].get_field_config("ocpus").field_type == "numeric"
    seed = import_module("datum.reconcile.migrations.0004_seed_comparison_schemas").seed
    row = ComparisonPolicy.objects.get(kind__name="Deployment")
    row.fields = policy("tolerance(2)")
    row.save()
    seed(apps, None)
    row.refresh_from_db()
    assert row.fields == policy("tolerance(2)")


def test_supported_write_validates_before_activating_policy():
    kind = Kind.objects.get(name="Deployment")
    configure_comparison_policy(kind, policy("tolerance(2)"))
    with pytest.raises(SchemaError):
        configure_comparison_policy(kind, policy("tolerance(inf)"))
    assert ComparisonPolicy.objects.get(kind=kind).fields == policy("tolerance(2)")


def test_incomplete_write_is_rejected_but_existing_incomplete_policy_is_tolerated():
    kind = Kind.objects.get(name="Deployment")
    kind.attribute_schema["enabled"] = "bool"
    kind.save()
    with pytest.raises(MissingFieldConfig):
        configure_comparison_policy(kind, policy())
    assert set(_load_comparison_schemas({kind.name})[kind.name].fields) == {"replicas"}


def test_no_stored_policy_returns_no_schema():
    ComparisonPolicy.objects.all().delete()
    assert _load_comparison_schemas({"Deployment"}) == {}


def test_malformed_stored_policy_does_not_abort_loading_other_kinds():
    row = ComparisonPolicy.objects.get(kind__name="Deployment")
    row.fields = ["not a field mapping"]
    row.save()
    assert set(_load_comparison_schemas({"Deployment", "ComputeInstance"})) == {"ComputeInstance"}


def test_unusable_field_does_not_discard_usable_neighbors():
    row = ComparisonPolicy.objects.get(kind__name="ComputeInstance")
    row.fields["shape"]["comparison"]["mode"] = "invalid"
    row.save()
    schema = _load_comparison_schemas({"ComputeInstance"})["ComputeInstance"]
    assert set(schema.fields) == {"ocpus"}
    row.fields["ocpus"] = {"comparison": {"mode": "invalid"}, "logging": "discrepancy"}
    row.save()
    assert _load_comparison_schemas({"ComputeInstance"}) == {}


@pytest.mark.parametrize(
    "definition",
    [
        None,
        {},
        {"comparison": {}, "logging": "debug"},
        {"comparison": None, "logging": "debug"},
        {"comparison": {"mode": 1}, "logging": "debug"},
        {"comparison": {"mode": "exact_value"}, "logging": "invalid"},
        {"comparison": {"mode": "exact_value"}, "logging": None},
        {"type": "numeric", "comparison": {"mode": "exact_value"}, "logging": "debug"},
    ],
)
def test_invalid_policy_shape_is_rejected(definition):
    with pytest.raises(SchemaError):
        comparison_schema("Deployment", {"replicas": ("numeric",)}, {"replicas": definition})


def test_unknown_declared_type_and_uncovered_field_are_rejected():
    kind = Kind.objects.get(name="Deployment")
    kind.attribute_schema = {"replicas": "future"}
    assert resolve_declared_field_types(kind) == {"replicas": ()}
    with pytest.raises(SchemaError):
        comparison_schema(kind.name, {}, policy())
    with pytest.raises(SchemaError):
        comparison_schema(kind.name, {}, [])


def test_string_declaration_can_select_timestamp_policy_without_persisted_type():
    kind = Kind.objects.create(name="Clock", attribute_schema={"observed_at": "str"})
    configured = {
        "observed_at": {
            "comparison": {"mode": "semantic_utc", "precision": "second"},
            "logging": "debug",
        }
    }
    configure_comparison_policy(kind, configured)
    schema = _load_comparison_schemas({kind.name})[kind.name]
    assert schema.get_field_config("observed_at").field_type == "timestamp"
