# WBS 1.5.3: Precedence Policy

**Status:** Specification (to be decided)

**Scope:** When a discrepancy is detected on a field, which plane's value is authoritative? The precedence policy is a lookup table `(kind, field)` → rule that decides.

---

## Core Principles (Already Decided)

From DESIGN §14:

- **Explainability is structural.** The result includes the deciding rule; it's not hidden in a log or synthesized. `resolve_field()` returns `(authoritative_value, deciding_rule)` or `Undecidable`, never a default.
- **Missing rule → undecidable.** A field with no precedence rule becomes queue work. It does not fall back to "declared wins" or "discovered wins."
- **Rules keyed on `(kind, field)`.** No tenant dimension. Kinds are global; if that changes, rules migrate with kinds.
- **Implement as a lookup, not conditionals.** If comparison logic needs branches, it's a table. Precedence rules are definitely a table.

---

## Questions to Answer

### 1. Policy Shape: Global vs Per-Tenant vs Per-Kind?

**Options:**

**A) Global policy** — One rule per `(kind, field)` across all tenants
- Simplest: one table, uniform behavior
- Downside: teams cannot customize precedence for their own resources

**B) Per-tenant override** — Global default + tenant-level overrides
- Teams can set custom precedence for their own kinds
- Complexity: which rule wins when both exist?

**C) Per-kind default + per-tenant override** — Three-level hierarchy
- Kind author sets default, tenant can override per field
- Most flexible but most complex

---

### 2. Granularity: What Does Each Rule Decide?

**Options:**

**A) Binary: Declared or Discovered** — Each rule picks one plane as authoritative
- Rule: `"declared_authoritative"` or `"discovered_authoritative"`
- Result: always picks one side's value
- Downside: cannot express "use the stricter value" or "merged result"

**B) Function-based** — Each rule is a function `(declared, discovered) → value`
- Rule: `"use_max"`, `"use_min"`, `"use_declared"`, `"use_discovered"`, `"merge_lists"`, etc.
- Result: call the function to get the decision
- Allows complex logic per field type

**C) Enum of known operators** — Limited set of pre-defined operators
- Rule values: `DECLARED_AUTHORITATIVE`, `DISCOVERED_AUTHORITATIVE`, `USE_MAX`, `USE_MIN`, `MERGE_LISTS`, `COMPUTED_CHECKSUM`, etc.
- Result: switch on the enum, apply the logic
- Bounded complexity, extensible

---

### 3. Resolution Order: What If Multiple Rules Could Apply?

**Options:**

**A) Exact match only** — Look up exact `(kind, field)` tuple, no fallbacks
- If not found → undecidable
- Requires every field to be explicitly configured

**B) Cascade with fallback** — Try `(kind, field)`, then `(kind, "*")`, then `("*", field)`, then global default
- Reduces configuration burden
- Risk: wrong rule silently wins if hierarchy is wrong

**C) Most specific match wins** — Define specificity order (e.g., field-specific > kind-specific > global), apply first match
- Example: `Deployment.replicas` rule overrides general `Deployment.*` rule
- Clean and explicit

---

### 4. Versioning: What Happens When Rules Change?

**Options:**

**A) Immutable rules** — Rules are versioned. When you change a rule, it creates a new version. Old discrepancies keep the old version's rule.
- Each Discrepancy record stores the rule version that decided it
- If rule changes, old records don't retroactively change their outcome
- Requires: `PrecedenceRule.version` column

**B) Latest rules apply retroactively** — When a rule changes, it applies to all future evaluations
- Simpler: no versioning needed
- Risk: re-evaluating old discrepancies under new rules gives different results, confusing operators

**C) Snapshot per reconciliation run** — Each run stores which rule version was active
- Discrepancies from run N use rules from run N
- Run N+1 uses updated rules
- Requires: `ReconciliationRun.rule_snapshot` JSON

---

### 5. Open Discrepancies on Rule Change: What Happens to Queue Items?

**Example:** A field `cpu` has rule `"declared_authoritative"`. An OPEN discrepancy exists: declared=2, discovered=4. Then you change the rule to `"discovered_authoritative"`. What happens to that queue item?

**Options:**

**A) Lock in the old rule** — The OPEN discrepancy keeps the rule that decided it
- Operator reviews it under the original rule
- Rule change only affects NEW discrepancies
- Queue doesn't retroactively change

