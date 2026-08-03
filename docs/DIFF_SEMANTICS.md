# WBS 1.5.2: Diff Engine Comparison Semantics

**Status:** Specification (revised 2026-07-30)

**Scope:** Comprehensive decisions on how the diff engine compares field values across declared and discovered planes. Each field definition specifies what the data is; a dedicated comparison configuration specifies how it is compared. These decisions lock in behavior across all reconciliation runs.

---

## Core Principles

### Null / Missing / Empty Semantics

These are distinct and must be compared explicitly:

- **`null`** — The field is explicitly set to null/None
- **`missing`** — The field is absent from the object entirely
- **`empty string`** — The field is a string with zero characters: `""`
- **`empty list`** — The field is an array with no elements: `[]`
- **`empty object`** — The field is an object with no keys: `{}`

Each pair is compared by their full plane state:

| Declared | Discovered | Result | Reason |
|---|---|---|---|
| `missing` | `null` | **discrepancy** | Field absence ≠ explicit null |
| `null` | `null` | **match** | Both sides agree on null |
| `""` | `missing` | **discrepancy** | Empty string ≠ field absence |
| `[]` | `null` | **discrepancy** | Empty list ≠ null |
| `[]` | `[]` | **match** | Both sides agree on empty list |
| `{}` | `{}` | **match** | Both sides agree on empty object |

A field's presence is part of its value. Comparison logic handles nulls and missing values explicitly, never silently treating them as equivalent.

### Object Key Order Independence

Objects are compared **without regard to key order**:

```
{"a": 1, "b": 2} == {"b": 2, "a": 1}  # True, key order ignored
```

When recursing into objects, iteration over keys must be in sorted order for determinism, but the comparison result is order-independent. Objects are never considered different due to key ordering alone.

### Logging Levels

Every comparison decision is logged with a configurable level:

1. **Debug** — All comparisons (noisy, useful during troubleshooting)
2. **Discrepancy** — Only when a discrepancy is found (normal operation)
3. **Sampled Audit** — Every Nth comparison (configurable sampling rate for audit trail)

Each log entry includes:
- Field name and type
- Declared value (raw + transformed if applicable)
- Discovered value (raw + transformed if applicable)
- Comparison rule applied
- Intermediate steps (normalization, type conversion, precision check, etc.)
- Final result (match, discrepancy, or error)

---

## Architecture: Field Definition vs Comparison Config

Each field in `attribute_schema` has two concerns:

```python
{
  "replicas": {
    # FIELD DEFINITION: What is this field?
    "type": "integer",

    # COMPARISON CONFIG: How is it compared?
    "comparison": {
      "mode": "exact",           # exact value vs exact string representation
      "precision": None,         # For numeric: tolerance window (null = exact)
      "logging": "discrepancy"   # debug, discrepancy, sampled
    }
  },

  "env_vars": {
    # FIELD DEFINITION
    "type": "list",

    # COMPARISON CONFIG
    "comparison": {
      "list_mode": "unordered_multiset",  # ordered, unordered_multiset, set
      "element_comparison": "exact",
      "logging": "discrepancy"
    }
  },

  "metadata": {
    # FIELD DEFINITION
    "type": "object",

    # COMPARISON CONFIG
    "comparison": {
      "mode": "opaque",          # opaque, hash, version, identity, recurse
      "logging": "discrepancy"
    }
  }
}
```

---

## 1. Lists: Three Comparison Modes

**Decision:** Three distinct modes (ordered, unordered multiset, set) configured per field.

### List Modes

- **`ordered`** — Exact order required: `[1, 2, 3]` ≠ `[3, 2, 1]`, elements must match position-by-position
- **`unordered_multiset`** (default for list type) — Order ignored, duplicates preserved: `[1, 1, 2]` == `[1, 2, 1]`, but not equal to `[1, 2]`
- **`set`** — Order ignored, duplicates removed before comparison: `[1, 1, 2]` == `[2, 1]` (both become `{1, 2}`)

### Implementation

```python
{
  "ports": {
    "type": "list",
    "comparison": {
      "list_mode": "ordered",  # Container init order matters
      "element_comparison": "exact",
      "logging": "discrepancy"
    }
  },
  "labels": {
    "type": "list",
    "comparison": {
      "list_mode": "set",  # Tags are unique, order irrelevant
      "element_comparison": "exact",
      "logging": "discrepancy"
    }
  },
  "replicas_history": {
    "type": "list",
    "comparison": {
      "list_mode": "unordered_multiset",  # Preserves counts, ignores order
      "element_comparison": "exact",
      "logging": "discrepancy"
    }
  }
}
```

