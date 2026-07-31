# WBS 1.5.3: Precedence Policy

**Status:** Specification (revised 2026-07-30)

**Scope:** When a discrepancy is detected on a field, which source wins? The precedence policy is a lookup table `(kind, field)` → rule that decides which plane's value is authoritative. Rules are versioned for immutable audit trails. Tenant customization is deferred to v2 (complexity burden unclear).

---

## Core Principles

From DESIGN §14:

- **Explainability is structural.** The result includes the deciding rule; it's not hidden in a log or synthesized. Query functions return `(authoritative_value, deciding_rule)` or `Undecidable`, never a default.
- **Missing rule → undecidable.** A field with no precedence rule becomes queue work. It does not fall back to "declared wins" or "discovered wins."
- **Rules keyed on `(kind, field)` globally.** No tenant dimension in v1. Kinds are global; if that changes, rules migrate with kinds.
- **Implement as a lookup, not conditionals.** If resolution logic needs branches, it's a table. Precedence rules are definitely a table.

### Two Separate Concerns

This spec addresses **source selection** (which plane is authoritative). Reconciliation **operators** (what to do with selected values) are separate:

- **Precedence:** DECLARED_AUTHORITATIVE, DISCOVERED_AUTHORITATIVE (binary choice)
- **Operators:** USE_MAX, USE_MIN, MERGE_LISTS (transformations, handled elsewhere)

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

Based on simplicity + explainability + auditability. **Tenant overrides are deferred to v2** because the complexity-to-value ratio is unclear without demonstrated customer need. This keeps v1 deterministic and focused.

### Decision 1: Global Rules Only (No Tenant Overrides in v1)
- One precedence rule per `(kind, field)` globally
- No `TenantPrecedenceOverride` table in v1
- Rationale:
  - Tenant overrides introduce a lookup hierarchy that complicates both logic and debugging
  - No demonstrated need yet; can add if customers request it
  - Without them, every decision is fully deterministic: same (kind, field, version) → same rule → same behavior
  - If needed later, migration path is clear: add tenant table, update resolution to check tenant first

**Future v2 Addition:** If tenants need custom precedence:
```
Question: Should tenants redefine semantics of a kind?
Answer: Only if they have demonstrated need. Otherwise, use kind's global definition.
```

### Decision 2: Binary Precedence Only (Not Reconciliation Operators)
```python
class PrecedenceOperator(TextChoices):
    DECLARED_AUTHORITATIVE = "declared"      # Source is declared plane
    DISCOVERED_AUTHORITATIVE = "discovered"  # Source is discovered plane
    # Note: USE_MAX, MERGE_LISTS, etc. are NOT precedence rules.
    #       They are reconciliation operators, handled separately in diff/comparison logic.
```

**Rationale:** Precedence answers "which source wins?" not "how to combine them." Conflating these leads to confusion. Example: "Is MERGE_LISTS authoritative?" doesn't make sense—only sources can be authoritative.

### Decision 3: Exact Match + Explicit Fallback
- Look up exact `(kind, field)` tuple
- If not found: explicitly return `Undecidable`
- No hidden defaults or cascades
- Requires configuration per field (explicit over implicit)

### Decision 4: Immutable Rules with Rule ID for Audit
- Each rule is immutable; changes create new rules (not versions of old ones)
- Each rule has a unique `id` (UUID) and `kind_field_composite_id` tuple
- **Don't use version numbers alone** for audit trails
- **Reason:** Different fields in same kind might change at different times:
  - `Deployment.replicas` → v1 (old, unchanging)
  - `Deployment.cpu` → v2 (new, changed)
  - Audit log entry "Rule v2" is ambiguous: which field?

**Schema:**
```python
class PrecedenceRule(models.Model):
    id: UUID = models.UUIDField(primary_key=True, default=uuid4)
    kind: ForeignKey(Kind)
    field_name: str
    operator: CharField(choices=PrecedenceOperator)  # DECLARED_AUTHORITATIVE or DISCOVERED_AUTHORITATIVE
    created_at: DateTimeField(auto_now_add=True)

    # Human-readable audit trail
    created_by: CharField(max_length=255)  # Username or "system"
    reason: TextField  # Why this rule? (required)

    class Meta:
        # Single active rule per (kind, field)
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "field_name"],
                condition=models.Q(deleted_at__isnull=True),
                name="uq_kind_field_active_rule"
            )
        ]

    def __str__(self):
        return f"Rule({self.kind.name}.{self.field_name} → {self.operator}) [ID: {self.id}]"
```

