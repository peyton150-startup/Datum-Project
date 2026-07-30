# WBS 1.5.2: Diff Engine Comparison Semantics

**Status:** Specification (decided 2026-07-30)

**Scope:** Five open questions about how the diff engine compares field values across declared and discovered planes. These decisions lock in behavior across all reconciliation runs.

---

## 1. Lists vs Sets: Field-Level Configuration

**Decision:** Hybrid approach with per-field configuration. Kind schema declares list comparison semantics for each field.

**Implementation:**

The `Kind` model gains an optional `attribute_schema` that specifies comparison behavior per field:

```python
# Example schema on Deployment kind
{
  "replicas": {"type": "integer"},
  "ports": {"type": "list", "ordered": True},      # Order matters (e.g., init containers)
  "labels": {"type": "list", "ordered": False},    # Set semantics (e.g., tags, labels)
  "env_vars": {"type": "list", "ordered": False},  # Set semantics
}
```

**Comparison Logic:**

- **`ordered: True`** (default) — Exact order required: `[1, 2, 3]` ≠ `[3, 2, 1]`
- **`ordered: False`** — Set semantics: `[1, 2, 3]` == `[3, 2, 1]`, duplicates ignored: `[1, 1, 2]` == `[1, 2]`

**User Experience:**

When declaring a kind, teams can choose:
- Kubernetes Deployments: `ports` are ordered (container startup order matters), `labels` are unordered
- Cloud instances: `security_groups` are unordered, `volume_attachments` are ordered

**Audit Trail:**

Discrepancy records include which comparison rule applied, so operators understand "order difference suppressed" vs "value difference detected."

**Test Cases (Adversarial Corpus):**

- Declared `[1, 2, 3]`, discovered `[3, 2, 1]` with `ordered: True` → **discrepancy**
- Declared `[1, 2, 3]`, discovered `[3, 2, 1]` with `ordered: False` → **no discrepancy**
- Declared `[1, 2]`, discovered `[1, 1, 2]` with `ordered: False` → **no discrepancy**
- Declared `[1, 2]`, discovered `[1, 1, 2]` with `ordered: True` → **discrepancy**

---

## 2. Numeric Precision: User-Configurable with Default

**Decision:** Precision-aware canonicalization (choice C) with user-facing configuration at kind level. Operator chooses per-field: exact match (A) or tolerance window (C).

**Implementation:**

The `Kind` attribute schema specifies numeric comparison for each field:

```python
{
  "replicas": {"type": "integer", "precision": "exact"},      # 3 ≠ 3.0
  "cpu_cores": {"type": "float", "precision": 0.01},          # Within 0.01 is OK
  "memory_bytes": {"type": "integer", "precision": "exact"},  # Must be exact
}
```

**Comparison Logic:**

- **`precision: "exact"`** (default) — Strict equality: `3` ≠ `3.0`, `3.1` ≠ `3.14`
- **`precision: N`** (float) — Tolerance window: `abs(a - b) <= N` → no discrepancy

**User Experience:**

Teams configure per kind:
- Kubernetes Deployment: replicas are exact integers (3 must be 3, not 3.0)
- Cloud instance: CPU cores allow 0.01 tolerance (3.0 == 3.01, both acceptable)

**Audit Trail:**

Discrepancy reports show the rule applied: "exact integer required" vs "within 0.01 tolerance."

**Test Cases (Adversarial Corpus):**

- `3` vs `3.0` with `precision: "exact"` → **discrepancy**
- `3` vs `3.0` with `precision: 0.1` → **no discrepancy**
- `3.0` vs `3.05` with `precision: 0.01` → **discrepancy**
- `3.0` vs `3.005` with `precision: 0.01` → **no discrepancy**

---

## 3. Case and Whitespace: Field-Aware with Error Logging

**Decision:** Configurable normalization (choice C) with detailed error logs. Kind schema declares per-field: exact match or normalized comparison. All normalization decisions logged.

**Implementation:**

The `Kind` attribute schema specifies string comparison:

```python
{
  "name": {"type": "string", "normalize": False},           # Exact: "Name" ≠ "name"
  "labels": {"type": "string", "normalize": True},          # Normalized: "Name" == "name"
  "description": {"type": "string", "normalize": "trim"},   # Trim whitespace only
}
```

**Comparison Logic:**

- **`normalize: False`** (default) — Exact comparison: `"Name"` ≠ `"name"`, `"hello"` ≠ `"hello "`
- **`normalize: True`** — Full normalization: lowercase + trim whitespace
- **`normalize: "trim"`** — Whitespace trimming only: `"hello "` == `"hello"`, but `"Name"` ≠ `"name"`