### Test Cases (Adversarial Corpus)

- `[1, 2, 3]` vs `[3, 2, 1]` with `ordered` → **discrepancy**
- `[1, 2, 3]` vs `[3, 2, 1]` with `unordered_multiset` → **no discrepancy**
- `[1, 2, 3]` vs `[3, 2, 1]` with `set` → **no discrepancy**
- `[1, 1, 2]` vs `[1, 2]` with `unordered_multiset` → **discrepancy** (count differs)
- `[1, 1, 2]` vs `[1, 2]` with `set` → **no discrepancy** (both become `{1, 2}`)
- `[1, 1, 2]` vs `[1, 2]` with `ordered` → **discrepancy**

---

## 2. Numeric: Value Precision vs String Representation

**Decision:** Separate exact numeric value comparison from exact string representation.

### Numeric Modes

- **`exact_value`** — Values must match numerically: `3` == `3.0` (both are 3 in numeric terms), but `3` ≠ `3.1`
- **`exact_string`** — String representations must match exactly: `3` ≠ `3.0` (different formats), `"3.00"` ≠ `"3"`
- **`tolerance(N)`** — Within tolerance: `abs(a - b) <= N` → no discrepancy, where N is the threshold

### Implementation

```python
{
  "replicas": {
    "type": "integer",
    "comparison": {
      "mode": "exact_value",  # 3 == 3.0
      "logging": "discrepancy"
    }
  },
  "version_string": {
    "type": "string",
    "comparison": {
      "mode": "exact_string",  # "1.0" ≠ "1.00"
      "logging": "discrepancy"
    }
  },
  "cpu_cores": {
    "type": "float",
    "comparison": {
      "mode": "tolerance(0.01)",  # Within 0.01 is OK
      "logging": "discrepancy"
    }
  },
  "api_version": {
    "type": "integer",
    "comparison": {
      "mode": "exact_string",  # Must be exact string format (for APIs)
      "logging": "discrepancy"
    }
  }
}
```

### Test Cases (Adversarial Corpus)

- `3` vs `3.0` with `exact_value` → **no discrepancy**
- `3` vs `3.0` with `exact_string` → **discrepancy** (different string forms)
- `"3.00"` vs `"3"` with `exact_string` → **discrepancy**
- `3.0` vs `3.01` with `tolerance(0.01)` → **no discrepancy**
- `3.0` vs `3.02` with `tolerance(0.01)` → **discrepancy**

---

## 3. Strings: Normalization with Explicit Definition

**Decision:** Configurable normalization with explicit semantics.

### Normalization Modes

- **`exact`** (default) — No normalization: `"Name"` ≠ `"name"`, `"hello "` ≠ `"hello"`
- **`lowercase`** — Convert to lowercase before comparing: `"Name"` == `"name"`
- **`trim`** — Remove leading/trailing whitespace only: `"  hello  "` == `"hello"`, but `"Name"` ≠ `"name"`
- **`normalize`** — Full normalization: lowercase + trim whitespace + collapse internal whitespace

### Implementation

```python
{
  "name": {
    "type": "string",
    "comparison": {
      "mode": "exact",  # Case-sensitive, whitespace matters
      "logging": "discrepancy"
    }
  },
  "label": {
    "type": "string",
    "comparison": {
      "mode": "lowercase",  # Case-insensitive
      "logging": "discrepancy"
    }
  },
  "description": {
    "type": "string",
    "comparison": {
      "mode": "trim",  # Whitespace trimming only
      "logging": "discrepancy"
    }
  },
  "tag": {
    "type": "string",
    "comparison": {
      "mode": "normalize",  # Full normalization
      "logging": "discrepancy"
    }
  }
}
```

### Logging Example

```
[DIFF] String comparison: field=label, mode=lowercase
  declared: "MyLabel" (normalized to "mylabel")
  discovered: "MYLABEL" (normalized to "mylabel")
  result: NO DISCREPANCY

[DIFF] String comparison: field=name, mode=exact
  declared: "prod-db"
  discovered: "PROD-DB"
  result: DISCREPANCY (case mismatch)
```

### Test Cases (Adversarial Corpus)

- `"Name"` vs `"name"` with `exact` → **discrepancy**
- `"Name"` vs `"name"` with `lowercase` → **no discrepancy**
- `"  hello  "` vs `"hello"` with `trim` → **no discrepancy**
- `"  hello  "` vs `"hello"` with `exact` → **discrepancy**
- `"hello\nworld"` vs `"hello world"` with `normalize` → **no discrepancy** (whitespace collapsed)

