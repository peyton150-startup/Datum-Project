# WBS 1.5.4: Discrepancy Lifecycle

**Status:** Specification (to be decided)

**Scope:** The state machine for discrepancies: transitions, who may perform each, suppression semantics, rediscovery, and retention.

---

## Core Principles (Already Decided)

From DESIGN §15:

- **Identity is `(tenant, kind, scope, name, discrepancy_type, field_name)`** — Enforced by partial unique indexes, not one whole-table constraint. Re-detected drift finds its suppressed record instead of duplicating.
- **Terminal states distinguish actor.** Human resolution and system closure are different facts. A record vacated by intent revision must not claim a reviewer looked at it.
- **Suppression is field-scoped, not resource-scoped.** A resource deleted from intent and re-added gets fresh OPEN records; old suppression stays inert.
- **Retention is per-discrepancy, age-based, terminal states only.** OPEN records never pruned. Transition history never pruned.

---

## Questions to Answer

### 1. State Machine: What Are the Five States?

**Current:** Two states (OPEN, RESOLVED). Needed: Five states distinguishing actor and finality.

**Proposal:**

```
OPEN (initial)
├─→ RESOLVED (human closes it)
├─→ SUPPRESSED (human suppresses with expiry)
├─→ INVALIDATED (system closes it: declared resource deleted)
└─→ MUTED (system ignores it: discovered resource vanished)
```

| State | Actor | Meaning | Terminal | Reversible |
|---|---|---|---|---|
| **OPEN** | System | Discrepancy detected, awaiting review | No | — |
| **RESOLVED** | Human | Operator acknowledged, no action needed | Yes | No |
| **SUPPRESSED** | Human | Operator acknowledged, ignore until expiry date | Soft | Yes (if re-discovered before expiry) |
| **INVALIDATED** | System | Declared resource deleted, decision moot | Yes | No |
| **MUTED** | System | Discovered resource vanished, nothing to reconcile | Yes | No |

---

### 2. Transitions: What's Allowed from Each State?

**Question:** Can an OPEN discrepancy go directly to SUPPRESSED? Or must it go OPEN → RESOLVED first?

**Options:**

**A) Linear path** — `OPEN → (RESOLVED | SUPPRESSED | INVALIDATED | MUTED)`, no re-opening
- Simplest state machine
- Risk: operator makes a mistake, can't undo

**B) Flexible with undo** — `OPEN ↔ RESOLVED`, `RESOLVED ↔ SUPPRESSED`, terminal states one-way
- Operator can change their mind
- Complexity: tracking undo history

**C) Reopen with re-evaluation** — Terminal states stay terminal, but OPEN can resurrect if same discrepancy re-detected
- Example: INVALIDATED (declared resource gone), then declared resource re-created → new OPEN record
- Requires identity-based resurrection logic

---

### 3. Suppression Semantics: What Does "Suppression" Mean?

**Options:**

**A) Boolean flag + expiry date**
- `Discrepancy.suppressed_at` (timestamp)
- `Discrepancy.suppression_expires_at` (timestamp or null for permanent)
- If expiry is null: permanent suppression
- If expiry passed: can be re-opened

**B) Suppression level + reason**
- `Discrepancy.suppression_level` (enum: ACKNOWLEDGED, SUPPRESSED, ARCHIVED)
- ACKNOWLEDGED: operator saw it, left OPEN for next run
- SUPPRESSED: ignore for N days
- ARCHIVED: ignore forever
- Each has a `reason` text field (required for audit)

**C) Suppression plan**
- `Discrepancy.suppression_plan` (JSON) with:
  - `reason`: why
  - `expires_at`: when it auto-unsuppresses
  - `escalate_if_unchanged_for`: auto-reopen if still wrong after X days
  - `notify_on_expiry`: who to alert when it expires

---

### 4. Rediscovery: What If a Suppressed Discrepancy Is Detected Again?

**Scenario:** Field `replicas` is suppressed until 2026-08-30. On 2026-08-15, a new reconciliation run detects the same discrepancy again.

**Options:**

**A) Extend suppression** — Update `suppression_expires_at` to 2026-08-15 + suppression_duration
- Discrepancy keeps same identity
- Suppression window slides forward
- Operator doesn't see it reappear

