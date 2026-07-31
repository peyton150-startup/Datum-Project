# WBS 1.5.4: Discrepancy Lifecycle

**Status:** Specification (revised 2026-07-30)

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

### Decision 2: Linear Human Transitions, Automatic System Reversions
```
OPEN →┬→ RESOLVED (human action; terminal)
      ├→ SUPPRESSED (human action; soft terminal)
      ├→ INVALIDATED (system action; terminal)
      └→ MUTED (system action; terminal)

Automatic reversions (system-driven, not human re-opening):
  SUPPRESSED → OPEN (only if suppression expiry passes and discrepancy still exists)
  RESOLVED → OPEN (only if discrepancy is re-detected in next run)
```

**Clarification:** "No re-opening" means humans cannot manually transition RESOLVED or SUPPRESSED back to OPEN. Only automatic system processes can revert these states. This preserves operator intent while allowing the system to resurface persistent problems.

### Decision 3: Suppression = Expiry + Reason, Decoupled from Retention
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
    suppression_expires_at: DateTimeField(null=True)  # When operator wants to review again
                                                       # null = never auto-reopen, but still subject to retention

    # System-initiated closure
    invalidated_at: DateTimeField(null=True)  # When system invalidated (declared gone)
    invalidated_reason: CharField(null=True)  # e.g., "declared_resource_deleted"

    muted_at: DateTimeField(null=True)  # When system muted (discovered gone)
    muted_reason: CharField(null=True)  # e.g., "discovered_resource_absent"
```

**Suppression Semantics:**
- `suppression_expires_at = NULL`: Operator never wants to revisit this. System will **never auto-reopen** it.
- `suppression_expires_at = TIMESTAMP`: Auto-reopen on that date (if discrepancy still exists).
- **Independent from retention:** Retention policy (90 days) applies to SUPPRESSED records regardless of expiry setting. Rows are deleted after 90 days in terminal state; audit history is kept forever in DiscrepancyTransition.

### Decision 4: Rediscovery = Keep Original Expiry, Track Re-detection
- If suppressed discrepancy is re-detected: update values, keep expiry unchanged, add DiscrepancyTransition record
- Example: suppressed with 7-day expiry (expires 2026-08-30). Re-detected on 2026-08-15 → expiry stays 2026-08-30 (no change)
- Operator sees in history that it re-detected (transition record), but queue doesn't reopen and expiry doesn't slide
- **Rationale:** Prevents indefinite deferral of chronic bugs. Original expiry is hard commitment; rediscoveries just add audit trail.

### Decision 4b: RESOLVED Rediscovery = Revert to OPEN, Track Persistence
- If RESOLVED discrepancy is re-detected in next reconciliation run: transition back to OPEN, add DiscrepancyTransition record
- Example: operator resolved discrepancy (marked as "no action needed"). Next run detects same discrepancy → state returns to OPEN
- Queue shows it again; operator can re-resolve or suppress
- **Rationale:** Ensures operator learns if resolution didn't stick. Prevents silent accumulation of resolved-but-persistent issues.

### Decision 5: Identity Persistence Across Runs (Stateful)
- Same logical discrepancy identity `(tenant, kind, scope, name, discrepancy_type, field_name)` maps to same row across reconciliation runs
- Rows are reused, not created fresh each run
- Unique constraint allows only one row per identity in active states (OPEN, RESOLVED, SUPPRESSED)
- Terminal states (INVALIDATED, MUTED) are excluded from uniqueness, allowing history rows
- **Implementation:** During reconciliation, look up existing row by identity before creating new one; update if found

### Decision 6: System Closure on Next Reconciliation Run
- During `_invalidate_unanchored` (matching phase): detect declared resources that vanished → mark their discrepancies INVALIDATED
- During diff phase: detect discovered resources that vanished (is_absent=True) → mark their discrepancies MUTED
- Operators see the state transition in history

### Decision 7: Full Audit Trail via History Table
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

### Decision 8: Hybrid Permissions
- **Humans:** OPEN → RESOLVED, OPEN → SUPPRESSED (via workflow UI)
- **System:** OPEN → INVALIDATED (on declared deletion), OPEN → MUTED (on discovered absence), RESOLVED → OPEN (on rediscovery)
- Each transition logged with actor

### Decision 9: OPEN Never Pruned, Terminal 90 Days, Suppression Independent from Retention

**Retention Policy (for Discrepancy table):**
- `OPEN` records: kept indefinitely (never deleted unless manually closed)
- `RESOLVED` records: deleted after 90 days in terminal state
- `SUPPRESSED` records: deleted after 90 days in terminal state (regardless of `suppression_expires_at`)
- `INVALIDATED` and `MUTED` records: deleted after 90 days in terminal state

**Suppression Semantics (independent of retention):**
- `suppression_expires_at = NULL`: Never auto-reopen, but still subject to 90-day retention
- `suppression_expires_at = TIMESTAMP`: Auto-reopen on that date if discrepancy still detected, then subject to 90-day retention

**Audit Trail (DiscrepancyTransition):**
- All transition records kept forever (never pruned)
- Provides immutable history even after main Discrepancy row is deleted
- This decouples workflow visibility (suppression expiry) from data retention (90 days)

**Rationale:** Suppression expiry controls operator workflow (when to review), retention controls table hygiene (when to delete old records). These are independent policies. Without DiscrepancyTransition, operator can query history; with it, audit trail persists forever.

---

## State Machine Diagram

```
┌─────────────┐
│   OPEN      │ ← Initial state (detected by diff engine)
└──┬──────┬──┬────────────────────┬─────────────┐
   │      │  │                    │             │
   │      │  │ (human)            │ (system)    │ (system)
   ▼      ▼  ▼                    ▼             ▼
