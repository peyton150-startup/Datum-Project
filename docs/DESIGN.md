# Datum: Design Document

**Status:** Skeleton. Sections marked TO WRITE are prompts for the implementer, not placeholders to leave in.
**Companion:** PROJECT_PLAN.md (scope, schedule, quality objectives, and the phase 1 vertical slice). ADRs live in `docs/adr/`. Construction conventions are folded into this document, below.
**Rule:** this document is written before the code it describes, and updated when the code disagrees with it.

---

## 1. Problem and quality objectives

**Fixed.** The problem definition and the per-component quality ranking live in PROJECT_PLAN.md and are not restated here. The one line that governs every decision below: the diff engine optimizes for correctness at the cost of robustness, and the collectors optimize for robustness at the cost of strict correctness. Where this document has to choose, that is the tiebreaker.

## 2. Goals and non-goals

**Fixed.** Goals and exclusions are in PROJECT_PLAN.md. The exclusion that most shapes this design: Datum never writes to the estate. That removes the entire class of concerns around apply ordering, rollback, and blast radius, and it means correctness here is about representation, not execution.

## 3. Program organization

| Module | Single responsibility | Must not |
|---|---|---|
| `kinds` | Define and validate resource kind schemas | Know about planes, matching, or diffs |
| `graph` | Persist and query resources and relationships | Decide what is authoritative |
| `intent` | Turn commits into immutable revisions and project them | Touch the discovered plane |
| `discovery` | Run collectors, record runs, normalize provider shapes | Write to the declared plane, ever |
| `reconcile` | Match, diff, apply precedence, emit discrepancies | Read from providers or Git directly |
| `workflow` | Discrepancy lifecycle, suppression, audit | Recompute diffs |
| `api` | Serve the read model | Contain business rules |

For each module, answer the question that actually matters: **what does it hide?** A module that hides nothing does not deserve to exist as a module. Record the alternative organizations considered (for example, folding `reconcile` into `discovery`) and why they lost.

Fan-out check: no module should depend on more than about seven others. If `reconcile` ends up importing everything, that is the signal the boundary is wrong.

## 4. Core concepts

- **Kind:** a schema-defined resource type. Data, not code.
- **Resource:** an instance of a kind, in one of two planes.
- **Declared plane:** the projection of an intent revision. Origin is Git.
- **Discovered plane:** the projection of a collector run. Origin is the estate.
- **Intent revision:** an immutable snapshot tied to a commit.
- **Collector run:** an immutable snapshot tied to a schedule execution.
- **Match:** a link between a declared and a discovered resource, with confidence and strategy.
- **Discrepancy:** a difference between the planes, with a lifecycle.
- **Precedence policy:** the versioned rule set stating which plane is authoritative for a kind and field.

Each of these should be an abstract data type in the code, named for what it means in the domain rather than for its storage. A discrepancy is a `DiscrepancySet`, not a `list[dict]` passed between functions.

## 5. Data model

**Decided:**
- Kinds are schema-defined data, not a model per kind.
- The declared and discovered planes are **separate tables**, so no query can accidentally blend them. Shape and indexes are duplicated deliberately.
- Attributes are **hot fields in typed columns, the long tail in JSONB** (ADR-005).
- Tenant isolation is **Postgres row-level security**, enforced by the database rather than by an application filter.

**ADR-005, decided:**
- **The rule:** a field earns a typed column only if a query **filters, sorts, joins, or constrains** on it. Otherwise it stays in JSONB.
- **Global core, not per-kind columns.** Every resource of every kind shares one set of typed columns: `tenant_id`, `kind`, `name`, `scope`, `provider_id`, timestamps, and the match and state foreign keys. Kind-specific fields stay in JSONB.
- **Manual promotion, triggered by evidence.** Promoting a field is a deliberate migration.
- **Promote, never duplicate.** Once a field becomes a column it leaves JSONB entirely.

**Still TO WRITE:** how a Kind's schema is stored and validated and what happens to existing resources when it changes; how relationships are modeled when a declared resource references one that does not exist yet; what is soft-deleted vs hard-deleted and how history survives. Include the ER diagram and DDL for core tables. Note every database-level constraint and why it lives there rather than in Python. The one-to-one match constraint from section 12 and the RLS policies both live here.

**Business rules the schema depends on:** a resource belongs to exactly one tenant; a discovered resource matches at most one declared resource; a suppression always expires. Write down where each is enforced.

## 6. Error handling strategy

The system has a **barricade** (ADR-008). Outside it, all data is untrusted. Inside, code may assume its inputs are valid and use assertions rather than checks.

| Zone | What is there | Discipline |
|---|---|---|
| Dirty | Provider API responses, Git document contents, HTTP request bodies | Validate everything. Convert to domain types at the boundary, immediately. |
| Clean | `graph`, `reconcile`, `workflow` | Inputs are already typed and valid. Use assertions for conditions that indicate a bug. |

| Condition | Can it legitimately happen | Response |
|---|---|---|
| Provider returns a resource missing a required field | Yes | Skip and record (collectors favor robustness) |
| Provider times out mid-run | Yes | Partial run recorded with a gap |
| Intent document fails schema validation | Yes | Reject the whole revision, keep the previous one active |
| Two declared resources claim the same identity | Yes | Reject at intent validation |
| Diff engine receives a match whose two sides have different kinds | No | Assert. This is a bug. |
| Precedence policy has no rule covering a field | Yes | **Neither silent nor fatal.** The field yields an undecidable-precedence discrepancy and the run completes. Decided 2026-07-30, §23.6 |

Exceptions crossing a module boundary carry that module's abstraction, not a lower one. No empty except blocks without a comment. Every exception message includes the identifiers needed to find the thing that failed. Offensive in development, graceful in production.

## 7. Resource management

TO WRITE. Database connections under concurrent Celery workers, provider API rate budget, memory during a full-estate diff. State the limits and what happens at each one.

## 8. Change strategy

| Expected to change | Isolation mechanism |
|---|---|
| Resource kinds | Schema-defined, no code change |
| Precedence rules | Table-driven policy, evaluated at runtime, not conditionals |
| Providers | Collector interface, one adapter each |
| Intent document format | Versioned format field, validator per version |
| Matching strategy | Strategy chosen per kind, confidence recorded |

Anything on this list that turns out to be a hard-coded conditional is a defect against this design.

## 9. Buy versus build

- Schema validation: library (Pydantic).
- Git access: library.
- Audit history: library (`django-simple-history`) or hand-rolled.
- Diff: **build.** This is the correctness kernel.
- Rule evaluation: build, small and typed.
- Test data generation: build.

## 10. Intent ingestion

**Decided (2026-07-27), at the opening of phase 2.** Phase 1 shipped a placeholder that parsed raw Kubernetes manifests; this section replaces it.

### The document format

Intent documents speak **Datum's vocabulary, not a provider's**. A declaration of a Deployment and a declaration of an OCI compute instance have the same envelope and differ only in `kind` and the contents of `attributes`. This is the direct consequence of ADR-001: if a kind is data, the document that declares one cannot be shaped by a provider's API.

```yaml
apiVersion: datum.dev/v1
kind: Deployment          # names a Kind row, not a Kubernetes kind
metadata:
  name: web
  scope: default
attributes:
  replicas: 3
```

| Field | Required | Rule |
|---|---|---|
| `apiVersion` | yes | Must be a format version this build knows. Currently only `datum.dev/v1`. This is the versioned format field promised in §8; a second version gets a second validator, never a conditional inside the first. |
| `kind` | yes | Must name an existing `Kind` row. |
| `metadata.name` | yes | Non-empty, at most 253 characters. |
| `metadata.scope` | yes | Non-empty, at most 253 characters. The provider-neutral word for a Kubernetes namespace, an OCI compartment, and whatever the third provider calls it. |
| `attributes` | yes | A mapping, validated against that kind's `attribute_schema`. |
| `provider_id` | **forbidden** | Intent is authored before the resource exists, so it cannot know the provider's identifier (§12). A document carrying one is rejected rather than ignored, because silently dropping it would let an author believe they had pinned an identity. |

One document declares exactly one resource. Multi-document YAML streams are not accepted in phase 2; a file containing more than one document is rejected. Reconsider when a real repo makes the one-file-per-resource rule annoying, not before.