**B) Alert operator** — Unsuppress and mark as OPEN, notify operator "drift re-detected"
- Operator aware that the drift persists
- Queue item reappears
- Risk: notification fatigue if drift is chronic

**C) Silent re-close** — Auto-update to current state but stay suppressed with original expiry
- Re-detected drift updates the values but suppression doesn't change
- Operator reviews discrepancy history to see "detected again on 2026-08-15"

---

### 5. System-Initiated Closure: When Does the System Invalidate?

**Scenario:** A discrepancy exists on field `Deployment.cpu`. Then the Deployment is deleted from intent. What happens?

**Options:**

**A) Immediate system closure** — On intent commit that deletes the resource, all its discrepancies become INVALIDATED immediately
- Clean: resource gone, discrepancies irrelevant
- Risk: operator was mid-review when it disappeared

**B) On next reconciliation run** — System detects missing declared resource during diff, transitions discrepancy to INVALIDATED
- Operator has time to finish review
- Risk: stale queue items temporarily

**C) Never close automatically** — Operator must close it
- Explicit: nothing vanishes without human action
- Risky: queue accumulates phantom records

---

### 6. Audit Trail: What Gets Recorded?

**Options:**

**A) Snapshot approach** — Each state transition creates a `DiscrepancyHistory` record
- `DiscrepancyHistory(discrepancy_id, from_state, to_state, actor, reason, timestamp)`
- Full trail of all transitions
- Query history to see "opened, then suppressed, then re-opened"

**B) Event log** — Separate `DiscrepancyEvent` table
- `DiscrepancyEvent(discrepancy_id, event_type, actor, reason, timestamp)`
- Events: OPENED, RESOLVED, SUPPRESSED, UNSUPPRESSED, INVALIDATED, etc.
- Richer semantics per event

**C) Immutable current + last-known-good** — Keep `Discrepancy` as-is, add `last_resolved_at`, `last_suppressed_at`, etc.
- Compact: no separate table
- Limited: only most recent action visible

---

### 7. Who Can Perform Each Transition?

**Question:** Can a system user suppress a discrepancy? Or only the reconciliation system?

**Options:**

**A) Humans only (workflow)**
- OPEN → RESOLVED: any human reviewer
- OPEN → SUPPRESSED: any human reviewer
- Requires: `Discrepancy.resolved_by`, `Discrepancy.suppressed_by` (username or user_id)

**B) System only**
- OPEN → INVALIDATED: reconciliation system (on intent change)
- OPEN → MUTED: reconciliation system (on discovery absence)
- Requires: detect these conditions during reconciliation

**C) Hybrid**
- Humans: OPEN ↔ RESOLVED, OPEN → SUPPRESSED
- System: OPEN → INVALIDATED, OPEN → MUTED
- Requires: tracking both `resolved_by` and `system_closed_reason`

---

### 8. Retention Policy: How Long Do We Keep Discrepancies?

**Options:**

**A) OPEN: never prune. Terminal: age-based**
- `OPEN` records kept indefinitely (unless manually closed)
- `RESOLVED`, `SUPPRESSED`, `INVALIDATED`, `MUTED`: delete after N days (e.g., 90 days)
- History records: never deleted

**B) Never prune, but archive**
- Move terminal records to `DiscrepancyArchive` table after N days
- Still queryable but not in main table
- Reduces main table size without losing data

**C) Tenant-configurable retention**
- Each tenant sets their own retention per state
- Default: OPEN never prune, terminal 90 days
- Tenants can extend or shorten

---

## Proposal: Balanced Decisions

### Decision 1: Five States
```python
class DiscrepancyState(TextChoices):
    OPEN = "open"              # Awaiting review
    RESOLVED = "resolved"      # Human acknowledged, no action
    SUPPRESSED = "suppressed"  # Human suppressed until expiry
    INVALIDATED = "invalidated"  # System: declared resource gone
    MUTED = "muted"            # System: discovered resource gone (no action possible)
```

### Decision 2: Linear Transitions (No Re-opening)
```
OPEN →┬→ RESOLVED
      ├→ SUPPRESSED (with expiry date)
      ├→ INVALIDATED (system-initiated)
      └→ MUTED (system-initiated)

Suppressed can auto-revert to OPEN if suppression expires and discrepancy re-detected.
```