### Decision 5: Two Query Functions (Not One)

**Problem:** A single `resolve_field()` can mean two different things:
1. **Current resolution:** What's the rule NOW? (for new discrepancies)
2. **Historical resolution:** What was the rule THEN? (for auditing old discrepancies)

**Solution:** Two separate functions:

```python
def resolve_field_current(
    kind: Kind,
    field_name: str,
    declared_value: PlaneValue,
    discovered_value: PlaneValue
) -> tuple[PlaneValue, PrecedenceRule] | Undecidable:
    """
    Resolve a field's authoritative value using CURRENT rules.
    Used when processing NEW discrepancies.

    Returns: (authoritative_value, active_rule) or Undecidable
    """
    rule = PrecedenceRule.objects.filter(
        kind=kind,
        field_name=field_name,
        deleted_at__isnull=True  # Only active rules
    ).first()

    if not rule:
        return Undecidable(kind=kind, field=field_name, reason="No precedence rule")

    if rule.operator == "declared":
        return (declared_value, rule)
    elif rule.operator == "discovered":
        return (discovered_value, rule)
    else:
        # Should never happen with two operators
        return Undecidable(kind=kind, field=field_name, reason=f"Unknown operator: {rule.operator}")


def resolve_field_historical(rule_id: UUID, declared_value, discovered_value):
    """
    Resolve a field's authoritative value using a HISTORICAL rule.
    Used when auditing or understanding OLD discrepancies.

    Returns: (authoritative_value, rule_definition)

    This function never returns Undecidable because the rule existed when the
    discrepancy was created. If the rule has since been deleted, we still apply it.
    """
    rule = PrecedenceRule.objects.get(id=rule_id)

    if rule.operator == "declared":
        return (declared_value, rule)
    elif rule.operator == "discovered":
        return (discovered_value, rule)
```

**Rationale:** This prevents accidental use of current rules when historical rules are needed, and vice versa. The function name makes the intent explicit.

### Decision 6: Store Rule ID in Discrepancy (Immutable Snapshot)
```python
class Discrepancy(models.Model):
    # ... existing fields ...
    # Precedence snapshot at creation time
    applied_precedence_rule_id: UUID = models.ForeignKey(PrecedenceRule, null=True, on_delete=models.PROTECT)
    applied_precedence_operator: CharField(choices=PrecedenceOperator, null=True)

    # Both fields denormalized for reporting without joins
```

**Why `on_delete=PROTECT`:** Rules are never actually deleted; only marked deleted. So this FK is forever valid.

### Decision 7: Kind Schema Changes (Deletion, Rename)
- **Field removed from kind schema:** Rules remain in DB. They're harmless but orphaned.
  - Queries simply don't look them up (field no longer has a discrepancy)
  - Operators can view them in audit trail for historical reference

- **Kind renamed:** Kind FK prevents orphaning (cascade delete is NOT used).
  - Administrators must manually migrate rules or delete orphaned rules before deleting the kind
  - Alternative: soft-delete kind, keep rules for historical queries

**Recommendation:** Make Kind soft-deletable (add `deleted_at` column) to preserve rule history forever.

---

## Updated Schema Design