`attribute_schema` is a flat mapping of attribute name to type name, drawn from a closed table: `int`, `str`, `bool`. Evaluation is a lookup, not an if-chain.

**Known limit, deliberate:** every key in a kind's `attribute_schema` is required, and unknown keys are rejected. Optionality is not yet expressible. This is the same simplification §24 records for null-vs-absent in the diff engine, and it is held for the same reason: one kind with one required integer field cannot motivate the design. Both are revisited together when phase 3 adds a second kind — that is the point at which this stops being a simplification and becomes a correctness bug.

### Validation layers

Four layers, evaluated in order. **All of them block.** Phase 2 has no warn tier, because the quality objective for intent ingestion ranks correctness first and explicitly sacrifices convenience: a bad revision is rejected whole and never half-applies.

| Layer | Checks | On failure |
|---|---|---|
| Syntax | The file is parseable YAML and holds exactly one mapping | Reject the revision |
| Envelope | `apiVersion` is known; `kind`, `metadata.name`, `metadata.scope`, `attributes` are present and correctly typed; `provider_id` absent | Reject the revision |
| Schema | `attributes` matches the kind's `attribute_schema` exactly — every declared key present, every value the right type, no unknown keys | Reject the revision |
| Referential | `kind` resolves to a `Kind` row; no two documents in the revision claim the same natural key `(tenant, kind, scope, name)` | Reject the revision |
| Policy | none in phase 2 | — |

The referential layer is where the barricade is actually load-bearing. Two documents claiming one identity is listed in §6 as a condition that legitimately happens and must be *rejected at intent validation*. Phase 1 did not do this — the Postgres unique constraint caught it instead, raising `IntegrityError` across a module boundary and inverting ADR-008. That defect is CF-2, and it is fixed here rather than in a collector, because this is the layer that owns it.

**Validation is whole-revision, not fail-fast.** Every document is validated and every error is collected before anything is raised, so one push surfaces every problem at once instead of one problem per push. `InvalidRevision` carries the full list.

### Trigger and idempotency

Ingestion has **one entry point and may have several triggers**. Phase 2 builds polling; a Git webhook is deferred to an expansion (1.7) and, when it arrives, calls the same function rather than duplicating the path.

A Celery beat task fetches the configured repository on a schedule and ingests `HEAD` if its SHA differs from the active revision's. No inbound route, no shared secret, and nothing to expose through the free-tier host. The cost is bounded staleness: drift between a push and its revision is at most one poll interval.

Idempotency is keyed on `(tenant_id, commit_sha)`, enforced by a unique constraint and checked before any work is done. The same commit arriving twice returns the existing revision and writes nothing. This holds for a re-poll, a retried task, and a replayed webhook alike, because none of them are trusted to be delivered once.

### Projection: full rebuild

**Resolves open question #3.** Each accepted revision writes a complete new set of `declared_resource` rows keyed to that revision. Rows from prior revisions are retained, keyed to theirs. Queries against the declared plane select through the active revision.

Chosen over incremental diffing because incremental projection would itself be a second diff engine — a second thing that must be correct, determinism-tested, and kernel-reviewed — built to save rows the 10,000-resource ceiling does not require saving. It also makes resource identity across revisions load-bearing well before matching (§12) is built to carry it. Full rebuild makes a revision atomic almost for free: one transaction, one flip of `is_active`, no reachable half-state.

The cost is stated plainly: row count grows with revisions rather than with resources. If that becomes real, the fix is retention on inactive revisions, which is open question #4 and is not answered here.

### Error reporting

A rejection names the file and, where the YAML parser gives a position, the line and column. Duplicate-identity errors name **both** conflicting files, since naming one leaves the author guessing. Messages carry the identifiers §6 requires: tenant, kind, and natural key.

## 11. Discovery

**Decided (2026-07-27), at the opening of phase 3.**

Collectors are the **robustness zone**. The quality objectives rank robustness first here and explicitly sacrifice strict correctness: *"A cloud API will return junk, time out, and rate-limit. The collector keeps going and records what it could not read. A partial run is a valid outcome."* Every rule below follows from that sentence, and the phase 1 collector violated it (CF-1), which is the defect this phase exists to fix.

### What a collector is

One adapter per provider, with one job: **read the provider, normalize what it read into `ResourceSnapshot`s, and record what it could not read.** It is the discovery half of the barricade (ADR-008) — provider dicts are converted to domain types at this edge and never travel inward raw.

A collector is **forbidden** from:

- writing to, provisioning, or changing anything in the estate. Read-only toward the estate is permanent and by design.
- writing to the declared plane. `declared_resource` is a projection of a commit; a collector that touches it has destroyed the distinction the whole product rests on.
- deciding what a discrepancy is. Collectors observe; `reconcile` compares. A collector that knows what intent says is coupled to the wrong plane.
- deleting a discovered row it did not observe *this run*. See absence semantics below.
- raising on a single bad record. One malformed record is data, not an exception.

### Normalization

Each collector owns a normalizer that turns one provider record into one `ResourceSnapshot`, or rejects it. Rejection is per record and never aborts the read. The natural key is assembled here, and a snapshot missing any of `(kind, tenant, scope, name)` is structurally invalid — it would match nothing — so it is rejected rather than stored.

`provider_id` is recorded on the discovered plane and only there. Intent cannot carry one (§10, §12).

### Partial failure

**Policy: always persist what parsed.** Every valid record is written, every rejection is counted, and the run is recorded as `PARTIAL`. There is no error ratio above which a run is discarded wholesale.

This is the most literal reading of the stated quality objective, and it is chosen over a threshold because a threshold is a number nobody can defend: any value picked for it would be arbitrary, and the failure it guards against (a provider-side format change making everything unparseable) is better handled by absence semantics below, which already refuse to infer deletion from a damaged run.

`resources_read` counts **items observed**, not items successfully parsed. The run record is the audit trail for what the collector saw; under-reporting it hides the fact that anything was dropped. Phase 1 reported `resources_read = 1` for a three-record payload, which is the second half of CF-1 and is arguably worse than the data loss, because it is silent.

| Field | Meaning |
|---|---|
| `resources_read` | Items the provider returned, valid or not |
| `resources_written` | Snapshots persisted |
| `errors` | Items rejected by normalization |
| `status` | `SUCCESS` when `errors == 0`, else `PARTIAL`; `FAILED` when the provider could not be reached at all |

`resources_read == resources_written + errors` is an invariant, and is tested as one.

### Absence semantics: the rule that prevents mass deletion

A resource missing from a run means one of two things, and the collector cannot tell them apart: it was deleted, or this run did not manage to read it. Conflating them is the risk the WBS names against 1.4.4 — *"a partial read read as mass deletion."*

The rule: **only a `SUCCESS` run may be used to infer absence.** A `PARTIAL` or `FAILED` run never marks anything absent, because by definition it has a gap and cannot distinguish a gap from a deletion.

Absence is recorded, not destructive. Discovered rows carry `last_seen_run`, and a row not observed by a successful run is marked absent rather than deleted, so history survives and `reconcile` can still produce a "declared, missing" discrepancy for it. Deleting the row would destroy the evidence the review queue exists to show.

**Inferring absence requires a `SUCCESS` run. Refuting it does not.** The asymmetry is deliberate. Absence is inferred from silence, and only a complete read makes silence mean anything; but a resource that was actually read is direct evidence that it exists, and that evidence is not weakened by a different record in the same payload being malformed. So a `PARTIAL` run clears the absence flag on everything it observed, while marking nothing new absent. Without the asymmetry a resource could stay flagged missing while being read successfully on every run, for as long as any one unrelated record kept failing.

**Absence is scoped by `(tenant, collector_name)`, which is sound only while a collector owns exactly one kind.** That is true today and is now enforced rather than assumed: a collector declares its kind, and producing a snapshot of any other kind is an assertion failure — a bug in the adapter, not bad provider data. The hole this closes is narrow but severe. A collector owning two kinds whose `fetch` silently returned records for only one of them would report `errors=0`, `has_gap=False`, `SUCCESS`, because nothing in the framework can distinguish "read everything I own" from "read everything I happened to return" — and absence would then be entitled to mark every resource of the other kind absent. That is this section's own mass-deletion failure relocated one level down, from provider to kind. Multi-kind ownership is not built, because nothing yet needs it; see open question 8.