### Decision 3: Suppression = Expiry + Reason
```python
class Discrepancy(models.Model):
    # ... existing fields ...
    state: CharField  # OPEN, RESOLVED, SUPPRESSED, INVALIDATED, MUTED
    resolved_by: CharField(null=True)  # Username who resolved it
    resolved_at: DateTimeField(null=True)  # When human closed it

    # Suppression-specific
    suppressed_by: CharField(null=True)  # Username who suppressed it
    suppressed_at: DateTimeField(null=True)  # When suppression started
    suppression_reason: TextField(null=True)  # Why suppressed (required if state=SUPPRESSED)
    suppression_expires_at: DateTimeField(null=True)  # null = permanent

    # System-initiated closure
    invalidated_at: DateTimeField(null=True)  # When system invalidated (declared gone)
    invalidated_reason: CharField(null=True)  # e.g., "declared_resource_deleted"

    muted_at: DateTimeField(null=True)  # When system muted (discovered gone)
    muted_reason: CharField(null=True)  # e.g., "discovered_resource_absent"
```

### Decision 4: Rediscovery = Extend Suppression (Silent)
- If suppressed discrepancy is re-detected before expiry: update values, keep suppressed, extend expiry by original duration
- Example: suppressed 1 day ago with 7-day expiry (6 days remaining). Re-detected → expiry becomes +7 days from now
- Operator sees in history that it re-detected, but queue doesn't reopen

### Decision 5: System Closure on Next Reconciliation Run
- During `_invalidate_unanchored` (matching phase): detect declared resources that vanished → mark their discrepancies INVALIDATED
- During diff phase: detect discovered resources that vanished (is_absent=True) → mark their discrepancies MUTED
- Operators see the state transition in history

### Decision 6: Full Audit Trail via History Table
```python
class DiscrepancyTransition(models.Model):
    discrepancy: ForeignKey(Discrepancy)
    from_state: CharField  # Previous state
    to_state: CharField  # New state
    actor: CharField  # Username or "system"
    reason: TextField(blank=True)  # Why (required for RESOLVED, SUPPRESSED)
    timestamp: DateTimeField(auto_now_add=True)

    # Metadata
    suppression_expires_at: DateTimeField(null=True)  # If transitioning to SUPPRESSED
    values_at_transition: JSONField  # Snapshot of declared/discovered values
```

### Decision 7: Hybrid Permissions
- **Humans:** OPEN → RESOLVED, OPEN → SUPPRESSED (via workflow UI)
- **System:** OPEN → INVALIDATED (on declared deletion), OPEN → MUTED (on discovered absence)
- Each transition logged with actor

### Decision 8: OPEN Never Pruned, Terminal 90 Days
- CI job runs daily:
  - Delete `RESOLVED` and `SUPPRESSED` (expires_at passed) older than 90 days
  - Keep `INVALIDATED` and `MUTED` for 90 days (audit trail)
  - Keep all `DiscrepancyTransition` records forever (immutable history)
- Tenants cannot override retention (simplifies admin)

---

## State Machine Diagram

```
┌─────────────┐
│   OPEN      │ ← Initial state (detected by diff engine)
└──┬──────┬──┬┘
   │      │  │
   │      │  └──────────────────────┐
   │      │                         │
   ▼      ▼                         ▼
┌─────────────────┐   ┌──────────────────────┐
│   RESOLVED      │   │   SUPPRESSED         │
│ (acknowledged)  │   │ (expires_at = null   │
└─────────────────┘   │  OR timestamp)       │
                      └────────┬─────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
         (expires_at passes)   (re-detected)
                    │                     │
                    ▼                     ▼
            [Revert to OPEN]      [Extend expiry]
                                (stay SUPPRESSED)

Parallel (system-initiated):

OPEN ──────────────→ INVALIDATED
(declared deleted)   (immutable history)

OPEN ──────────────→ MUTED
(discovered absent)  (immutable history)
```

---

## Adversarial Corpus (Test Cases)

### Scenario 1: Human Resolves
- Discrepancy OPEN on `Deployment.replicas`
- Human clicks "Resolved" in workflow
- Expected: state → RESOLVED, resolved_by="alice", resolved_at=now, transition logged

### Scenario 2: Human Suppresses with Expiry
- Discrepancy OPEN on `Deployment.image`
- Human clicks "Suppress for 7 days" with reason "known drift, team working on it"
- Expected: state → SUPPRESSED, suppression_expires_at=now+7d, reason stored, transition logged