┌──────────────────┐  ┌────────────────────┐  ┌──────────────────┐
│   RESOLVED       │  │   SUPPRESSED       │  │  INVALIDATED     │
│ (acknowledged)   │  │ (expires_at or     │  │ (declared gone)  │
│                  │  │  NULL)             │  │                  │
└────────┬─────────┘  └────────┬───────────┘  └──────────────────┘
         │                     │
    (re-detected)          (expires or re-detected)
         │                     │
         │              ┌──────┴────────┐
         │              │               │
         └─────┬────────┘               ▼
               │                    [Keep expiry,
               │                     track in history]
               │
               ▼
          [Revert to OPEN]

Parallel (system-initiated, terminal):

OPEN ──────────────→ MUTED
(discovered absent)  (resource gone)

**Key:** Human transitions are deterministic and explicit. System transitions (rediscovery) are automatic. Terminal states are immutable; only DiscrepancyTransition records grow.
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

### Scenario 3: Rediscovery Before Expiry (SUPPRESSED)
- Discrepancy SUPPRESSED, expires 2026-08-30
- Next reconciliation run on 2026-08-15: same discrepancy re-detected
- Expected: values updated, suppression_expires_at stays 2026-08-30 (unchanged), DiscrepancyTransition records "re-detected on 2026-08-15"

### Scenario 4: Suppression Expires, No Rediscovery
- Discrepancy SUPPRESSED with expires_at=2026-08-30, created 2026-07-30
- On 2026-09-30, CI cleanup job runs (90 days after state change)
- Expected: deleted from Discrepancy table; DiscrepancyTransition records remain

### Scenario 5: Suppression with NULL expires_at (Never Reopen)
- Discrepancy SUPPRESSED with expires_at=NULL, created 2026-07-30
- Next run: same discrepancy re-detected
- Expected: values updated, state stays SUPPRESSED, never auto-reopens
- On 2026-09-30, CI cleanup job deletes record after 90 days (retention independent of expiry)

### Scenario 6: RESOLVED Rediscovery
- Discrepancy RESOLVED (alice marked "no action needed"), created 2026-07-30
- Next reconciliation run detects same discrepancy still exists
- Expected: state → OPEN, DiscrepancyTransition records "re-detected on 2026-08-01", queue shows it again
- Operator can re-resolve or suppress it this time

### Scenario 7: Declared Resource Deleted
- Discrepancy exists on `Deployment.cpu`
- Intent commit deletes the Deployment
- Next reconciliation run
- Expected: during matching phase, resource vanishes, discrepancy → INVALIDATED, reason="declared_resource_deleted"

### Scenario 8: Discovered Resource Vanishes
- Discrepancy exists on `Deployment.labels`
- Collector detects resource gone (is_absent=True)
- Next reconciliation run
- Expected: during diff phase, no discovered snapshot, discrepancy → MUTED, reason="discovered_resource_absent"