### Idempotency and overlapping runs

A collector run is idempotent on the discovered natural key: running twice against an unchanged estate produces the same rows, not duplicates. This is already enforced by `uq_discovered_natural_key` plus upsert.

**One run per collector per tenant at a time.** A second run starting while one is in flight is skipped, not queued: the later run would read the same estate and the two would race on the same rows to no benefit. Skipping is recorded so a permanently-overlapping schedule is visible rather than silent.

The mechanism for that exclusion is decided in the next subsection, because it is the same decision three other places in the system already need and none of them have made.

**A skip is counted against the run that caused it, not recorded as a run of its own.** `CollectorRun` carries `skipped_attempts`, incremented by whichever tick loses the lock. Resolves open question 7.

The alternative was a fourth `CollectorRunStatus` member, and it is refused for a specific reason rather than a stylistic one. Every existing status means *a read was attempted*, and absence semantics turns on the rule that **only `SUCCESS` may infer absence**. A `SKIPPED` member adds a status that read nothing, which is safe only as long as every consumer spells that rule as `== SUCCESS` and never as `!= FAILED`. That is a trap laid for a future reader, guarding a case that does not need a run row at all. Growing the enum grows the blast radius of the one bug this section exists to prevent.

Counting instead keeps `CollectorRun` meaning exactly one thing — a read was attempted, here is what it saw — and attributes the skip to the run actually responsible. It is also the more informative record: a run with `skipped_attempts: 11` says the read took long enough to block eleven ticks, which is precisely the permanently-overlapping schedule this rule wants visible. A standalone skipped row would say only that something was blocked, without saying by what.

The counter is written by a process other than the one that owns the run, so it is incremented with an atomic database expression rather than read-modify-write. That is the first new case governed by the concurrency rule below, and it is stated here so the rule has a worked example.

### Concurrency and isolation

**Decided (2026-07-29), while building 1.4.1.** This subsection is scoped wider than discovery on purpose: the collector lock above cannot be designed without stating the rule, and once stated the rule turns out to indict two paths in already-merged intent code.

**The assumed isolation level is Postgres's default, READ COMMITTED.** Stating it is the point — every race below is a race *relative to that level*, and a reader who assumes SERIALIZABLE will conclude, wrongly, that the database already prevents them. Datum does not raise the isolation level; see the accepted cost at the end.

**The rule: a concurrency conflict is a domain condition, and must surface as a domain exception.** A conflict that reaches the caller as `IntegrityError` has inverted the barricade exactly as CF-2 did — the boundary passed a condition inward and the database caught it. That the database *does* catch it is not the defect. The defect is that the rejection depends on a constraint firing mid-transaction and arrives wearing a lower module's abstraction, so no caller can be written to expect it. CF-2 was one instance of this. It is a class.

Three instances exist today, all reachable, none currently honouring the rule:

| Where | The race, under READ COMMITTED | What holds integrity | What the caller sees |
|---|---|---|---|
| `intent/ingest.py::ingest_revision` | Check-then-insert on `(tenant, commit_sha)`. Two triggers carrying the same commit both read "no existing revision", both project. | `uq_revision_tenant_commit` | `IntegrityError` |
| `intent/ingest.py::_project` | `UPDATE ... WHERE is_active` then `INSERT is_active=True`. T2's scan cannot see the row T1 has not yet committed, so both may insert an active revision. | `uq_one_active_revision_per_tenant` | `IntegrityError` |
| `discovery/collector.py::_upsert` | `update_or_create` is a read followed by a write, not one statement. Two overlapping runs may both find no row and both insert. | `uq_discovered_natural_key` | `IntegrityError` |

None of these is hypothetical. The first two need only a second Celery beat worker, and become certain when the Git webhook deferred in §10 lands beside the poller — two triggers into one idempotent entry point is precisely the design that invites simultaneous delivery. The third is what the collector lock exists to prevent.

**A race has two ways to lose, and the table above only listed one.** Found in review of the first implementation, and worth stating here because the omission was in the design rather than the code. A conflicting write is rejected either by a **constraint** — `IntegrityError`, SQLSTATE class 23 — or by the **transaction manager**, when the database rolls one side back to break a deadlock (`40P01`) or to preserve serializability (`40001`). Those arrive as different exception types: the second is an `OperationalError` and is invisible to any handler watching for `IntegrityError`.

The rule stated above therefore reads too narrowly. It is not "never as `IntegrityError`" but **never as the database's exception, whichever one it is.** Both losses mean the same thing — another writer was doing this at the same time — so both get the same answer, including the idempotency check that may make the loss a non-event.

What must *not* be swallowed with them is an `OperationalError` that is not a race at all: a dropped connection, an exhausted pool. Answering one of those as a lost race would report a database outage as a successful no-op, which is worse than the defect it was closing. The discrimination is on the SQLSTATE class, not the exception type.

**Decisions:**

- **Exclusion for collector runs is a Postgres advisory lock** keyed on `(tenant_id, collector_name)`, taken for the duration of the run and released by the connection. Chosen over a row lock because there is no natural row to lock — the run being excluded does not exist yet — and over a broker-level lock because the database is already the thing both workers agree on, and adding a second coordination authority means two things that can disagree about who holds the lock.
- **Idempotent insertion states the conflict rather than reading around it.** Where a unique constraint already expresses the invariant, the write attempts and handles the conflict, rather than checking first and hoping the gap is narrow. The check-then-insert shape is not made safe by narrowing its window; it is made safe by not being that shape.
- **Every one of the three converts to a domain exception at its own boundary.** `ingest` raises its own; `collector` counts the loser as a skipped write rather than a rejection, because nothing was malformed.

**Explicitly not done: raising the isolation level to SERIALIZABLE.** It would close all three races generically, and it is refused because it trades a small number of named, locally-fixable races for serialization failures that can surface on *any* transaction and must be retried everywhere. That is a system-wide obligation bought to solve three known problems, and it would also quietly become load-bearing — a later contributor would have no way to know which code depends on it. The named fixes are legible; the isolation level is not.

**Accepted cost.** Every new path that writes must ask this question for itself, because nothing in the type system asks it for you. The mitigation is the rule stated at the top of this subsection plus its line on the review checklist.

### Rate limits and backoff

Provider calls are paginated where the SDK supports it and retried with exponential backoff on 429 and 5xx, up to a bounded number of attempts. Exhausting the attempts ends the run as `PARTIAL` with the gap recorded — never as a success, and never as evidence of deletion.

Credentials are read from the environment, never logged, never returned by the API, and never rendered in the UI. That is a stated non-functional requirement with a test attached.

### Live versus recorded

| Collector | Phase 3 source | Rationale |
|---|---|---|
| Kubernetes | A live local k3s cluster | Free, and the only way to exercise the SDK, real API shapes, and pagination. A fixture-only collector proves the parser works, not the collector. |
| Oracle Cloud | Recorded JSON fixture | Deferred until credentials exist; the normalizer is still fully testable against recorded payloads. |
| CI | Fixtures for both | The build stays hermetic and offline. A test that needs a cluster is not a unit test and does not gate the build. |

Recorded fixtures are multi-record from the start. The single-record fixture is precisely why phase 1 could not surface CF-1, and it is retired here.

## 12. Identity matching

**Decided.**

### The problem
Intent is authored before a resource exists, so it cannot carry the provider's identifier. Reality is identified by machine-assigned IDs. The two planes are identified by different things, permanently. Matching bridges that gap. Every failed match produces two false discrepancies instead of one true one.

### The reframe
**A match is a reconciliation decision, not preprocessing.** It has a strategy, a confidence, a state, and a human who can confirm or reject it. Matches are stored, never recomputed from scratch.

### Strategies, in priority order
| Order | Strategy | Confidence |
|---|---|---|
| 1 | Stored binding from a prior confirmed match | Highest |
| 2 | Provider tag carrying a Datum identifier (`datum/id`) | Highest |
| 3 | Natural key: kind, tenant, scope, name | High, until a rename |
| 4 | Shape similarity | Low. **Deferred to an expansion.** |

**Phase 4 ships strategies 1 and 3.** Strategy 2 is supported by reading the tag when present. Strategy 4 is out of scope for the core build.