```python
# Updated Kind model:
class Kind(models.Model):
    name: str
    tenant_id: UUID  # Always NULL (global)
    # ... existing fields ...
    deleted_at: DateTimeField(null=True, blank=True)  # Soft delete for history preservation

# Single source of truth for rules:
class PrecedenceRule(models.Model):
    id: UUID = models.UUIDField(primary_key=True, default=uuid4)
    kind: ForeignKey(Kind)
    field_name: str
    operator: CharField(choices=PrecedenceOperator)  # Only: DECLARED_AUTHORITATIVE or DISCOVERED_AUTHORITATIVE

    created_at: DateTimeField(auto_now_add=True)
    created_by: CharField(max_length=255)  # Username or "system"
    reason: TextField  # Why this rule? (required, for audit trail)

    deleted_at: DateTimeField(null=True, blank=True)  # Soft delete, preserves history

    class Meta:
        # Single active rule per (kind, field)
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "field_name"],
                condition=models.Q(deleted_at__isnull=True),
                name="uq_active_kind_field_rule"
            ),
            # Rules only reference kinds and fields that exist in the schema
            models.CheckConstraint(
                check=models.Q(operator__in=["declared", "discovered"]),
                name="ck_valid_operator"
            )
        ]

    def __str__(self):
        return f"Rule({self.kind.name}.{self.field_name} → {self.operator}) [ID: {self.id}]"

# Update Discrepancy model:
class Discrepancy(models.Model):
    # ... existing fields ...
    # Immutable snapshot of which rule decided this discrepancy
    applied_precedence_rule_id: UUID = models.ForeignKey(
        PrecedenceRule,
        null=True,
        blank=True,
        on_delete=models.PROTECT  # Rules never deleted, only soft-deleted
    )
    applied_precedence_operator: CharField(
        choices=PrecedenceOperator,
        null=True,
        blank=True
    )
    # Denormalized for fast queries without joins
```

---

## Resolution Functions

**v1 Implementation uses two functions to prevent confusion:**

### Current Discrepancies (New)
```python
def resolve_field_current(
    kind: Kind,
    field_name: str,
    declared_value: PlaneValue,
    discovered_value: PlaneValue
) -> tuple[PlaneValue, PrecedenceRule] | Undecidable:
    """
    Resolve using CURRENT (active) rules.
    Call this when creating NEW discrepancies.

    Returns: (authoritative_value, active_rule) or Undecidable
    """
    rule = PrecedenceRule.objects.filter(
        kind=kind,
        field_name=field_name,
        deleted_at__isnull=True  # Only active rules
    ).first()

    if not rule:
        return Undecidable(kind=kind, field=field_name, reason="No precedence rule")

    authoritative_value = (
        declared_value if rule.operator == "declared" else discovered_value
    )
    return (authoritative_value, rule)
```

### Historical Discrepancies (Audit)
```python
def resolve_field_historical(
    rule_id: UUID,
    declared_value: PlaneValue,
    discovered_value: PlaneValue
) -> tuple[PlaneValue, PrecedenceRule]:
    """
    Resolve using a HISTORICAL rule (for audit/explanation).
    Call this when explaining OLD discrepancies that were decided by a specific rule.

    Always succeeds because the rule was active when the discrepancy was created.
    Rules are soft-deleted, never hard-deleted.

    Returns: (authoritative_value, rule_definition)
    """
    rule = PrecedenceRule.objects.get(id=rule_id)
    authoritative_value = (
        declared_value if rule.operator == "declared" else discovered_value
    )
    return (authoritative_value, rule)
```

---

## Adversarial Corpus (Test Cases)

### Scenario 1: No Rule (Undecidable)
- Field: `Deployment.custom_annotation`
- No precedence rule defined
- Discrepancy: declared="value1", discovered="value2"
- Expected: `resolve_field_current()` returns `Undecidable`
- Queue work: operator must decide

### Scenario 2: Declared Authoritative
- Field: `Deployment.replicas`
- Active rule: `DECLARED_AUTHORITATIVE`
- Discrepancy: declared=3, discovered=5
- Expected: `resolve_field_current()` returns (3, rule)
- Discrepancy stores rule_id for later audit

### Scenario 3: Discovered Authoritative
- Field: `Deployment.image`
- Active rule: `DISCOVERED_AUTHORITATIVE`
- Discrepancy: declared="v1.0", discovered="v1.1"
- Expected: `resolve_field_current()` returns ("v1.1", rule)

### Scenario 4: Rule Change — Old Discrepancy Audited Historically
- Discrepancy created with rule_id=abc (DECLARED_AUTHORITATIVE)
- Declared: declared=3, discovered=5
- Later, rule changed to DISCOVERED_AUTHORITATIVE (new rule_id=xyz)
- Expected: `resolve_field_historical(rule_id=abc, ...)` still returns 3
- New discrepancies use rule_id=xyz (returns 5)
- Audit trail is clear: two different rules, two different outcomes