---

## 4. Timestamps: Three Timezone Modes

**Decision:** Semantic timestamp comparison with configurable timezone handling.

### Timestamp Modes

- **`string`** (default) — Exact string comparison (timezone-unaware): `"2026-07-30T18:00:00Z"` ≠ `"2026-07-30T10:00:00-08:00"` even if same instant
- **`semantic_utc`** — Parse, normalize to UTC, compare at specified precision
- **`semantic_resource_tz`** — Parse using resource's configured timezone, compare at specified precision

### Precision Levels (for semantic modes)

- `day` — Same calendar day (ignores time and zone)
- `hour` — Same hour in normalized timezone
- `minute` — Same minute in normalized timezone
- `second` (default) — Same second in normalized timezone

### Implementation

```python
{
  "created_at": {
    "type": "timestamp",
    "comparison": {
      "mode": "semantic_utc",
      "precision": "second",  # Same second in UTC
      "logging": "discrepancy"
    }
  },
  "scheduled_backup": {
    "type": "timestamp",
    "comparison": {
      "mode": "semantic_resource_tz",
      "precision": "minute",  # Same minute in resource timezone
      "logging": "discrepancy"
    }
  },
  "api_timestamp": {
    "type": "timestamp",
    "comparison": {
      "mode": "string",  # Exact string format (for API compatibility)
      "logging": "discrepancy"
    }
  }
}
```

### Logging Example

```
[DIFF] Timestamp comparison: field=created_at, mode=semantic_utc, precision=second
  declared: "2026-07-30T18:00:00Z" (parsed as UTC)
  discovered: "2026-07-30T10:00:00-08:00" (parsed as -08:00, converted to UTC → 2026-07-30T18:00:00Z)
  result: NO DISCREPANCY (same second in UTC)

[DIFF] Timestamp comparison: field=created_at, mode=string
  declared: "2026-07-30T18:00:00Z"
  discovered: "2026-07-30T10:00:00-08:00"
  result: DISCREPANCY (string mismatch)
```

### Test Cases (Adversarial Corpus)

- `"2026-07-30T18:00:00Z"` vs `"2026-07-30T18:00:00Z"` with `semantic_utc/second` → **no discrepancy**
- `"2026-07-30T18:00:00Z"` vs `"2026-07-30T10:00:00-08:00"` with `semantic_utc/second` → **no discrepancy** (same instant)
- `"2026-07-30T18:00:00Z"` vs `"2026-07-30T10:00:00-08:00"` with `string` → **discrepancy** (different strings)
- `"2026-07-30T18:30:00Z"` vs `"2026-07-30T10:00:00-08:00"` with `semantic_utc/minute` → **discrepancy** (different minutes)
- `"2026-07-30T18:00:00Z"` vs `"2026-07-30T18:00:30Z"` with `semantic_utc/minute` → **no discrepancy** (same minute)

---

## 5. Objects: Four Comparison Modes

**Decision:** Configurable object comparison from opaque to fully recursive.

### Object Modes

- **`opaque`** — Treat entire object as an opaque blob; compare by structure-preserving hash. No drilling.
- **`version`** — Objects have a version field; compare versions, ignore contents. Useful for provider metadata.
- **`identity`** — Objects have an id field; compare by identity, ignore contents. Useful for cross-references.
- **`ignore`** — Ignore this field entirely; always matches (useful for timestamps or internal state).
- **`recurse(depth)`** — Recursively compare up to specified depth:
  - `recurse(0)` — No recursion (treat as opaque)
  - `recurse(1)` — One level deep
  - `recurse(-1)` — Fully recursive (default)

### Implementation

```python
{
  "metadata": {
    "type": "object",
    "comparison": {
      "mode": "opaque",  # Compare by hash; no drilling
      "logging": "discrepancy"
    }
  },
  "provider_tags": {
    "type": "object",
    "comparison": {
      "mode": "version",  # Compare by version field
      "logging": "discrepancy"
    }
  },
  "owner": {
    "type": "object",
    "comparison": {
      "mode": "identity",  # Compare by id field
      "logging": "discrepancy"
    }
  },
  "internal_state": {
    "type": "object",
    "comparison": {
      "mode": "ignore",  # Always match
      "logging": "debug"
    }
  },
  "spec": {
    "type": "object",
    "comparison": {
      "mode": "recurse(-1)",  # Fully recursive, compare all nested fields
      "logging": "discrepancy"
    }
  },
  "config": {
    "type": "object",
    "comparison": {
      "mode": "recurse(2)",  # Two levels deep
      "logging": "discrepancy"
    }
  }
}
```