> **CF-6, found 2026-07-30 at the opening of Phase 4, closed in WBS 1.5.1.** Strategy 1 was unimplementable in shipped code, for two reasons rather than the one first recorded. `run_reconciliation` deleted every `Match` for the tenant on each run, confirmed ones included, so a stored binding had no input to read. That was the visible half. The second half only appeared on reading the schema: `Match.declared_resource` was a foreign key to `DeclaredResource`, which is **revision-scoped** under full-rebuild projection (§10), so a preserved match pointed at a superseded revision's row and was never found again. **A confirmed match would have survived the run and died at the next intent commit, silently, with no error.** Fixing the delete alone would have shipped a binding that looked durable and was not. The cross-run semantics below are what the fix required.

### Recorded on every match, from the first line of code
`strategy`, `confidence` (high/medium/low), `state`, `confirmed_by`, `confirmed_at`.

### What a match is anchored to, and what survives a run
**Decided 2026-07-30 (WBS 1.5.1).** Four rules, which together answer *what a confirmed match means across runs*.

**1. A match is anchored on domain identity, not on row identity.** The durable anchor is the declared natural key `(kind, tenant, scope, name)` as of the decision, plus the discovered resource's own `provider_id`. Foreign keys to either plane's rows are convenience lookups and carry no meaning: neither plane's rows are durable, and a foreign key to a `DeclaredResource` binds a decision to a *commit* rather than to a resource. This is the same call already made for discrepancy identity in §23.2, for the same reason.

A consequence worth stating plainly: **a discovered resource with no `provider_id` has nothing to anchor to, so it can be matched but never confirmed.** The database refuses the confirmation rather than storing a decision that cannot be found again.

**2. A run rebuilds proposals and never touches human decisions.** `PROPOSED` matches are deleted and recomputed each run, exactly as discrepancies are. `CONFIRMED` and `REJECTED` are human-authored and are read as input, never rewritten. This is the rule CF-6 broke.

**3. A rejection is remembered, and refuses the pairing rather than merely failing to propose it.** A human rejecting a match said *these are not the same resource*. The matcher therefore suppresses that pairing and lets both sides fall out as orphans on their own sides. Re-proposing it every run would make the queue un-clearable, which is the same failure that makes a resolved discrepancy durable.

**4. A vanished anchor invalidates the decision, and re-creation does not revive it.** When either side of a stored decision stops resolving, the record moves to `INVALIDATED` — a terminal state, system-closed, and deliberately distinct from `REJECTED`, because "the resource this was about is gone" is not the same fact as "a human said no". A re-created resource is re-proposed and re-decided by a human. This follows §23's decision that a re-added resource does not inherit its old suppressions, and for the same reason: the anchor tuple is identical on return, so without an explicit terminal state the old decision would silently re-apply to a different instance.

Because terminal rows are history, and several may accumulate for one resource over time, **the one-to-one constraint holds over the active states only** — `PROPOSED` and `CONFIRMED` — as partial unique indexes on each side. A separate partial unique index on the full pairing where `state = rejected` keeps one rejection from being recorded twice.

> **Not yet decided: the strategy 2 tag format.** This section says Phase 4 ships strategies 1 and 3, and also that strategy 2 is "supported by reading the tag when present". Nothing anywhere defines what value `datum/id` carries — a name, a natural key, a Datum-assigned identifier that does not currently exist. WBS 1.5.1 shipped strategies 1 and 3 only, deliberately. Building strategy 2 needs that format decided first, and it is a schema commitment to operators, not a detail.

### Error bias
A wrong match is far worse than a missing one. **When uncertain, do not match.** A low-confidence result becomes its own queue item; it never silently feeds the diff engine.

### Constraints, enforced in Postgres
Matching is one-to-one. Unique constraints on both sides at the database level.

### Adversarial corpus, built before the matcher is written
| Case | Expected |
|---|---|
| Resource renamed, provider ID stable | Matched by binding. One field-level discrepancy on name. |
| Resource recreated, new provider ID, name stable | Matched by natural key. No discrepancy. |
| Two resources swap names | Both matched by binding. Two name discrepancies. Never a crossed match. |
| Resource moves namespace or compartment | Natural key breaks. Two orphans, correctly, until shape matching exists. |
| Same name in two scopes | Two distinct matches. No collision. |
| Declared resource never provisioned | One orphan: declared, not discovered. |
| Two declared resources claim one identity | Rejected at intent validation, not at matching. |

### Cross-run corpus, added with the semantics above
| Case | Expected |
|---|---|
| Run twice, nothing changed | The confirmed match is still there, still `CONFIRMED`, not recreated. |
| Intent commits a new revision, nothing else changed | The confirmed match still binds. This is the case the foreign key broke. |
| Confirmed, then the discovered resource disappears | Match becomes `INVALIDATED`, not deleted. Declared side becomes an orphan. |
| Confirmed, then the resource is re-created with the same identity | A **new** `PROPOSED` match. The `INVALIDATED` row stays as history and does not revive. |
| Rejected, then run again | Not re-proposed. Two orphans, and the rejection is not duplicated. |
| Confirmed binding whose declared resource is renamed in intent | The anchor no longer resolves: `INVALIDATED`, and the new name is proposed afresh. |
| Discovered resource with no `provider_id` | Matchable by natural key, and refused at confirmation by the database. |

## 13. Diff engine

The correctness kernel. Highest review standard, reviewed by the model that did not write it, no bulk-generated code.

- **Determinism as a stated invariant:** identical inputs produce an identical discrepancy set. Canonical ordering for collections, canonical normalization for values. Test it as an invariant.
- Comparison semantics per attribute type: sets versus lists, null versus absent, case and whitespace, numeric precision, timestamps and zones.
- Orphan detection: declared-not-discovered and discovered-not-declared are different discrepancy types.
- **Complexity ceiling:** no routine exceeds cyclomatic complexity 10, enforced in CI. If comparison logic needs more branching, it is a table.

### Null versus absent — WBS 1.5.0, specified then built

Written before the implementation and **delivered 2026-07-30**. This is one of §13's five comparison-semantics questions, answered early because it gates 1.5.3 and 1.5.4.

**Two things the specification got wrong, found by building it.** Both are recorded because a spec that is quietly corrected during implementation teaches nothing the next time.

*The fixtures could not produce the case the contract test needed.* The spec named "extend the fixtures so ingestion yields a declared-absent field" as a deliverable. It cannot be done in this package: §10's all-attributes-required limit means a declared document cannot omit an attribute in its kind's schema, so **declared-absent is unreachable through intent ingestion today** — which the "which layer the guarantee is made at" paragraph below already said, one section away from a deliverable that contradicted it. The wire-contract test builds its rows directly, which is the honest level for an API serialization contract. The end-to-end version becomes writable when §10's limit lifts and belongs with that work.

*The determinism test's own guard was lossy in the way this package exists to fix.* It compared two attribute maps with `==` to check they were the same input. Hypothesis found `[("a", 0), ("a", False)]`: last-wins yields `{"a": False}` one way and `{"a": 0}` the other, and `{"a": 0} == {"a": False}` is `True` in Python while the canonical forms differ. The guard admitted two genuinely different inputs and blamed the engine. **The 0-versus-`False` conflation that motivated `PlaneValue`'s custom equality reappeared inside the test written to check it** — which is the most useful thing this package produced, because it shows the hazard is not confined to the type that was hardened against it.

**The defect being closed.** `reconcile` already *compares* an absent key and an explicit null distinctly, via a sentinel. It then *reports* both as `None`, so a reader of the resulting discrepancy cannot tell "intent does not mention this field" from "intent requires this field empty". The comparison was never wrong; the reporting throws away the answer.

**The rule.** Presence and value are two facts, and a discrepancy carries both, on both planes. Absence is never encoded as a value.

Both directions are enumerated rather than one plus a symmetry note, because the rule's own claim is that direction matters. A table that states asymmetry and then relies on the reader to mirror half of it invites exactly the implementation that handles one side and reuses a bare-`None` helper for the other.

| Declared | Discovered | Outcome |
|---|---|---|
| absent | absent | **Vacuous by construction** — see below. Not reachable through this engine |
| null | null | Not a discrepancy. Both planes state the same thing |
| value | same value | Not a discrepancy |
| value | different value | A discrepancy. Both sides present |
| absent | null | **A discrepancy**, and the report distinguishes which side is which |
| null | absent | **A discrepancy**, distinct from the row above and not its mirror image |
| absent | value | A discrepancy, declared side reported absent, not null |
| null | value | A discrepancy, declared side reported null, not absent |
| value | absent | A discrepancy, discovered side reported absent, not null |
| value | null | A discrepancy, discovered side reported null, not absent |