### Scenario 3: Rediscovery Before Expiry
- Discrepancy SUPPRESSED, expires in 3 days
- Next reconciliation run: same discrepancy re-detected
- Expected: values updated, suppression_expires_at extended to now+7d (original duration), state stays SUPPRESSED

### Scenario 4: Suppression Expires, No Rediscovery
- Discrepancy SUPPRESSED, expires today
- No new reconciliation run
- CI cleanup job runs tomorrow
- Expected: deleted (90+ days after SUPPRESSED)

### Scenario 5: Declared Resource Deleted
- Discrepancy exists on `Deployment.cpu`
- Intent commit deletes the Deployment
- Next reconciliation run
- Expected: during matching phase, resource vanishes, discrepancy → INVALIDATED, reason="declared_resource_deleted"

### Scenario 6: Discovered Resource Vanishes
- Discrepancy exists on `Deployment.labels`
- Collector detects resource gone (is_absent=True)
- Next reconciliation run
- Expected: during diff phase, no discovered snapshot, discrepancy → MUTED, reason="discovered_resource_absent"

### Scenario 7: Transition History Immutable
- Discrepancy: OPEN → RESOLVED (alice) → [RESOLVED deleted after 90d]
- Later query to DiscrepancyTransition table
- Expected: transitions still there, showing full history

### Scenario 8: Re-added Resource Gets Fresh OPEN
- Resource deleted from intent, discrepancies → INVALIDATED
- Resource re-added to intent
- Next reconciliation run detects discrepancy
- Expected: new OPEN record (different identity per run? or resurrect INVALIDATED?)

---

## Schema Updates

```python
# Update existing Discrepancy model:
class Discrepancy(models.Model):
    # Existing identity fields
    tenant_id: UUID
    discrepancy_type: CharField
    kind_name: CharField
    scope: CharField
    name: CharField
    field_name: CharField(null=True)

    # Existing value fields
    declared_present: BooleanField(null=True)
    declared_value: JSONField(null=True)
    discovered_present: BooleanField(null=True)
    discovered_value: JSONField(null=True)
    authoritative_plane: CharField

    # New state machine
    state: CharField(choices=DiscrepancyState, default=OPEN)
    created_at: DateTimeField(auto_now_add=True)

    # Human-initiated transitions
    resolved_by: CharField(null=True, blank=True)
    resolved_at: DateTimeField(null=True, blank=True)

    suppressed_by: CharField(null=True, blank=True)
    suppressed_at: DateTimeField(null=True, blank=True)
    suppression_reason: TextField(null=True, blank=True)
    suppression_expires_at: DateTimeField(null=True, blank=True)

    # System-initiated transitions
    invalidated_at: DateTimeField(null=True, blank=True)
    invalidated_reason: CharField(null=True, blank=True)

    muted_at: DateTimeField(null=True, blank=True)
    muted_reason: CharField(null=True, blank=True)

    class Meta:
        # Partial unique indexes
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "kind_name", "scope", "name", "discrepancy_type", "field_name"],
                condition=models.Q(state__in=[OPEN, RESOLVED, SUPPRESSED]),
                name="uq_identity_active_states"
            ),
        ]

# New audit table:
class DiscrepancyTransition(models.Model):
    discrepancy: ForeignKey(Discrepancy)
    from_state: CharField
    to_state: CharField
    actor: CharField  # "system" or username
    reason: TextField(blank=True)
    timestamp: DateTimeField(auto_now_add=True)

    # Metadata for specific transitions
    suppression_expires_at: DateTimeField(null=True)
    values_snapshot: JSONField  # {declared_value, discovered_value} at time of transition

    class Meta:
        index_together = [["discrepancy", "timestamp"]]
        # Never auto-delete; retention policy manages purge
```

---

## Implementation Notes

- Suppression expiry is checked during reconciliation: if SUPPRESSED record exists and expires_at < now, it can transition back to OPEN
- Cleanup job (`datum/workflow/tasks.py`): daily purge of terminal records older than 90 days
- Workflow UI: needs RESOLVED and SUPPRESSED buttons with reason fields
- History immutability: delete operations only on main Discrepancy table after retention period; Transition records never deleted