**Error Logging:**

Every normalization decision is logged with full context:

```
[DIFF] Normalization applied: field=labels, rule=normalize:true
  declared: "MyLabel" (normalized → "mylabel")
  discovered: "MYLABEL" (normalized → "mylabel")
  result: NO DISCREPANCY (values match after normalization)

[DIFF] Whitespace trimmed: field=description, rule=normalize:trim
  declared: "Prod database  " (trimmed → "Prod database")
  discovered: "Prod database" (no change)
  result: NO DISCREPANCY
```

**Audit Trail:**

Discrepancy records include whether normalization was applied. Operators can review logs to understand "why isn't this a discrepancy?"

**User Experience:**

Teams configure per kind:
- Kubernetes Deployment: `name` is exact (case matters), `labels` are normalized
- Cloud instance: all strings exact (case-sensitive IDs)

**Test Cases (Adversarial Corpus):**

- `"Name"` vs `"name"` with `normalize: False` → **discrepancy**
- `"Name"` vs `"name"` with `normalize: True` → **no discrepancy** (logged)
- `"hello "` vs `"hello"` with `normalize: "trim"` → **no discrepancy** (logged)
- `"hello "` vs `"hello"` with `normalize: False` → **discrepancy**

---

## 4. Timestamps and Timezones: Operator-Configurable Precision

**Decision:** Precision-aware comparison (choice C) with operator-defined time representation. Kind schema specifies per-field: strict string match (A), semantic UTC comparison (B), or precision window (C).

**Implementation:**

The `Kind` attribute schema specifies timestamp comparison:

```python
{
  "created_at": {"type": "timestamp", "comparison": "semantic", "precision": "second"},
  "backup_time": {"type": "timestamp", "comparison": "semantic", "precision": "minute"},
  "api_timestamp": {"type": "timestamp", "comparison": "string"},  # Exact string
}
```

**Comparison Logic:**

- **`comparison: "string"`** (default) — Exact string comparison: `"2026-07-30T18:00:00Z"` ≠ `"2026-07-30T10:00:00-08:00"` even if same moment
- **`comparison: "semantic"`** — Parse and normalize to UTC, then compare at specified precision
  - `precision: "day"` — Same calendar day (ignores hour/minute/second/zone)
  - `precision: "hour"` — Same hour in UTC
  - `precision: "minute"` — Same minute in UTC
  - `precision: "second"` — Same second in UTC

**Operator Control:**

Operators choose the representation they care about:

```python
# Example configurations:
{
  "created_at": {
    "type": "timestamp",
    "comparison": "semantic",
    "precision": "second",
    "timezone": "UTC"  # Normalize all to UTC for comparison
  },
  "scheduled_maintenance": {
    "type": "timestamp",
    "comparison": "semantic",
    "precision": "minute",
    "timezone": "operator"  # Use operator's configured timezone
  }
}
```

**Audit Trail:**

All timezone conversions are logged:

```
[DIFF] Timestamp comparison: field=created_at, rule=semantic(second, UTC)
  declared: "2026-07-30T18:00:00Z" (UTC)
  discovered: "2026-07-30T10:00:00-08:00" (parsed to UTC → 2026-07-30T18:00:00Z)
  result: NO DISCREPANCY (same second in UTC)
```

**User Experience:**

Teams configure based on their tolerance:
- Kubernetes: created_at at second precision (UTC)
- Cloud provider: last_modified at minute precision (UTC)
- Application: scheduled_task allows day-level variance (operator timezone)

**Test Cases (Adversarial Corpus):**

- `"2026-07-30T18:00:00Z"` vs `"2026-07-30T18:00:00Z"` with `semantic/second/UTC` → **no discrepancy**
- `"2026-07-30T18:00:00Z"` vs `"2026-07-30T10:00:00-08:00"` with `semantic/second/UTC` → **no discrepancy** (logged)
- `"2026-07-30T18:00:00Z"` vs `"2026-07-30T18:00:00Z"` with `string` → **no discrepancy**
- `"2026-07-30T18:00:00Z"` vs `"2026-07-30T10:00:00-08:00"` with `string` → **discrepancy**
- `"2026-07-30T18:30:00Z"` vs `"2026-07-30T10:00:00-08:00"` with `semantic/minute/UTC` → **discrepancy** (different minutes)

---

## 5. Deep Comparison: Configurable Recursion Depth

**Decision:** Configurable depth (choice C). Kind schema specifies per-field how deeply to recurse into nested objects.

**Implementation:**

The `Kind` attribute schema specifies nesting behavior:

```python
{
  "metadata": {
    "type": "object",
    "recurse": 0  # Treat whole object as opaque, no drilling
  },
  "labels": {
    "type": "object",
    "recurse": 1  # Recurse one level, then treat as opaque
  },
  "config": {
    "type": "object",
    "recurse": -1  # Fully recursive (infinite depth)
  }
}
```

**Comparison Logic:**

- **`recurse: 0`** — No recursion. `{"user": {"name": "alice"}}` vs `{"user": {"name": "Alice"}}` treated as opaque values. Either both sides match exactly or it's a discrepancy.
- **`recurse: 1`** — One level deep. Compare top-level keys, drill into immediate children only
- **`recurse: -1`** (default) — Fully recursive. Drill into all nested levels

**User Experience:**

Teams configure based on importance:
- Kubernetes: `spec` fully recursive (every detail matters), `metadata` opaque (timestamps, resourceVersion not important)
- Cloud provider: `tags` fully recursive, `system_metadata` opaque (provider's internal state)

**Audit Trail:**

Discrepancy reports show which level the difference was detected:

```
[DIFF] Deep comparison: field=spec, rule=recurse:-1
  Path: spec → containers[0] → env → HOME
  declared: "/home/app" (present)
  discovered: "/root" (present)
  result: DISCREPANCY detected at depth 4

[DIFF] Deep comparison: field=metadata, rule=recurse:0
  No drilling into this field (opaque comparison)
  declared: {"version": "1", "timestamp": "..."}
  discovered: {"version": "2", "timestamp": "..."}
  result: DISCREPANCY (objects differ, but not drilling to explain)
```

**Test Cases (Adversarial Corpus):**

- `{"user": {"name": "alice"}}` vs `{"user": {"name": "Alice"}}` with `recurse: -1` → **discrepancy**
- `{"user": {"name": "alice"}}` vs `{"user": {"name": "Alice"}}` with `recurse: 0` → **discrepancy** (opaque, objects differ)
- `{"user": {"name": "alice"}}` vs `{"user": {"name": "alice"}}` with `recurse: 0` → **no discrepancy**
- `{"user": {"name": {"first": "alice", "last": "smith"}}}` vs `{"user": {"name": {"first": "alice", "last": "Smith"}}}` with `recurse: 1` → **discrepancy** (drilled to depth 1, found difference at depth 2)
- `{"user": {"name": {"first": "alice", "last": "smith"}}}` vs `{"user": {"name": {"first": "alice", "last": "smith"}}}` with `recurse: 2` → **no discrepancy**

---

## Implementation Phases

### Phase 1: Schema and Configuration
- Extend `Kind` model to support `attribute_schema` JSON field
- Create schema validation and defaults
- Database migrations

### Phase 2: Comparison Logic
- Implement per-field comparison based on schema
- Add logging throughout (all normalization, timezone, recursion decisions)
- Update `_field_discrepancies` in `diff.py` to use schema

### Phase 3: Discrepancy Reporting
- Extend `Discrepancy` model to include applied rule name
- Log all comparison decisions to structured logs
- Update API schemas to expose comparison rules

### Phase 4: Testing (Adversarial Corpus)
- Test all combinations: lists (ordered/unordered), numerics (exact/tolerance), strings (exact/normalized), timestamps (string/semantic), nesting (all depths)
- Determinism tests: identical inputs produce identical discrepancies
- Determinism tests: input order does not affect output order

---

## Example Kind Configuration

```python
# datum/kinds/deployments.py or migrations/seed_deployment_kind.py

{
  "name": "Deployment",
  "tenant_id": GLOBAL_TENANT,
  "attribute_schema": {
    "replicas": {
      "type": "integer",
      "precision": "exact"
    },
    "image": {
      "type": "string",
      "normalize": False  # Case matters for registry URLs
    },
    "labels": {
      "type": "object",
      "normalize": True,
      "recurse": -1
    },
    "ports": {
      "type": "list",
      "ordered": True  # Container startup order matters
    },
    "env": {
      "type": "list",
      "ordered": False  # Environment variables are a set
    },
    "created_at": {
      "type": "timestamp",
      "comparison": "semantic",
      "precision": "second"
    },
    "metadata": {
      "type": "object",
      "recurse": 0  # Opaque, provider's internal state
    }
  }
}
```

---

## Notes

- **Logging is mandatory.** Every comparison decision must be logged with context, so operators can understand discrepancies and tune configurations.
- **Defaults are conservative.** Default to exact comparison (ordered lists, exact strings, full recursion) so nothing is silently normalized away.
- **Determinism as invariant.** Tests must prove identical inputs produce identical discrepancies, and output is sorted consistently.
- **User control.** Operators configure per kind, not globally. Different teams have different tolerance levels.