**The absent/absent row is unreachable and says so.** `_field_discrepancies` iterates the *union* of both sides' attribute keys, so a key present in neither is never visited. The row is listed because its absence from the table would read as an oversight, not because any code can produce it. It becomes reachable only if field enumeration moves from the observed keys to the kind's `attribute_schema`, which is a 1.5.2 question; if that happens, this row acquires a real outcome and needs a real test.

**The representation, which is the part that crosses languages.** `null` is already taken by the JSON value, so presence travels beside the value rather than inside it:

- **Domain:** a frozen `PlaneValue`, constructed only through `PlaneValue.absent()` and `PlaneValue.of(value)`, holding its state in private fields. `FieldDiscrepancy` holds one per plane, **replacing** the bare `declared_value` and `discovered_value` attributes rather than sitting alongside them.

  **There is no public `.value`.** Reaching a value goes through `resolve(on_absent=..., on_present=...)`. The public surface is exactly `absent`, `of`, `resolve`, and the tests assert that as a whitelist rather than blacklisting accessor names somebody guessed. `ruff`'s `SLF` rule is enabled by this package so that `fd.declared._value` — one character longer than the line this design rejects — is a lint failure rather than a convention.

  **What this does and does not guarantee, stated precisely, because the first draft overstated it.** `resolve` does not make the collapse *unavailable*. No total eliminator can: `resolve(on_absent=lambda: None, on_present=lambda v: v)` reproduces the defect exactly, and at the database layer that is the *correct* code, because the check constraint requires the value column to be `NULL` when presence is false. The same shape is right at one call site and the defect at the next.

  What it does guarantee is that **the decision is unavoidably visible at the call site.** A reviewer sees an `on_absent` branch and can judge it; before, there was nothing to see. That is a real property and a weaker one than "unavailable", and the difference matters because CLAUDE.md's boundary test turns on it. The load-bearing protection is this *plus* the destination shapes below — neither alone.

- **The destination shapes carry the other half, because `PlaneValue` cannot reach them.** `api/schemas.py` and `api/router.py` read `declared_value` off the Django *model*, not off a `FieldDiscrepancy`, so no domain-side interface constrains them; `web/src/api.ts` is hand-written and casts with `body.items as Discrepancy[]`, so even a changed payload does not surface. Only `service.py` is protected by the domain type, via `mypy --strict` on `datum.reconcile.*`.

  So the API and TypeScript shapes are part of this package: `DiscrepancyOut` carries a nested `{ present, value }` per plane, `api.ts` matches it, and the review queue renders both states distinctly. **One API contract test asserts that a declared-absent field serializes with `present: false` and a declared-null field with `present: true, value: null`.** That test is the only thing in the package that catches the collapse where a reader actually sees it, and without it the package's cross-language justification would be a claim about code that does not exist.

- **Equality delegates to the shared canonical form; it does not define one.** `PlaneValue.of(0) != PlaneValue.of(False)`, because a report whose equality contradicts the comparison that produced it is worse than the alternative. Equality and hashing are defined over `(present, canonical(value))`.

  **`_canonical` moves from `diff.py` into `domain.py` as part of this package.** `diff.py` already imports `domain.py`, so a `PlaneValue` reaching back into `diff` would be an import cycle on day one; the alternative an implementer reaches for under time pressure is duplicating it, leaving two canonical forms that must agree and will stop agreeing the first time 1.5.2 touches either. Canonicalization is a domain concept and belongs with the type whose identity it defines.

  **What this deliberately does not settle:** deep and numeric comparison semantics. `PlaneValue.of(3)` versus `PlaneValue.of(3.0)` is one of §13's own five open questions, owned by 1.5.2 — JSON has a single number type, and a collector returning `3.0` where intent says `3` becoming a permanent discrepancy is a real risk, not obviously the right answer. 1.5.0 fixes *where* canonicalization lives and that equality delegates to it. 1.5.2 decides what it does. Stated because a package quietly answering a question the plan assigned elsewhere is the failure this phase's spec-first order exists to prevent.

- **`_ABSENT` and `_present()` are removed from `diff.py`.** Once presence is a field on the value, the comparison is `PlaneValue` inequality and the sentinel is a second encoding of absence that has to agree with the first. Two encodings of one fact is the shape of thing that stops agreeing.

- **The storage pair has its own accessor: `as_columns() -> tuple[bool, object | None]`,** defined in `domain.py` where the absent-implies-`NULL` rule is already enforced. The alternative is four `resolve` calls per row in `_write_discrepancies`, two of them with lambdas that discard their argument — worse than the `if` it replaces, and the kind of shape the construction conventions would flag anywhere else. The DB destination gets a barricade instead of hand-written lambdas at the call site; `resolve` stays for semantic reads.

- **Database:** `declared_present` and `discovered_present` boolean columns beside the existing JSONB value columns, with a check constraint holding the value column to `NULL` when its flag is false, so that "meaningless" is not left to convention. **The domain type enforces the same rule at construction** — `PlaneValue.absent()` is the only way to build an absent value, and a non-`None` value with `present=False` is unconstructible. Without that, the domain can hold a state the database rejects, and the conflict surfaces as an `IntegrityError` from inside `run_reconciliation`'s transaction, taking down reconciliation for the whole tenant and wearing the database's abstraction rather than this module's — which §6 forbids.

- **Migration: open rows are deleted, terminal rows survive with undetermined presence.** The first draft of this specification said "delete existing rows" on the grounds that discrepancy records are still disposable. **That was false, and dangerously so.** `_reset` deletes only `state=OPEN`; `resolve_discrepancy` writes `RESOLVED` and `resolved_at` when a human clicks resolve in the review queue, `list_discrepancies` reads them back by state, and `DiscrepancyState` has exactly two members — so resolved rows are the *entire* durable human-authored state in the system. A blanket delete would have erased every record of who reviewed what, silently, with the drift reappearing as fresh open rows on the next beat. It also contradicted §23.4, written the same day, which says open records are never pruned and history is never pruned.

  The rule that survives: **delete `state=OPEN` rows only.** Those are rebuilt by the next reconciliation run, so deleting them invents nothing and backfills nothing, which was the whole of the original argument. It just never applied to terminal rows.

  **Terminal rows keep their values and get `NULL` presence**, meaning "recorded before 1.5.0, never determined". The presence columns are therefore nullable, and the check constraint permits it. `present=true` was refused for the same reason the blanket backfill was: it retroactively asserts "intent stated null" about records where nothing determined that, and doing it only to resolved rows means doing it where nobody will look.

  The cost, stated rather than hidden: **this is a third state, and it reaches TypeScript as `present: boolean | null`.** Legacy rows are a closed set that shrinks to nothing under §23.4's retention policy, so the third state is temporary in fact even though the type is permanent. Worth it to avoid writing a fact that was never true.

- **TypeScript:** `{ present: boolean | null; value: unknown }` in `web/src/api.ts`, matching the API. **Two gates that look like they cover this do not.** `scripts/gen_ts_enums.py` generates enums, and `PlaneValue` is not one, so the CI drift check passes with the shape wrong. And `api.ts` is hand-written with `return body.items as Discrepancy[]` — an unchecked assertion, so a changed payload does not surface at compile time either; if the API dropped `declared_value` today, `String(d.declared_value)` would render the literal string `undefined` with `tsc` green. The protection is the API contract test named above, not either gate. Stated because assuming a gate covers something it does not is this project's own named failure class.

- **Determinism (D5) over the new representation is a deliverable of this package, not a comment.** The existing Hypothesis test in `tests/kernel/test_diff.py` varies input order; its generator is extended here to produce absent and explicitly-null attributes. Named as a deliverable because the first draft left this as a promise in a comment, and comments do not fail.

**Which layer the guarantee is made at.** The domain distinction is total. Whether a *declared document* can express an absent key is a separate question owned by §10's all-attributes-required limit, which currently makes every attribute in a kind's schema mandatory — so today the declared-absent rows are reachable through the domain and through discovery, but not through intent ingestion. §23.8's cluster note groups these two simplifications deliberately; this package resolves one of them and does not touch the other.