**B) Apply new rule immediately** — The OPEN discrepancy is re-evaluated under the new rule
- If new rule would suppress it, it gets suppressed automatically
- If new rule would make it authoritative on a different side, the queue item updates
- Risk: operator is reviewing something that just changed out from under them

**C) Alert operator and hold** — On rule change, mark affected OPEN items as "rule changed, review"
- Operator acknowledges the rule change before the new rule applies
- Safest but requires UI/workflow support

---

### 6. Audit Trail: How Do We Record Which Rule Decided a Field?

**Options:**

**A) Store rule ID in Discrepancy** — Each Discrepancy record includes `precedence_rule_id`
- Compact: one foreign key
- Requires: `PrecedenceRule` table with `id, kind, field, ...`

**B) Store rule content in Discrepancy** — Each Discrepancy includes the full rule as JSON
- Self-contained: no lookup needed to explain the decision
- Slightly redundant but more durable if rules change

**C) Computed on read** — Query the current rule, apply it, derive what the decision would be
- Most flexible but depends on current state
- Breaks if rules are deleted

---

## Proposal: Balanced Decisions

Based on simplicity + explainability + operator control:

### Decision 1: Per-Kind Default + Per-Tenant Override
- **Global base:** A default precedence rule table `(kind, field)`
- **Tenant override:** Each tenant can override rules for kinds they use
- **Storage:** `Kind.precedence_rules` (JSON) + `TenantKindOverrides` table

### Decision 2: Enum of Operators
```python
class PrecedenceOperator(TextChoices):
    DECLARED_AUTHORITATIVE = "declared"      # Use declared value
    DISCOVERED_AUTHORITATIVE = "discovered"  # Use discovered value
    USE_MAX = "max"                          # For numeric fields
    USE_MIN = "min"                          # For numeric fields
    MERGE_LISTS = "merge"                    # For list fields (union)
    INTERSECTION_LISTS = "intersection"      # For list fields (intersection)
    UNDECIDABLE = "undecidable"             # Field has no rule
```

### Decision 3: Exact Match + Explicit Fallback
- Look up exact `(kind, field)`
- If not found: explicitly return `Undecidable`
- No hidden defaults or cascades
- Requires configuration per field

### Decision 4: Immutable Rules with Versioning
- Each `PrecedenceRule` has a `version` integer
- Increment when the rule changes
- Each `Discrepancy` stores `applied_rule_version`
- Operators can see "what rule was in effect when this was decided"

### Decision 5: Lock In Old Rule for OPEN Discrepancies
- When a rule changes, OPEN items keep the old rule
- New discrepancies use the new rule
- Operator review is stable and not disrupted
- Clear audit trail: "this was decided by Rule v2"

### Decision 6: Store Rule ID + Operator in Discrepancy
- `Discrepancy.precedence_rule_id` (foreign key)
- `Discrepancy.precedence_operator` (enum value, for quick display)
- Compact and queryable
- Operator can click through to see the rule definition

---

## Schema Design

```python
# Kind model gains:
class Kind(models.Model):
    name: str
    tenant_id: UUID  # Always NULL (global)
    # ... existing fields ...
    default_precedence: JSONField = None  # Optional default rules per field

# New table:
class PrecedenceRule(models.Model):
    kind: ForeignKey(Kind)
    field_name: str
    operator: CharField(choices=PrecedenceOperator)
    version: int = 1
    created_at: DateTimeField
    updated_at: DateTimeField
    reason: TextField  # Why this rule?

    class Meta:
        unique_together = [["kind", "field_name", "version"]]
        index_together = [["kind", "field_name"]]

# Tenant-level overrides:
class TenantPrecedenceOverride(models.Model):
    tenant_id: UUID
    kind: ForeignKey(Kind)
    field_name: str
    operator: CharField(choices=PrecedenceOperator)
    version: int = 1
    created_at: DateTimeField

    class Meta:
        unique_together = [["tenant_id", "kind", "field_name", "version"]]

# Update Discrepancy model:
class Discrepancy(models.Model):
    # ... existing fields ...
    precedence_rule: ForeignKey(PrecedenceRule, null=True, blank=True)
    precedence_operator: CharField(choices=PrecedenceOperator, null=True)
    authoritative_plane: CharField(choices=Plane)  # Already exists, now filled by rule
```