### Scenario 9: Transition History Immutable
- Discrepancy: OPEN → RESOLVED (alice, 2026-07-30) → OPEN (system re-detected, 2026-08-01) → SUPPRESSED (bob, 2026-08-01)
- On 2026-10-01, SUPPRESSED record deleted (90+ days in terminal state)
- Later query to DiscrepancyTransition table
- Expected: all transitions still there, showing full history even though main row is deleted

### Scenario 10: Stateful Row Identity Across Runs
- Run 1: Field `replicas` differs → Discrepancy created (id=D1)
- Run 2: Same field, same resource → Lookup by identity finds D1, update with new values
- Run 3: Field matches → Same D1 row still exists but values updated to "match"
- Expected: same row (id=D1) reused; not creating new rows per run

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

- **Rediscovery logic:** During reconciliation, look up existing discrepancy by identity `(tenant, kind, scope, name, discrepancy_type, field_name)` before creating new one. If found:
  - RESOLVED → revert to OPEN, create transition record
  - SUPPRESSED → update values, keep expiry unchanged, create transition record
  - Otherwise create new OPEN record

- **Expiry checking:** During reconciliation, if SUPPRESSED record has `expires_at < now`:
  - If discrepancy still detected: revert to OPEN
  - If discrepancy not detected: stay SUPPRESSED (will be deleted by retention job)

- **Retention job** (`datum/workflow/tasks.py`): daily purge of terminal records older than 90 days:
  ```python
  for state in [RESOLVED, SUPPRESSED, INVALIDATED, MUTED]:
      Discrepancy.objects.filter(
          state=state,
          created_at__lt=now - 90 days
      ).delete()
  # DiscrepancyTransition records NEVER deleted
  ```

- **Workflow UI:** needs RESOLVED and SUPPRESSED buttons with reason fields; suppression form asks for expiry date (with "never" option for NULL)

- **History immutability:** Delete operations only on main Discrepancy table after retention period; Transition records kept forever for audit trail

---

## Architectural Decisions Resolved

### Concern 1: "Linear transitions" vs "auto-revert on expiry" ✅
- **Resolution:** Clarified distinction between human re-opening (forbidden) and automatic system reversion (allowed)
- **Implementation:** Only system can revert RESOLVED→OPEN or SUPPRESSED→OPEN via rediscovery logic

### Concern 2: Rediscovery extends expiry indefinitely ✅
- **Resolution:** Chose Option C (keep original expiry)
- **Rationale:** Prevents eternal deferral. Rediscovery updates values but expiry date is hard deadline set by operator
- **Audit:** DiscrepancyTransition records track every rediscovery, so operator can see pattern

### Concern 3: INVALIDATED/MUTED consolidation ✅
- **Resolution:** Keep separate states
- **Rationale:** Mirrors Match model pattern; provides semantic clarity in state name

### Concern 4: RESOLVED rediscovery undefined ✅
- **Resolution:** Chose Option C (revert to OPEN)
- **Rationale:** Ensures operator learns if resolution didn't stick; prevents silent accumulation of unresolved drift

### Concern 5: Identity persistence model ✅
- **Resolution:** Chose Option B (stateful, reuse rows across runs)
- **Rationale:** Matches current code intent (rows represent logical discrepancies, not run artifacts)
- **Implementation:** Look up by identity before creating; update if found

### Concern 6: Unique constraint allows resurrection ✅
- **Resolution:** Intentional per DESIGN 12
- **Rationale:** INVALIDATED/MUTED excluded from uniqueness; same identity can be OPEN again after terminal state
- **Pattern:** New OPEN record created, not resurrection of old row

### Concern 7: Retention vs suppression independence ✅
- **Resolution:** Chose Option B revised (NULL = never reopen, independent from retention)
- **Rationale:** Decouples workflow visibility (expiry) from data hygiene (90-day retention)
- **Benefit:** Solves "permanent suppression living forever" problem without coupling concerns

### Concern 8: Mutable row vs immutable history ✅
- **Resolution:** Explicit in schema and implementation notes
- **Clarification:** Discrepancy row is mutable (values, states update); DiscrepancyTransition is immutable (audit trail)

### Concern 9: MUTED naming clarity ✅
- **Resolution:** Keep MUTED, document meaning
- **Rationale:** Mirrors Match.INVALIDATED pattern; consistency across boundaries
- **Documentation:** Comments explain "discovered resource disappeared, nothing to reconcile"