**Why not a sentinel inside the JSON.** A reserved object such as `{"__absent__": true}` needs one column instead of two, and fails on the barricade's own terms: provider payloads are untrusted data, and a magic value living inside untrusted data is indistinguishable from a payload that happens to contain it. Presence is metadata about the value and does not belong in the value's own namespace.

**Why not a new discrepancy type.** `discrepancy_type` is part of a discrepancy's durable identity (§23.2). Encoding absence there means a field flipping from absent to null becomes a different record, silently dropping any suppression made against it — the exact failure the identity rule exists to prevent.

**Existing tests are rewritten by this package, not preserved.** `tests/kernel/test_diff.py` currently asserts the collapsed representation positively — one case asserts `(None, True)` and `(3, None)` under the comments *"absent on declared"* and *"absent on discovered"*, and another's docstring states *"both report as None"* as expected behaviour. Those assertions are the defect, written down as a requirement. They are rewritten here, and `declared_value` / `discovered_value` are **removed** from `FieldDiscrepancy`. Said explicitly because the cheap way through a red suite is to keep the bare attributes alongside `PlaneValue`, which satisfies every test in both files and destroys the guarantee above.

**Explicitly out of scope for 1.5.0.** What absence *means* for authority — declared-absent as "intent has no opinion" versus declared-null as "intent requires this empty" — is a precedence question and belongs to 1.5.3. 1.5.0 exists so that 1.5.3 has an unambiguous input, and it stops there. Distinguishing the two values is this package; deciding what follows from the distinction is not.

**WBS 1.5.2 is spec-first, decided 2026-07-30, and the reason is the second bullet above.** "Comparison semantics per attribute type" names five questions — sets versus lists, null versus absent, case and whitespace, numeric precision, timestamps and zones — and answers none of them. This section is a list of things to decide, not a decision.

That is easy to miss because §12 sits next to it and is genuinely finished: matching has its strategy table, its confidence assignment, its error bias, and an adversarial corpus with expected outcomes, all written before its code. The diff engine has no equivalent. §12's corpus is about matching and does not exercise a single comparison rule. **The largest package in phase 4, and the one this document calls the correctness kernel, is the least specified thing in it** — which is precisely the shape of package where a wrong rule gets implemented, confirmed by tests written to match it, and handed over self-consistent.

This section's own corpus, in the sense §12 has one, is part of 1.5.2's specification rather than of its implementation.

## 14. Precedence policy

**Specified before implementation (WBS 1.5.3 is spec-first).** Still TO WRITE: policy shape and granularity, resolution order when several rules match, versioning, and what happens to open discrepancies when the policy changes. Implement as a lookup, not a conditional chain.

**Decided already, and binding on that specification:**

- **Explainability is structural, not procedural.** Given a field, evaluation returns the rule that decided it *as part of the result type*. A log line satisfies the wording of D6 and not the requirement; a return type makes an unexplainable decision unrepresentable. The quality objectives' enforcement column reads "nothing yet" against D6 today, and this is what closes it.
- **The result is a closed pair of cases:** a decision carrying its deciding rule, or an explicit undecidable. There is no third case in which a rule is synthesized to satisfy the type, because a synthesized rule is a silent default wearing the explainability guarantee as a costume.
- **A missing rule does not fail the run** (§23.6). It fails the field, which becomes queue work.
- **Rules are keyed on `(kind, field)` with no tenant dimension**, because kinds are global (§23). If that reopens, this table migrates with `Kind` and the RLS policies, together.

## 15. Discrepancy lifecycle

**Specified before implementation (WBS 1.5.4 is spec-first).** Still TO WRITE: the state machine drawn, with allowed transitions and who may perform each; suppression's required reason, required expiry, and behavior on expiry; what a rediscovery of already-suppressed drift does; immutability of transition history, enforced in the database.

**Decided already, and binding on that specification:**

- **A discrepancy is field-scoped, and its identity across runs is `(tenant, kind, scope, name, discrepancy_type, field_name)`** (§23.2), enforced by two partial unique indexes rather than one whole-table constraint. This is what lets a re-detected drift find its suppressed record instead of duplicating it — the named risk against this package.
- **Terminal states distinguish who closed the record.** Human resolution and system closure are different facts, and a record vacated by an intent revision must not claim a reviewer looked at it (D11).
- **Suppression is scoped to the record, not to the natural key.** A resource deleted from intent and later re-added gets fresh open records; the old suppression stays in history, inert (§23). Under a natural-key identity, resurrection is the default behaviour, so preventing it is a design act rather than an omission.
- **Retention is per discrepancy, age-based, terminal states only.** Open records are never pruned; transition history is never pruned (§23.4).

The current implementation has none of this. `run_reconciliation` deletes and rewrites every open discrepancy each run, `DiscrepancyState` carries two of the five states D7 requires, and `datum/workflow/models.py` is an empty file. The lifecycle is where discrepancy records stop being disposable, and every decision above exists because that transition is one-way.

## 16. API

TO WRITE. Resource design, filtering, pagination, versioning, error format. The schema generation path from django-ninja to the TypeScript client.

## 17. Frontend

TO WRITE. Review queue interaction model including the full keyboard path. How a field-level diff makes the authoritative side unmistakable. State management and data fetching.

## 18. Security and multi-tenancy

TO WRITE. Authentication, roles, where tenant isolation is enforced, and the test proving a cross-tenant read fails. Collector credentials cannot reach logs, the API, or the UI.

## 19. Operations

TO WRITE. Deployment topology, migration strategy, backup and a **tested** restore, logging, and the metrics that reveal the system is unhealthy.

## 20. Test strategy

| Layer | Approach |
|---|---|
| Diff engine, matching, precedence | Property-based tests for the invariants, plus a hand-built adversarial corpus |
| Every routine in the kernel | Basis testing: at minimum one case per decision point, both branches exercised |
| Boundaries | Just below, exactly at, and just above every limit. Plus compound boundaries. |
| Bad data classes | No data, too much data, wrong type, uninitialized. |
| Collectors | Fault injection: timeout, partial page, malformed resource, duplicate ID |
| Boundary interfaces | **Hostile-implementer tests.** Write the adapter that abuses the contract and assert the framework contains it -- one that dies mid-read, one that produces a kind it did not declare, one that returns junk where a record belongs. Named at Phase 3 close: the most valuable single act of that phase's review was attempting to reintroduce CF-1 through a new adapter and finding it impossible, and a review does that once while a test does it on every push. |
| Tenancy | A test that attempts a cross-tenant read and asserts it fails |

Prefer round numbers that can be hand-verified. Write the test before the code where the requirement is unclear. Deliberately write more tests that try to break the code. Aim for branch coverage, not statement coverage.

## 21. Model split for the build

- Correctness kernel (sections 12, 13, 14, 15) plus this document and its ADRs: strongest available model, reviewed line by line against the checklist.
- Bulk work (scaffolding, CRUD, collectors, UI components, fixtures): the cheaper model.
- Every kernel PR is read by the model that did not write it, against the checklist.
- Final audit pass over the whole system before phase 5 closes.
- PRs labeled by authoring model.

## 22. Architecture decision records

See `docs/adr/`. Each ADR: context, options considered, decision, consequences, and the cost of reversing it.

- ADR-001: schema-defined kinds rather than a model per resource type — **written**
- ADR-002: Valkey over Redis
- ADR-003: django-ninja over Django REST Framework
- ADR-004: intent in Git rather than in the database — **written**
- ADR-005: typed-column versus JSONB split
- ADR-006: discrepancy as a first-class entity rather than a changelog
- ADR-007: read-only toward the estate, permanently
- ADR-008: error handling strategy and the barricade boundary — **written**
- ADR-009: T-shaped integration, vertical slice inside phase 1

001, 004, and 008 are written before modeling code because they are inherited by the entire schema and every module boundary.

## 23. Open questions

**Live, blocks phase 1:** None.

**Resolved:**
3. ~~Is the declared plane rebuilt wholesale per revision or diffed incrementally?~~ **Wholesale rebuild**, decided 2026-07-27 at the opening of phase 2. Rationale and accepted cost in §10.
7. ~~How is a skipped run recorded?~~ **Counted against the run that caused it**, as `CollectorRun.skipped_attempts`, decided 2026-07-29 during phase 3. A fourth status member was refused because every existing status means a read was attempted, and adding one that read nothing widens the surface for the `!= FAILED` misreading that would license absence inference from a run which never looked. Rationale in §11.