### Scenario 5: Rule Immutability (no "versioning")
- Rule created: `Deployment.cpu` = DECLARED_AUTHORITATIVE (id=abc)
- Operator decides to change to DISCOVERED_AUTHORITATIVE
- Action: Create NEW rule (id=xyz), soft-delete old rule (id=abc)
- Old discrepancies still reference abc (immutable)
- New discrepancies reference xyz
- Audit trail: full rule object stored per discrepancy, no ambiguity

### Scenario 6: Rule Audit Trail (No Ambiguity)
- `Deployment.replicas` rule_id=abc created at 2026-01-01
- `Deployment.cpu` rule_id=def created at 2026-01-15
- Log entry: "Rule changed 2026-02-01"
- **With version numbers only:** Which field? Ambiguous!
- **With UUIDs:** rule_id=abc or def is explicit; zero confusion

### Scenario 7: Operator Type Mismatch (Prevented by Schema)
- Attempt: Create rule `Deployment.image` (string) with operator=USE_MAX
- **Schema constraint:** Operator must be DECLARED_AUTHORITATIVE or DISCOVERED_AUTHORITATIVE
- USE_MAX is not a precedence operator; it's a reconciliation operator
- Result: Constraint violation; invalid rule cannot be created
- (USE_MAX would be handled in comparison/diff logic, not precedence)

### Scenario 8: Kind Deleted (Soft Delete)
- Rule exists: `Deployment.cpu` = DECLARED_AUTHORITATIVE (rule_id=abc)
- Kind is soft-deleted: `Deployment.deleted_at = 2026-02-01`
- Rule remains in DB (FK still valid)
- Discrepancies referencing rule_id=abc can still be audited
- Query for active rules filters out soft-deleted kinds
- Historical queries still work

---

## Implementation Notes

- **Lookup complexity:** O(log n) database lookup by (kind, field), not O(1). Practically fast but technically indexed query.
- **Rules are immutable:** Changes create new rules, old rules soft-deleted
- **No versioning per field:** Each rule is a separate object with unique UUID; audit trails are unambiguous
- **No tenant overrides in v1:** Complexity deferred until demonstrated need
- **Soft deletes everywhere:** Kind, Rule, Discrepancy—preserve history forever
- **Backfill:** Generate default rules for all existing kinds (e.g., Deployment, ComputeInstance)
- **UI:** Kind admin can define rules; no tenant customization in v1

---

## Migration Path to v2 (Future, If Needed)

**If customers request tenant-specific rules:**

1. Add `TenantPrecedenceOverride` table (same schema as PrecedenceRule, but with tenant_id)
2. Update `resolve_field_current()` to check tenant override first:
   ```python
   # Try tenant override
   rule = TenantPrecedenceOverride.objects.filter(
       tenant_id=tenant_id,
       kind=kind,
       field_name=field_name,
       deleted_at__isnull=True
   ).first()

   if not rule:
       # Fall back to global rule
       rule = PrecedenceRule.objects.filter(...).first()
   ```
3. Discrepancy stores both `applied_precedence_rule_id` and `applied_tenant_precedence_override_id` (nullable)
4. Document trade-off: each tenant override adds complexity; only add if value is demonstrated

---

## Open Questions Resolved

1. ✅ **Separate current vs historical API?** Yes: `resolve_field_current()` and `resolve_field_historical()`
2. ✅ **Single source of truth?** Yes: PrecedenceRule only, no JSON on Kind
3. ✅ **Tenant customization?** Deferred to v2; no demonstrated need in v1
4. ✅ **Audit trail uniqueness?** UUID per rule, not version numbers (prevents ambiguity)
5. ✅ **Precedence vs operators?** Separated: only DECLARED/DISCOVERED in this table; operators go elsewhere
6. ✅ **Type constraints on operators?** Yes: check constraint enforces valid operators at schema level
7. ✅ **Operator implementation versioning?** Not needed in v1 (only two operators, stable semantics)
8. ✅ **Kind schema changes (delete/rename)?** Soft delete; rules preserved forever
9. ✅ **Version inheritance (global → tenant)?** N/A; no tenant overrides in v1
