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
| Precedence policy has no rule covering a field | Yes | Decide in phase 4 (default intent-wins vs hard error) |

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

### Recorded on every match, from the first line of code
`strategy`, `confidence` (high/medium/low), `state` (proposed/confirmed/rejected), `confirmed_by`, `confirmed_at`.

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

## 13. Diff engine

The correctness kernel. Highest review standard, reviewed by the model that did not write it, no bulk-generated code.

- **Determinism as a stated invariant:** identical inputs produce an identical discrepancy set. Canonical ordering for collections, canonical normalization for values. Test it as an invariant.
- Comparison semantics per attribute type: sets versus lists, null versus absent, case and whitespace, numeric precision, timestamps and zones.
- Orphan detection: declared-not-discovered and discovered-not-declared are different discrepancy types.
- **Complexity ceiling:** no routine exceeds cyclomatic complexity 10, enforced in CI. If comparison logic needs more branching, it is a table.

## 14. Precedence policy

TO WRITE. Policy shape and granularity. Resolution order when several rules match. **Explainability is a hard requirement:** given a field, the engine returns the rule that decided it and why. Versioning, and what happens to open discrepancies when the policy changes. Implement as a lookup, not a conditional chain.

## 15. Discrepancy lifecycle

TO WRITE. The state machine drawn, with allowed transitions and who may perform each. Suppression: required reason, required expiry, behavior on expiry. What a rediscovery of already-suppressed drift does. Immutability of transition history, enforced in the database.

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

**Open, but not needed until phase 4:**
2. Does a discrepancy attach to a field, a resource, or both?
4. How much drift history is retained, and is it per resource or per discrepancy? *(Phase 2 raises the stakes on this: full-rebuild projection grows rows per revision, so retention is now about the declared plane too, not only drift history.)*
5. Is the synthetic estate generator a permanent part of the product or a test fixture?
6. Does a missing precedence rule default to intent-wins, or is it a hard error?

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
- [ ] If it writes: what happens when two of these run at once? A conflict a constraint catches must still surface as a domain exception, never as `IntegrityError` (§11, concurrency and isolation)
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
