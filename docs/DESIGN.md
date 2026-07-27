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

TO WRITE. Document format with a full example. Validation layers (syntax, schema, referential, policy) and which block versus warn. Webhook or polling, and how ingestion stays idempotent when the same commit arrives twice. Projection: incremental or full rebuild. Error reporting good enough to map a failure to a file and line.

## 11. Discovery

TO WRITE. The collector interface and what a collector is forbidden from doing. Scheduling, concurrency limits, overlapping runs. Idempotency and partial failure: a run that reads 800 of 1,000 resources is a partial success with a recorded gap, never a signal that 200 resources vanished. Rate limits and backoff. Normalization. Collectors are the robustness zone.

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

**Open, but not needed until phase 4:**
2. Does a discrepancy attach to a field, a resource, or both?
3. Is the declared plane rebuilt wholesale per revision or diffed incrementally?
4. How much drift history is retained, and is it per resource or per discrepancy?
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