**Open, answered when a second kind arrives:**
8. How does a collector that owns more than one kind scope absence? Today one collector owns one kind, enforced by assertion, and absence is scoped by `(tenant, collector_name)`. A multi-kind collector needs the `Collector` protocol to declare the set of kinds it is responsible for, and absence scoped by `(tenant, collector_name, kind)` — so a run producing zero records for a kind it owns can still correctly infer absence for that kind, while never touching a kind it does not own. This joins the null-vs-absent simplification (§24) and the all-attributes-required limit (§10) in the cluster of things a second kind forces; they are revisited together, because a second kind is the event that turns each of them from a simplification into a defect.

**Resolved at the opening of phase 4 (2026-07-30), before the code that assumes them:**

These four were deferred as independent questions. They are not independent. Five of the six decisions taken that day are the same question wearing different hats — **when does a discrepancy acquire a durable identity, and what is that identity made of?** Attachment defines it, suppression depends on it, retention bounds it, intent deletion tests it, and re-adding a deleted resource attacks it. They were answered from that rule rather than one at a time, because four locally sensible answers that do not compose is the failure mode available here.

2. ~~Does a discrepancy attach to a field, a resource, or both?~~ **A field**, and the identity that follows is `(tenant, kind, scope, name, discrepancy_type, field_name)`.

   The stakes are not display; the review queue may group by resource either way. The stakes are identity across runs, which is the named risk against 1.5.4 — re-detected drift duplicating suppressed records. Once suppression is durable, re-detection must find the existing record rather than write a second one, and that needs a stable key.

   Take `field_name` out of that key and a record's *content* changes between runs while its *identity* does not. Someone suppresses drift on `replicas`; months later the same resource drifts on `image`, matches the same identity, and is covered by the earlier suppression with the earlier reason attached. The suppression outlives the fact it was made about, and nothing surfaces — the queue is merely quieter than it should be. Field-level identity makes that unrepresentable: a new field is a new record.

   Accepted cost: a fifty-field drift is fifty rows. That is what D8's bulk actions are for; it is a UI problem, not a schema one.

   **Enforced by a partial unique index, not a plain one.** `FIELD` rows carry a `field_name`; orphan rows do not, and Postgres treats NULLs as distinct, so a single unique constraint over the six columns would let duplicate orphans through while appearing to prevent them. Two partial indexes — one `WHERE field_name IS NOT NULL`, one `WHERE field_name IS NULL`. A constraint that looks enforced and is not is this project's own named failure class.

4. ~~How much drift history is retained, and is it per resource or per discrepancy?~~ **Per discrepancy, age-based, terminal states only.** Open records are never pruned. Resolved, vacated, and expired-suppressed records are pruned after a configured age. Transition history is never pruned, because D11 requires history to survive the resource. The declared plane keeps a separate policy, because its growth is per intent revision — bounded by commit rate — while discrepancy growth is per collector run times drifted fields, which is faster by an order of magnitude and multiplied by the field-level decision above.

   Decided now rather than later because nothing currently retains anything: `run_reconciliation` deletes and rewrites every open discrepancy each run, and that accidental bound disappears the moment suppression makes records durable. Retrofitting a policy means deciding what to do with rows already accumulated under none.

5. ~~Is the synthetic estate generator a permanent part of the product or a test fixture?~~ **A fixture, plus a seeding command. No UI, no API surface, no support commitment.**

   The fixture/feature framing was a false binary: D14 requires a public demo seeded with drift, so the generator already has a phase 5 job that is not a test. Answering "fixture" flatly and discovering that dependency in phase 5 is how fixture-grade code gets promoted to production. "Invoked by a management command during demo seeding" and "a user-facing capability with docs and a support story" are different commitments, and only the first is taken.

   **The standing tension, recorded because it will pull:** the risk already named against 1.2.3 is generated drift too tidy to be a real test. Two audiences pull opposite ways — the demo wants drift that reads well, the adversarial corpus wants drift designed to break the matcher. When they conflict, the corpus wins, because the demo has a person looking at it and the corpus does not.

6. ~~Does a missing precedence rule default to intent-wins, or is it a hard error?~~ **Neither exactly: the field yields an undecidable-precedence discrepancy and the run completes.**

   The error-versus-default framing left out the question that decides whether strictness is livable — the *blast radius*. Reconciliation is a batch job over a whole estate in one transaction, so a raise on one uncovered field kills reconciliation for the entire tenant. Phase 3 already answered the analogous question for collectors: one bad record does not take the good ones with it. The same shape applies. Precedence for that field is undecidable, so it surfaces in the queue as work to do, and every other field reconciles.

   This keeps both properties that mattered. Nothing is silently decided, so the result type's guarantee holds — `evaluate` returns either a decision carrying its deciding rule or an explicit `Undecidable`, and there is no third case where a rule is invented to satisfy the type. And the estate still reconciles, so a gap in policy is a queue item rather than an outage.

   A silent intent-wins default was refused on the reversal asymmetry this document already used to defer optionality in §10: loosening later breaks nothing, tightening later breaks every install that leaned on the default — and worse, nothing recorded which fields were deliberate and which were forgotten.

**Also resolved 2026-07-30**, from the project plan's own known-incompleteness note, which scheduled them before phase 4 rather than during it:

- **An intent revision deletes a resource with open discrepancies.** Those records are **system-closed to a state distinct from human resolution**, carrying the system actor and the revision that vacated them. D11 auditability is the reason for the distinct state: nobody reviewed those records, and a record that says `RESOLVED` claims somebody did. The resource then reappears at the next run as `DISCOVERED_UNDECLARED` on its own, which is simply true — it is in the estate and nobody declares it. No cascade is involved; `Discrepancy` denormalizes the natural key and holds no foreign key to either plane, which is what lets history survive the resource.

- **A deleted resource that is later re-added does not inherit its earlier suppressions.** Suppression is scoped to the record, and the record closed at deletion; the re-added resource gets fresh open records. Old suppressions remain in history, inert. This follows directly from the identity rule rather than being a separate policy: a suppression must never outlive the fact it was made about, and a resource can be deleted and re-added with entirely different attributes. Under a natural-key identity, resurrection is the *default* behaviour and has to be prevented deliberately, which is why it is written down rather than left to emerge.

- **Kinds are global only.** `Kind.name` is already globally unique, so this ratifies the status quo rather than changing it, and precedence rules stay keyed on `(kind, field)` with no tenant dimension. Timing is the reason it is answered now: precedence rules are per kind and per field, so tenant-defined kinds would put a tenant dimension in the precedence table — and 1.5.3 is being written now. Deciding after 1.5.3 ships means migrating the precedence model as well as the kind model. **Trigger to reopen:** a tenant needs a kind the project will not ship globally. The reversal then touches `Kind`, the RLS policies, and the precedence table together.

**Open, still not due:**

## 24. How this design could fail

- The schema-defined kind bet does not hold, and half the kinds need custom columns anyway. Early warning: the second kind requires a migration.
- Identity matching is the real project, and the diff engine turns out to be the easy part. Early warning: the adversarial corpus keeps growing after phase 4 starts.
- The precedence policy becomes expressible enough to be Turing-complete and therefore unexplainable. Early warning: a rule needs a rule to explain it.
- The two-plane model is one plane too few, because a resource can be declared, discovered, and also *pending* in a way neither plane captures.

Revisit at the end of phase 1 with real code in hand, and again at closure.

### §24 revisited at Phase 1 close (2026-07-26)