---

## Resolution Function

```python
def resolve_field(
    kind: Kind,
    field_name: str,
    declared_value: PlaneValue,
    discovered_value: PlaneValue,
    tenant_id: UUID
) -> tuple[PlaneValue, PrecedenceRule] | Undecidable:
    """
    Apply precedence rules to decide a field's authoritative value.

    Returns either (value, rule) or Undecidable.

    Resolution order:
    1. Check tenant-level override for (tenant, kind, field)
    2. Check kind-level rule for (kind, field)
    3. Return Undecidable (no rule found)
    """

    # Try tenant override first
    override = TenantPrecedenceOverride.objects.filter(
        tenant_id=tenant_id,
        kind=kind,
        field_name=field_name
    ).order_by("-version").first()

    if override:
        rule = override
    else:
        # Try kind default
        rule = PrecedenceRule.objects.filter(
            kind=kind,
            field_name=field_name
        ).order_by("-version").first()

    if not rule:
        return Undecidable(kind=kind, field=field_name, reason="No precedence rule")

    # Apply the operator
    operator = PrecedenceOperator(rule.operator)

    if operator == PrecedenceOperator.DECLARED_AUTHORITATIVE:
        return (declared_value, rule)
    elif operator == PrecedenceOperator.DISCOVERED_AUTHORITATIVE:
        return (discovered_value, rule)
    elif operator == PrecedenceOperator.USE_MAX:
        # Numeric fields only
        return (max(declared_value._value, discovered_value._value), rule)
    # ... etc for other operators

    return Undecidable(kind=kind, field=field_name, reason=f"Unknown operator: {rule.operator}")
```

---

## Adversarial Corpus (Test Cases)

### Scenario 1: No Rule (Undecidable)
- Field: `Deployment.custom_annotation`
- No precedence rule defined
- Discrepancy: declared="value1", discovered="value2"
- Expected: `Undecidable`, field becomes queue work

### Scenario 2: Declared Authoritative
- Field: `Deployment.replicas`
- Rule: `DECLARED_AUTHORITATIVE`
- Discrepancy: declared=3, discovered=5
- Expected: authoritative_value=3, rule stored in Discrepancy

### Scenario 3: Discovered Authoritative
- Field: `Deployment.image`
- Rule: `DISCOVERED_AUTHORITATIVE`
- Discrepancy: declared="v1.0", discovered="v1.1"
- Expected: authoritative_value="v1.1", rule stored

### Scenario 4: Tenant Override
- Kind rule: `Deployment.replicas` = `DECLARED_AUTHORITATIVE`
- Tenant override: `Deployment.replicas` = `DISCOVERED_AUTHORITATIVE`
- Discrepancy: declared=3, discovered=5
- Expected: authoritative_value=5 (tenant rule wins)

### Scenario 5: Rule Change on OPEN Discrepancy
- Field: `Deployment.env`, rule v1 = `DECLARED_AUTHORITATIVE`
- OPEN discrepancy created with rule v1
- Rule v2 issued: `DISCOVERED_AUTHORITATIVE`
- Expected: OPEN discrepancy keeps rule v1, new discrepancies use v2

### Scenario 6: Numeric Operators
- Field: `Deployment.cpu`, rule = `USE_MAX`
- Discrepancy: declared=2, discovered=4
- Expected: authoritative_value=4

### Scenario 7: List Merge
- Field: `Deployment.labels`, rule = `MERGE_LISTS`
- Discrepancy: declared=[a, b], discovered=[b, c]
- Expected: authoritative_value=[a, b, c] (union)

---

## Implementation Notes

- Lookup is O(1): query by `(tenant, kind, field)` with ordering by version descending
- Rules are immutable; updates create new versions
- Backfill: generate default rules for all existing kinds (e.g., Deployment, ComputeInstance)
- UI: kind author can define rules, tenant can override rules

---

## Open Questions (For Design Review)

1. Should rules be versioned per-kind or globally? (Current: per-rule)
2. Should tenant overrides require approval, or immediate?
3. What happens if an operator becomes invalid (e.g., `USE_MAX` on a string field)? Error? Silent fallback?
4. Can rules reference other fields (e.g., "use MAX if both > threshold")? Or always simple?