### Opaque Comparison Detail

When mode is `opaque`, `version`, `identity`, or `ignore`:

1. **Opaque**: Compute a structure-preserving hash of the entire object (including nested structure), compare hashes
2. **Version**: Compare what each side stated about the `version` key, as strings
3. **Identity**: Compare what each side stated about the `id` key, as strings
4. **Ignore**: Always report as matching, never generate discrepancy

For the two keyed modes, the key may be **absent**, **null**, or **valued**, and
two sides agree only when they made the same statement. Absent on both sides is
agreement, by the same rule as row 4 of the Null / Missing / Empty table: the key
is the whole meaning of the field, so two objects that both omit it have said the
same thing about it. One side absent while the other states a value or a null
remains a discrepancy, as does null against any string.

Presence and nullity are read off the key, never off a rendered string. `null` is
not the string `"None"`, and a value that happens to spell a marker — `<missing>`,
`<null>` — is still a value. A rendering may only ever reach the audit log, never
a comparison (issues #34, #35).

A stated value is compared **as a string**, which is what makes `1` and `"1"` the
same version. A *structured* value — an object or a list — is out of spec here,
because these modes compare a version or id field rather than a document. It is
reachable all the same, so it is rendered through `canonical()` rather than
`str()`: key order never changes a result, at any depth, in any mode, and the two
keyed modes were the last exception to that rule (issue #39). Element order
within a list is preserved, because element order is not key order.

### Test Cases (Adversarial Corpus)

- `{"a": 1, "b": 2}` vs `{"b": 2, "a": 1}` with `opaque` → **no discrepancy** (same structure)
- `{"user": {"name": "alice"}}` vs `{"user": {"name": "Alice"}}` with `recurse(-1)` → **discrepancy**
- `{"user": {"name": "alice"}}` vs `{"user": {"name": "Alice"}}` with `recurse(0)` → **discrepancy** (opaque, objects differ)
- `{"user": {"name": "alice"}}` vs `{"user": {"name": "alice"}}` with `recurse(0)` → **no discrepancy**
- `{"version": "v1"}` vs `{"version": "v1"}` with `version` → **no discrepancy**
- `{"version": "v1"}` vs `{"version": "v2"}` with `version` → **discrepancy**
- `{"version": {"a": 1, "b": 2}}` vs `{"version": {"b": 2, "a": 1}}` with `version` → **no discrepancy** (key order, issue #39)
- `{"version": [1, 2]}` vs `{"version": [2, 1]}` with `version` → **discrepancy** (element order is not key order)
- `{"id": "123"}` vs `{"id": "123", "timestamp": "2026-07-30"}` with `identity` → **no discrepancy** (ID matches)
- `{"internal": "state"}` vs `{"internal": "DIFFERENT"}` with `ignore` → **no discrepancy** (always ignored)

---

## Implementation Phases

### Phase 1: Schema and Configuration
- Extend `Kind` model to support `attribute_schema` JSON field with comparison configs
- Create schema validation and defaults (all comparison values must be explicit, never inferred)
- Database migrations

### Phase 2: Comparison Logic
- Implement per-field comparison based on schema
- Dedicated comparison functions for each type: `compare_list()`, `compare_numeric()`, `compare_string()`, `compare_timestamp()`, `compare_object()`
- All logging at three levels: debug, discrepancy, sampled audit
- Update `_field_discrepancies()` in `diff.py` to use schema and new comparison functions
- Handle null/missing/empty semantics explicitly (never collapse them)

### Phase 3: Discrepancy Reporting
- Extend `Discrepancy` model to include applied rule snapshot (comparison config that decided it)
- Log all comparison decisions to structured logs with full context
- Update API schemas to expose comparison rules and their decisions

### Phase 4: Testing (Adversarial Corpus)
- Test all combinations:
  - Lists: ordered, unordered multiset, set; with duplicates, nulls, empty lists
  - Numerics: exact_value, exact_string, tolerance; with integers, floats, mixed types
  - Strings: exact, lowercase, trim, normalize; with case, whitespace, unicode, empty strings
  - Timestamps: string, semantic_utc, semantic_resource_tz; with different precisions and zones
  - Objects: opaque, version, identity, ignore, recurse(0/1/-1); with key order variations, nested objects
- Null/missing/empty corpus: all five special values across all types
- Determinism tests: identical inputs produce identical discrepancies
- Determinism tests: input order does not affect output order
- Object key order tests: prove key ordering never affects comparison result

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
      "comparison": {
        "mode": "exact_value",  # 3 == 3.0
        "logging": "discrepancy"
      }
    },
    "image": {
      "type": "string",
      "comparison": {
        "mode": "exact",  # Case matters for registry URLs
        "logging": "discrepancy"
      }
    },
    "labels": {
      "type": "list",
      "comparison": {
        "list_mode": "set",  # Tags are unique, order irrelevant
        "element_comparison": "exact",
        "logging": "discrepancy"
      }
    },
    "ports": {
      "type": "list",
      "comparison": {
        "list_mode": "ordered",  # Container startup order matters
        "element_comparison": "exact",
        "logging": "discrepancy"
      }
    },
    "env": {
      "type": "list",
      "comparison": {
        "list_mode": "unordered_multiset",  # Environment variables, order irrelevant, counts matter
        "element_comparison": "exact",
        "logging": "discrepancy"
      }
    },
    "created_at": {
      "type": "timestamp",
      "comparison": {
        "mode": "semantic_utc",
        "precision": "second",
        "logging": "discrepancy"
      }
    },
    "metadata": {
      "type": "object",
      "comparison": {
        "mode": "opaque",  # Treat entire object as blob; compare by hash
        "logging": "discrepancy"
      }
    },
    "spec": {
      "type": "object",
      "comparison": {
        "mode": "recurse(-1)",  # Fully recursive; drill into all nested fields
        "logging": "discrepancy"
      }
    },
    "internal_state": {
      "type": "object",
      "comparison": {
        "mode": "ignore",  # Always match, never discrepancy
        "logging": "debug"
      }
    },
    "cpu_cores": {
      "type": "float",
      "comparison": {
        "mode": "tolerance(0.01)",  # Within 0.01 is OK
        "logging": "discrepancy"
      }
    }
  }
}
```

---

## Null / Missing / Empty Adversarial Corpus

**Critical test cases proving distinct handling:**

| Case | Declared | Discovered | Type | Expected | Reason |
|---|---|---|---|---|---|
| 1 | `null` | `null` | integer | **match** | Both sides agree on null |
| 2 | `missing` | `null` | integer | **discrepancy** | Field absence ≠ explicit null |
| 3 | `null` | `missing` | integer | **discrepancy** | Explicit null ≠ field absence |
| 4 | `missing` | `missing` | integer | **match** | Both sides absent |
| 5 | `""` | `null` | string | **discrepancy** | Empty string ≠ null |
| 6 | `""` | `missing` | string | **discrepancy** | Empty string ≠ field absence |
| 7 | `[]` | `null` | list | **discrepancy** | Empty list ≠ null |
| 8 | `[]` | `[]` | list | **match** | Both sides empty list |
| 9 | `[]` | `missing` | list | **discrepancy** | Empty list ≠ field absence |
| 10 | `{}` | `null` | object | **discrepancy** | Empty object ≠ null |
| 11 | `{}` | `{}` | object | **match** | Both sides empty object |
| 12 | `{}` | `missing` | object | **discrepancy** | Empty object ≠ field absence |
| 13 | `[null, 1]` | `[1]` | list(multiset) | **discrepancy** | Count differs (null counts) |
| 14 | `[null, 1]` | `[1]` | list(set) | **no discrepancy** | Set removes null (?) — **decide** |

**Open question on nulls in lists:** Should null elements be treated as distinct values (count in multiset) or as "absent" (removed by set semantics)? Recommendation: null counts as a value in multisets, removed by set semantics.

---

## Notes

- **Comparison is structural, not semantic.** No field becomes "close enough" unless its rule explicitly allows it. Conservative by default.
- **Null/missing/empty are not equivalent.** Each must be handled distinctly and explicitly. The PlaneValue type carries presence, not just value.
- **Key order never matters for objects.** When recursing into objects, iterate keys in sorted order for determinism, but the comparison is always order-independent.
- **Logging is mandatory.** Every comparison decision must be logged at the configured level, so operators understand discrepancies and can tune configurations.
- **Defaults are conservative.** Default to exact comparison (ordered lists, exact_value numbers, exact strings, string timestamps, full recursion) so nothing is silently normalized away.
- **Determinism as invariant.** Tests must prove:
  1. Identical inputs produce identical discrepancies
  2. Input order does not affect discrepancies
  3. Object key order does not affect discrepancies
  4. Output is sorted consistently
- **User control.** Operators configure per kind, not globally. Different teams have different tolerance levels and field semantics.