- **Schema-defined kind bet: held so far, but untested.** Nothing in the Phase 1 resource tables is Deployment-specific — `attributes` is JSONB and `Kind.attribute_schema` carries the shape. Adding a second kind should be data plus a fixture with no migration to `declared_resource` / `discovered_resource`. This is not yet evidence: one kind cannot falsify a bet about the second. Confirm when Phase 3 adds one.
- **Matching vs diff difficulty: diff was harder, but not where expected.** The matcher fell out of the natural-key design in one pass and all six corpus cases passed on the first run. The diff engine's own logic also passed first time; the difficulty was in the *determinism invariant*, whose first formulation was itself order-sensitive and failed under Hypothesis. The early warning to watch is therefore sharper than "the corpus keeps growing": it is *the corpus getting harder to state correctly*. Hardest cases so far: absent-key-on-one-side, and the determinism property.
- **Repo/CI foundation: the Ratchet failure did not recur, but only after a real catch.** CI ran on push from Task 3 onward and caught a packaging defect (setuptools flat-layout discovery breaking the editable install once `web/` appeared) that both the local venv and the Docker build missed. CI earning its place this early is the strongest signal in this list.
- **null-vs-absent is a live simplification, not a resolved question.** The Phase 1 diff treats an absent key as a discrepancy carrying value `None`, so a field genuinely declared `null` and a field simply missing are indistinguishable downstream. Deliberate for one kind with one required integer field. Revisit the moment a second kind exposes optional fields — that is the point at which this becomes a correctness bug rather than a simplification.
- **Two-plane model: no strain yet.** Nothing in Phase 1 needed a "pending" state. Untested, since a fixture collector cannot observe an in-flight change.

### §24 revisited at Phase 3 close (2026-07-29)

Two of the four failure modes now have real evidence rather than speculation, and one of the phase's findings is not on the original list at all.

- **Schema-defined kinds: the bet holds, and this is the first evidence rather than an opinion.** The named early warning was *"the second kind requires a migration."* Adding `ComputeInstance` cost a `Kind` row and a fixture; `makemigrations --check` reports no changes to `declared_resource` or `discovered_resource`, no index, and no constraint. Both kinds share the same two tables, distinguished by a foreign key rather than by a schema, and the same name in two kinds is two resources because the natural key carries the kind. One kind could not falsify this claim; two can, and did not. Confidence raised from untested to supported — a third kind is still where a per-kind column would first be *tempting*, since two kinds with two attributes each is a thin test of a data model meant to carry twelve.

- **Matching versus diff difficulty: no new evidence, and none was available.** Neither was touched this phase. The Phase 1 finding stands unamended: the difficulty was in stating the determinism invariant, not in the matcher.

- **Precedence becoming unexplainable: not yet reachable, but now guarded before it starts.** The early warning was *"a rule needs a rule to explain it."* Phase 3 adds a second guard ahead of the work: the evaluator must return the deciding rule as part of its result type, so an unexplainable decision is unrepresentable rather than discouraged. Recorded as a Phase 4 entry condition.

- **The two-plane model being one plane too few: still no strain, and still untested for the same reason.** Nothing this phase needed a "pending" state. Absence semantics came close — a resource marked absent is neither declared-only nor discovered-now — but it fits inside the discovered plane as a flag rather than demanding a third plane. Worth watching: if `is_absent` accumulates companions (`is_pending`, `is_terminating`), that is the two-plane model failing one flag at a time rather than all at once.

**The failure mode that was missing from the list.** §24 asks how the *design* could fail and lists four structural bets. It does not ask how the *method* could fail, and that is what Phase 3 actually caught: **a quality objective can be stated plainly, in a table, from Phase 0 onward, and be violated anyway.** The collectors' robustness objective was stated and CF-1 did the exact opposite of it. Stating an objective is necessary and insufficient; it needs a mechanism, and the mechanism should make the violation unavailable rather than discouraged.

That is now tracked outside this section, as an enforcement column on the quality objectives in PROJECT_PLAN — where two entries read "nothing yet" and one reads "intent only". Those three are this list's real open risks, and they are more concrete than anything the original four could say.

**Revisit again at Phase 4 close**, when precedence and the lifecycle exist and the third and fourth bets become testable for the first time.


---

# Construction Conventions

Decided in writing before code exists. Personal standard: **every line must be readable by someone who did not write it.**

## Tooling, non-negotiable

| Concern | Tool | Enforcement |
|---|---|---|
| Python formatting | `ruff format` | Pre-commit and CI |
| Python linting | `ruff` | CI fails on error |
| Python typing | `mypy --strict` on `reconcile`, `intent`, `graph` | CI fails |
| Complexity | `ruff` C901, max 10 | CI fails |
| TypeScript | `eslint` + `prettier`, `strict: true` | CI fails |
| Coverage | branch coverage, kernel modules gated | CI fails below threshold |

Formatting is never discussed in review.

## Naming

- Names come from the problem domain. `declaredResource`, not `objA`.
- Length scales with scope.
- Never differentiate by number. `total1`/`total2` means the design has not decided what either is.
- Booleans read as a positive true statement: `isSuppressed`, `hasExpired`. Never `notReady`.
- Opposites are consistent project-wide: open/close, add/remove, declared/discovered, suppress/restore.
- One purpose per variable.
- The same word for the same concept everywhere. If the schema says `kind`, nothing says `type`.

## Routines

- A routine exists to reduce complexity, name an abstraction, remove duplication, or hide a sequence.
- Functional cohesion: one operation. A routine whose honest name needs "and" is two routines.
- The name describes everything it does, side effects included.
- Banned verbs: `handle`, `process`, `manage`, `do`.
- Parameters: input, then input-output, then output. At most about seven, consistent order.
- Non-trivial routines are designed in pseudocode first.

## Control flow

- Guard clauses over nesting; validate and return early.
- Nesting past three levels means split, table, or inverted condition.
- Positive conditions.
- Boolean expressions past two operators get named intermediates or a helper.
- Repeated branching on one value is a table, not an if-chain. Applies with force to precedence rules and comparison semantics.
- Loops do one thing.
- No recursion where iteration is clearer.

## Data

- No magic values. Any literal other than 0, 1, or empty is a named constant or config.
- Declare and initialize immediately before first use.
- Keep span and live time short.
- Enumerations, never bare strings, for fixed state sets. Discrepancy state is an enum in Python, the database, and TypeScript, generated from one source.
- Bind late; config over constants where the value varies by deployment.

## Error handling

Full strategy in ADR-008. Rules while writing any line:
- Validate at the barricade; inside it, assert.
- Assertions are for bug conditions and may be compiled out, so they carry no side effects.
- Error handling is for conditions that legitimately occur; it degrades gracefully in production.
- No empty except blocks without a comment explaining why swallowing is correct.
- Exceptions match the abstraction level of the module raising them.
- Exception messages carry the identifiers needed to find the failure: tenant, kind, resource ID, run ID.
- Convert external data to domain types at the boundary, on entry.

## Layout

- Blank lines separate logical paragraphs within a routine.
- Braces and blocks always, even for a single statement.
- Complicated boolean expressions break at the logical operators, aligned so structure is visible.
- Comments explain **why**, never **what**.

## Review checklist

One page, applied to every kernel PR, amended from the project's own defect log.

- [ ] One consistent abstraction, and you can name it?
- [ ] What does it hide? If nothing, why does it exist?
- [ ] Coupling loose, visible, easy to rewire? No semantic coupling to another module's internals?
- [ ] Containment where it would do, rather than inheritance?
- [ ] Fewer than about seven collaborators?
- [ ] Guards on everything crossing the barricade; assertions for the impossible cases
- [ ] No empty except blocks; exceptions at the right abstraction level
- [ ] If it writes: what happens when two of these run at once? A conflict must surface as a domain exception, never as the database's -- and a race loses two ways, by constraint (`IntegrityError`) *and* by transaction rollback (`OperationalError`, SQLSTATE class 40). Watching for only one leaves the hole half-closed (§11, concurrency and isolation)
- [ ] Cyclomatic complexity under 10 in kernel modules
- [ ] Names from the domain; no magic values; one purpose per variable
- [ ] Tests cover both branches of every condition, boundaries above/at/below, and at least one case designed to break it
- [ ] Test data is hand-verifiable
- [ ] Comments say why, and are still true

## Debugging discipline

1. Reproduce reliably before hypothesizing.
2. Shrink to the smallest reproduction where one change flips the outcome.
3. Form a hypothesis accounting for all evidence.
4. Fix the cause. A special case bolted next to the bug means the logic still fails that input generally.
5. Test the fix plus a nearby case that should NOT trigger it.
6. Look for the same mistake elsewhere before moving on.
7. Before a hard session, decide the brute-force fallback and a time limit for the clever approach.

Under deadline pressure, slow down. Over half of first-attempt fixes are wrong.
