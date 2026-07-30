# Datum: Project Plan

**Project title:** *Build Datum, a self-hosted source of truth for a cloud and Kubernetes estate that holds declared intent in Git, ingests discovered state on a schedule, and drives every difference between the two through an auditable reconciliation workflow, deployed publicly on free and open source infrastructure.*

**Owner:** Nic Reilly
**Status:** Phase 0 draft. Becomes the baseline when phase 0 closes.
**Companion documents:** DESIGN.md (technical design and ADRs)
**Supersedes:** SCOPE.md (removed; folded in here)

---

## Step 0: Delivery model

**Hybrid.** The phase structure is fixed up front because the sequence is genuinely hard-dependent: no diff engine without a resource graph, no review queue without discrepancies. Inside each phase the work runs from a backlog in one to two week increments, because the domain model will change as it gets exercised. This is the same sequencing rule that worked on Ratchet: ship the core end to end first, then add expansions one at a time.

---

## Problem definition

Stated in the operator's language, deliberately containing no solution:

> A team cannot answer "is our infrastructure actually what we think it is," and the documents that claim to answer it are wrong within weeks of being written.

Everything below is one solution to that problem. Naming it separately matters because the alternatives are real and cheaper: a scheduled export into a spreadsheet, a nightly script that diffs two YAML files, or accepting the staleness and re-auditing quarterly. Datum is worth building because it is also a portfolio artifact, not because it is the only fix. If that ceases to be true, the honest move is to stop, and this section is what makes that check possible.

## Quality objectives

Quality attributes trade off against each other, so they get ranked rather than all maximized. The ranking differs by component, which is the point.

| Component | First priority | Explicitly sacrificed |
|---|---|---|
| Diff engine, matching, precedence | **Correctness.** A wrong discrepancy is worse than no discrepancy, because it teaches the operator to distrust the queue. | Robustness. On malformed input it refuses to produce a result rather than guessing. |
| Collectors | **Robustness.** A cloud API will return junk, time out, and rate-limit. The collector keeps going and records what it could not read. | Correctness in the strict sense. A partial run is a valid outcome. |
| Intent ingestion | **Correctness.** A bad revision is rejected whole; it never half-applies. | Convenience. Validation is strict and will annoy the author. |
| Web UI | **Usability.** The review queue is the product. | Flexibility. Fewer options, one good path. |
| Whole system | **Understandability.** A reader has fifteen minutes. | Performance. Nothing here is tuned until measured. |

Stating these matters because people build what they are told to build. Unstated quality goals do not emerge on their own, and this project has one developer, so nobody else will supply the missing objective.

### What enforces each of them

**Added at Phase 3 close (2026-07-29), because stating an objective turned out not to be enough.** The collectors' robustness objective was stated plainly in the table above from Phase 0 onward, and the Phase 1 collector violated it anyway: one malformed record and it discarded every healthy record beside it (CF-1). The sentence was necessary and insufficient.

This column exists so an objective with nothing behind it is visible *before* it becomes a defect rather than after. "Intent only" is a permitted answer. It is not a permitted surprise.

| Objective | Enforced by |
|---|---|
| Diff determinism | A property-based test asserting identical inputs produce an identical discrepancy set, plus canonical ordering inside the engine |
| Diff and matcher correctness | The adversarial corpus in DESIGN §12, branch coverage gated at 100% in CI, complexity capped at 10, and non-authoring model review before merge |
| Collector robustness | **Structural.** The `fetch`/`normalize` split means an adapter never receives the collection it would have to abort, so per-record failure is not a rule to remember. Plus the `read == written + errors` invariant, asserted in code and tested across clean, all-bad, and mixed payloads |
| Collectors never infer absence from an incomplete read | `may_infer_absence` as a positive whitelist (`== SUCCESS`, never `!= FAILED`), and `has_gap` forcing PARTIAL even when every record that arrived was clean |
| Intent correctness | Whole-revision validation that accumulates every error before rejecting, four blocking layers, and branch coverage gated at 100% |
| A conflict never surfaces as the database's exception | Conversion at the intent boundary for both ways a race loses — constraint and transaction rollback — and a review-checklist line |
| Read-only toward the estate | **Intent only.** No mechanism prevents a collector from issuing a write; ADR-007 and the `Collector` protocol's documented prohibitions are all that stand there. The protocol gives an adapter no writing tool, which is weak enforcement rather than none |
| Precedence explainability (D6) | **Still nothing, but the mechanism is now decided rather than aspirational.** As of 2026-07-30, evaluation returns the deciding rule inside its result type, and a missing rule returns an explicit undecidable rather than a synthesized one — so there is no case where the type is satisfied by inventing a rule. Structural once 1.5.3 ships; this row stays "nothing" until it does, because a decision is not an enforcement |
| Review queue usability (D8) | **Nothing yet.** `dont-make-me-think-ui` is bound to 1.5.5, and the keyboard path is the only part with a test |
| Whole-system understandability | The construction conventions, the two-tier review rule, and CI refusing unformatted or lint-failing code |
| Performance | Nothing, deliberately. Nothing here is tuned until measured, and the benchmark is scheduled per phase close rather than enforced per commit |

---

## Step 1: Scope statement

| Category | Content |
|---|---|
| **Justification** | An infrastructure inventory goes stale within weeks, and the naive fix, importing discovered state over the record, destroys the curated intent that made it valuable. Datum makes the gap between declared and discovered state the primary object: stored, typed, assigned, resolved, audited, never silently closed by an importer. Secondary justification: a portfolio artifact demonstrating deep domain modeling, relational data design, and full stack delivery. |
| **Product scope** | A multi-tenant web application. Intent is authored as declarative documents in Git and ingested on push. Collectors read actual state from Kubernetes and Oracle Cloud on a schedule. A diff engine matches discovered to declared resources, emits field-level discrepancies, and applies a per-field precedence policy. Discrepancies are first-class records with a lifecycle. A TypeScript frontend provides the review queue, resource explorer, and history. A read-only REST API serves automation consumers. |
| **Deliverables (with acceptance criteria)** | See the deliverables table below. |
| **Exclusions** | No writes to the estate: Datum never applies, provisions, or remediates. No DCIM (racks, cables, power, physical devices). No monitoring, metrics, or health alerting. No secrets management. No config rendering or templating. No installed agents; collectors run centrally against APIs. No billing or cost analysis. No paid, trial-limited, or source-available components anywhere in the stack. |
| **Constraints** | Solo developer, no budget. Every component must be OSI-approved open source and free at the scale used. Deployment target is the existing Oracle Cloud always-free instance. CI must fit the GitHub Actions free tier plus a self-hosted runner. The design must be defensibly distinct from NetBox. No hard external deadline, so schedule pressure comes from job-search timing, not from a customer. |
| **Assumptions** | See the assumptions table below. |

### Deliverables and acceptance criteria

| # | Deliverable | Acceptance criteria |
|---|---|---|
| D1 | Resource graph and typed schema | At least four resource kinds persist with typed attributes, relationships, and tenant scoping. A new kind requires no migration. Constraints enforced in Postgres, not only in Python. |
| D2 | Intent ingestion from Git | A push produces a new immutable intent revision. Malformed documents fail with a line-level error and leave the previous revision active. Every resource traces to the commit that declared it. |
| D3 | Collector framework and two collectors | Kubernetes and Oracle Cloud collectors run on a schedule, are idempotent, record run counts and errors, and degrade to a partial run rather than a failed one on single-resource failure. |
| D4 | Identity matching | Discovered resources match declared resources by a documented, testable strategy. Match confidence is recorded. Unmatched resources on either side are surfaced, never silently dropped. |
| D5 | Diff engine | Field-level discrepancies for matched pairs plus orphan discrepancies for unmatched resources. Deterministic: identical inputs produce an identical discrepancy set, proven by property-based tests. |
| D6 | Precedence policy | Per kind and per field, declares which plane is authoritative. Discovery can never overwrite an intent-authoritative field. Policy is versioned, and its evaluation is explainable for any single field. |
| D7 | Discrepancy lifecycle | States: open, acknowledged, accepted, suppressed, resolved. Suppression requires a reason and an expiry. Transitions record actor and timestamp and cannot be edited retroactively. |
| D8 | Review queue UI | Side-by-side declared versus discovered view with filtering and bulk actions. A reviewer can clear a fifty-discrepancy backlog without leaving the keyboard. |
| D9 | Resource explorer UI | Search and filter across the graph, resource detail with relationships, full change history per resource. |
| D10 | Read-only API | Documented, versioned, paginated, filterable, consumable by a script with no UI knowledge. |
| D11 | Audit and history | Every state-changing action is attributable and queryable. History survives resource deletion. |
| D12 | Multi-tenancy and permissions | Tenant isolation enforced at the query layer, with a test proving a cross-tenant read fails. Roles: viewer, reviewer, admin. |
| D13 | Deployment and operations | One-command local bring-up. Publicly reachable on the Oracle Cloud host with TLS. Automated Postgres backups with a tested restore. |
| D14 | Documentation | Design doc, ADR set, README including the NetBox differentiation section, API docs, public demo seeded with drift. |

**Requirement priorities.** Must have: D1 to D8, D10, D11, D13, D14. Should have: D9, D12. Nice to have: drift timeline visualization, webhook notifications, a second cloud collector, an intent linting CLI on PyPI.

### Assumptions

| Issue | Approach |
|---|---|
| Oracle Cloud free tier may not carry Postgres, Valkey, Celery, the app, and a CI runner at once | Assume it carries the app tier plus database. Measure at the end of phase 1. If constrained, shed the self-hosted runner first and fall back to hosted CI minutes. |
| No real estate exists that has drifted enough to demo | Assume a synthetic estate generator can produce believable drift. Build it in phase 1 so the diff engine is not blocked on real infrastructure rotting. |
| Identity matching may be harder than estimated | Assume exact-key matching plus one heuristic is enough for v1. Record match confidence from day one so a better strategy layers in without a schema change. |
| Schema-defined kinds could become an unqueryable JSON swamp | Assume a typed-column and JSONB split works. Decide the split rule in phase 1, revisit if query complexity grows. |
| A solo build of this size can stall mid-way | Assume the Ratchet sequencing rule holds: phases 0 to 5 end to end before any expansion begins. |
| Available time is roughly 16 focused hours per week | Plan against that. Every duration below is stated in those weeks, not calendar weeks of wall time. |

### Non-functional requirements

The first draft specified what the system does and skipped what it must be. Each of these is testable, which is the bar for keeping it.

| Attribute | Requirement | How it is verified |
|---|---|---|
| Performance | A diff over an estate of 5,000 resources completes in under 60 seconds. Review queue pages render in under 500 ms at p95. | Benchmark against the synthetic estate at each phase close |
| Scale ceiling | Designed for 10,000 resources and 20 tenants. Beyond that is out of scope and stated as such. | Load test once, at phase 5 |
| Reliability | A collector failure never corrupts the declared plane. A crashed run leaves no partial state. | Fault-injection test in the collector suite |
| Security | Collector credentials are never logged, never returned by the API, and never rendered in the UI. All API access authenticated. | Test asserting credentials absent from log output |
| Data integrity | Discrepancy transition history is append-only. No code path updates or deletes a transition row. | Database-level constraint plus a test that tries |
| Maintainability | A new resource kind is added without a migration or a code change. | Add a fifth kind at phase 5 as an acceptance test |
| Recoverability | Full restore from backup in under 30 minutes. | Perform the restore once, in phase 5, and record the time |
| Accessibility | Review queue is fully keyboard operable. | Manual pass, no mouse |

**Known incompleteness, flagged rather than guessed:** retention policy for drift history, behavior when an intent revision deletes a resource with open discrepancies, and whether tenants may define their own kinds or only use global ones. These are resolved before phase 4, not now. Flagging them is deliberate: an iterative project is allowed to leave requirements open, but it is not allowed to leave them unnoticed.

**All three were answered at the opening of phase 4 on 2026-07-30, on the schedule this note set.** Rationale in DESIGN §23; the decisions themselves are summarised in "Phase 4 opening decisions" at the end of this document. Recorded here rather than only there because the value of a flagged gap is that someone can check it closed on time, and that check has to be possible from where the flag was raised.

### Stakeholders

- **Initiator and driver:** Nic. Sole voice defining what the result should be.
- **Supporter:** the toolchain and the two coding models, which determine what can be done at what pace. Supporters do not get a vote on scope.
- **Sponsor and owner:** Nic. Funded in time rather than money, which makes time the scarce resource to protect.
- **Champion:** none. Logged as a gap: a solo project has nobody to keep it alive during a slow week, so the control loop in Step 5 has to do that job.
- **Users and implementers:** hiring managers and interviewing engineers, who will judge the repo in fifteen minutes; plus the demo persona, a platform engineer inheriting an undocumented estate. Every feature should be defensible to that persona.
- **Opponent:** the reviewer who says "this is just NetBox." Addressed by the differentiation table below, which must be visible in the README rather than buried.

### Differentiation from NetBox

A scope requirement, not a disclaimer. Each row is a design decision that changes the architecture.

| Dimension | NetBox | Datum |
|---|---|---|
| Estate | Racks, devices, interfaces, cables, circuits, IPAM | Cloud and Kubernetes resources. No physical modeling. |
| Where intent lives | Records edited in a UI; the database is the origin | Declarative documents in Git, reviewed by pull request. The database is a projection of a commit, never the origin. |
| Data model shape | Fixed, richly specific models per device concept | A typed resource graph with schema-defined kinds. A new kind is data, not a migration. |
| Discovered data | Imported, with the known risk of overwriting curated records | Never written into the intent projection. Stored separately, only ever compared. |
| The diff | A changelog recorded after the fact | A first-class Discrepancy entity with lifecycle, assignee, suppression reason, expiry |
| Conflict handling | Largely human convention | An explicit, versioned, per-field precedence policy the engine evaluates and can explain |
| Direction | Increasingly bidirectional in commercial editions | Strictly read-only toward the estate, permanently and by design |

**Build rule:** do not read NetBox source while designing the schema. Differentiation is only credible if the model was derived from first principles, and an interviewer will probe it.

### Technology decisions

| Layer | Choice | Note |
|---|---|---|
| Backend | Django (BSD) | |
| API | django-ninja (MIT) | Typed schemas pair with the TypeScript client |
| Database | PostgreSQL | |
| Queue | Celery (BSD) | |
| Broker and cache | Valkey (BSD) | Chosen over Redis, which relicensed away from BSD in 2024 |
| Frontend | React, TypeScript, Vite, Tailwind (MIT) | |
| Collectors | Kubernetes Python client, OCI SDK (Apache 2.0) | |
| Proxy and TLS | Caddy (Apache 2.0) | |
| Runtime | Docker Compose, k3s later if warranted | |
| CI | GitHub Actions free tier plus self-hosted runner | |
| Project license | Apache 2.0 | |

Rejected: Redis and Elasticsearch (relicensed), anything whose free tier is a commercial trial, anything whose self-hosted edition is feature-crippled.

---

## Step 2: Work Breakdown Structure

```
1. Datum
   1.1 Phase 0: Foundation
       1.1.1 Scope statement and design doc
       1.1.2 Repo, Compose stack, CI, pre-commit, test harness
       1.1.3 ADR-001 (schema-defined kinds) and ADR-004 (intent in Git)
   1.2 Phase 1: Resource graph
       1.2.1 Kind schema and typed attribute storage
       1.2.2 Relationships, tenancy, Postgres constraints
       1.2.3 Synthetic estate generator
       1.2.4 Read-only API over the graph
   1.3 Phase 2: Intent ingestion
       1.3.1 Intent document format and validator
       1.3.2 Git ingestion and immutable revisions
       1.3.3 Projection of a revision into the graph
       1.3.4 Line-level validation error reporting
   1.4 Phase 3: Discovery
       1.4.1 Collector framework, scheduling, run records
       1.4.2 Kubernetes collector
       1.4.3 Oracle Cloud collector
       1.4.4 Partial-failure and idempotency handling
       1.4.5 Phase 1 carry-forward remediation (CF-1, CF-3; CF-2 folded into 1.3.1)
   1.5 Phase 4: Reconciliation core
       1.5.0 Null-versus-absent resolution (blocks 1.5.3 and 1.5.4)
       1.5.1 Identity matching with confidence scoring
       1.5.2 Diff engine and orphan detection
       1.5.3 Precedence policy model and evaluator
       1.5.4 Discrepancy lifecycle and audit trail
       1.5.5 Review queue UI
       1.5.6 Resource explorer UI
   1.6 Phase 5: Production readiness
       1.6.1 AuthN, roles, tenant isolation tests
       1.6.2 Deploy, TLS, backups, seeded public demo
       1.6.3 README, API docs, differentiation writeup
   1.7 Expansions (rolling wave, one at a time, each with its own design doc and branch)
       1.7.1 Drift timeline visualization
       1.7.2 Webhook notifications
       1.7.3 Intent linting CLI on PyPI
       1.7.4 Third collector
```

No gaps: every deliverable D1 to D14 traces to a package. No overlaps: UI work appears only under 1.5.5, 1.5.6; auth appears only under 1.6.1. Phase 1.7 is deliberately left at low detail (rolling wave) because those decisions depend on what the core build teaches.

**One deliberate exception to "no overlaps": 1.4.5.** It is a remediation package, so it is scheduled by phase but touches modules owned by other packages — intent validation (1.3.1) and CI (1.1.2). This is a known trade recorded at Phase 1 close, not an accident of decomposition. It carries one hazard worth stating plainly: a package that spans layers invites fixes landing where the work is scheduled rather than where the code belongs. See "Carried forward from Phase 1 close" for what each fix may and may not touch.

### WBS dictionary

| ID | Work package | Responsible | Duration | Depends on | Top risk |
|---|---|---|---|---|---|
| 1.1.2 | Repo, Compose, CI, test harness | Bulk model | 1 wk | 1.1.1 | Repo rooted wrong so CI never runs (happened on Ratchet) |
| 1.1.3 | ADR-001 and ADR-004 | Nic + kernel model | 0.5 wk | 1.1.2 | Deciding by default instead of on purpose |
| 1.2.1 | Kind schema and typed storage | Nic + kernel model | 2 wk | 1.1.3 | Every downstream table inherits this shape |
| 1.2.2 | Relationships, tenancy, constraints | Nic + kernel model | 1.5 wk | 1.2.1 | Forward references to resources that do not exist yet |
| 1.2.3 | Synthetic estate generator | Bulk model | 1 wk | 1.2.2 | Generated drift too tidy to be a real test |
| 1.2.4 | Read-only API over the graph | Bulk model | 1 wk | 1.2.2 | Filter surface grows without a spec |
| 1.3.1 | Intent format and validator | Nic + kernel model | 1.5 wk | 1.2.1 | Format churn after documents exist |
| 1.3.2 | Git ingestion, immutable revisions | Bulk model | 1.5 wk | 1.3.1 | Duplicate delivery of the same commit |
| 1.3.3 | Projection into the graph | Nic + kernel model | 1 wk | 1.3.2, 1.2.2 | Rebuild versus incremental chosen by accident |
| 1.3.4 | Line-level validation errors | Bulk model | 0.5 wk | 1.3.3 | Low value, easy to over-invest in |
| 1.4.1 | Collector framework | Nic + kernel model | 1.5 wk | 1.2.2 | Collector allowed to write to the declared plane |
| 1.4.2 | Kubernetes collector | Bulk model | 1 wk | 1.4.1 | Resource churn creates phantom drift |
| 1.4.3 | Oracle Cloud collector | Bulk model | 1 wk | 1.4.1 | API rate limits |
| 1.4.4 | Partial failure and idempotency | Nic + kernel model | 1 wk | 1.4.2 | A partial read read as mass deletion |
| 1.4.5 | Phase 1 carry-forward remediation | Bulk model | 0.5 wk | 1.4.4 | Fixed in the phase that schedules it rather than the layer that owns it |
| 1.5.0 | Null-versus-absent resolution | Nic + kernel model | 1 wk | 1.4.3 | Deferred once already; deferring again lands it after records become durable |
| 1.5.1 | Identity matching | Nic + kernel model | 2 wk | 1.3.3, 1.2.3 | Renames and recreations break matching |
| 1.5.2 | Diff engine | Nic + kernel model | 2.5 wk | 1.5.1 | Nondeterminism from unordered collections |
| 1.5.3 | Precedence policy and evaluator | Nic + kernel model | 2 wk | 1.5.2 | Unexplainable policy means operators stop trusting it |
| 1.5.4 | Discrepancy lifecycle and audit | Nic + kernel model | 1.5 wk | 1.5.2 | Re-detected drift duplicating suppressed records |
| 1.5.5 | Review queue UI | Bulk model | 2.5 wk | 1.5.3, 1.5.4 | Authoritative side visually ambiguous |
| 1.5.6 | Resource explorer UI | Bulk model | 1.5 wk | 1.2.4 | Scope sprawl into a general admin tool |
| 1.6.1 | Auth, roles, isolation tests | Nic + kernel model | 1.5 wk | 1.5.5 | Isolation asserted but never tested |
| 1.6.2 | Deploy, TLS, backups, demo seed | Bulk model | 1.5 wk | 1.6.1 | Free-tier host runs out of headroom |
| 1.6.3 | Docs and differentiation writeup | Nic | 1 wk | 1.6.2 | Written last, tired, and thin |

---

## Step 3: Schedule and critical path

Durations are in working weeks of roughly 16 focused hours.

| ID | Activity | Dur | Predecessors |
|---|---|---|---|
| A | Scope and design doc | 0.5 | none |
| B | Repo, Compose, CI, harness | 1 | A |
| C | ADR-001 and ADR-004 | 0.5 | B |
| D | Kind schema and typed storage | 2 | C |
| E | Relationships, tenancy, constraints | 1.5 | D |
| F | Synthetic estate generator | 1 | E |
| G | Read-only API over graph | 1 | E |
| H | Intent format and validator | 1.5 | D |
| I | Git ingestion and revisions | 1.5 | H |
| J | Projection into graph | 1 | I, E |
| K | Line-level validation errors | 0.5 | J |
| L | Collector framework | 1.5 | E |
| M | Kubernetes collector | 1 | L |
| N | Oracle Cloud collector | 1 | L |
| O | Partial failure and idempotency | 1 | M |
| P | Identity matching | 2 | J, F |
| Q | Diff engine | 2.5 | P |
| R | Precedence policy | 2 | Q |
| S | Discrepancy lifecycle | 1.5 | Q |
| T | Review queue UI | 2.5 | R, S |
| U | Resource explorer UI | 1.5 | G |
| V | Auth, roles, isolation tests | 1.5 | T |
| W | Deploy, TLS, backups, demo | 1.5 | V |
| X | Docs and writeup | 1 | W, T, U |

**Critical path:** A, B, C, D, H, I, J, P, Q, R, T, V, W, X. Length 21 weeks.

**The finding that should change how you build.** The critical path runs through *intent ingestion*, not discovery. Every discovery activity (L, M, N, O) carries about twelve weeks of slack, because the synthetic estate generator (F) is what actually unblocks identity matching and the diff engine. Discovery is the part that feels like the real work, and it is the part you should build second.

**Resource-constrained reality.** CPM assumes unlimited resources. With one developer nothing runs in parallel, so total effort, 32.5 activity-weeks, is the real calendar figure, not 21. At 16 hours per week that is roughly **520 hours, about eight months part-time**.

**Milestones**

| Milestone | Earliest (effort-weeks) |
|---|---|
| M1 Phase 0 complete, CI green | 2.0 |
| M2 Resource graph queryable via API | 6.5 |
| M3 First intent revision projected from a real commit | 8.0 |
| M4 First discrepancy produced by the engine | 12.5 |
| M5 Backlog clearable end to end in the UI | 17.0 |
| M6 Public demo live with seeded drift | 20.0 |
| M7 Documented and closed | 21.0 |

**Compression options** if timing gets tight: drop N (Oracle collector) and ship with Kubernetes only, drop U (resource explorer) to should-have, and hold K until after M6. None of those touch the critical path.

---

## Construction practices

### Integration strategy

**T-shaped, then feature-oriented.** The vertical bar of the T comes first: one resource kind, declared in Git, discovered by one collector, matched, diffed, and reviewed in the UI. One kind, one collector, one screen, end to end. That validates the riskiest architectural assumption — that the resource graph shape survives contact with both planes — before any breadth exists to invalidate it. **Build the vertical slice inside phase 1, not after phase 4.**

### Daily build and smoke test

- The build runs on every push and nightly, and includes a smoke test that exercises the vertical slice end to end: ingest an intent revision, run a collector against the synthetic estate, produce a discrepancy, transition it.
- The smoke test grows as the system does. A stale smoke test is worse than none.
- A broken build gets fixed before new work starts.
- Commit at least every two days.

### Defect removal plan

| Technique | Where it applies | Cadence |
|---|---|---|
| Checklist self-inspection | The correctness kernel: matching, diff, precedence, lifecycle | Before merging any kernel PR |
| Model review as second reader | Kernel PRs, reviewed by the model that did not author them | Every kernel PR |
| Code reading | Bulk-authored code, read in batches | Weekly |
| External review | Design doc and schema, once | At M4 |

### Measurement

| Measure | Question it answers |
|---|---|
| Hours per work package | Are the Step 3 estimates real |
| Defects found, by phase found in | Is review catching things before tests do |
| Defects found after a phase closed | Was the phase closed honestly |
| Cyclomatic complexity, worst routine | Is the kernel drifting toward unmaintainable |

---

## Phase 1 vertical slice (the current build target)

**Instruction to the implementer:** build exactly the slice below and stop at the acceptance test. Do not add a second kind, a second collector, a second screen, precedence, suppression, or history. Those are later phases. If a decision is needed that this spec does not cover, stop and ask rather than inventing scope.

### What the slice is

The narrowest path that touches every architectural layer once: **one kind, declared in Git, discovered by one collector, matched, diffed, and reviewed in the UI.** Concretely: a Kubernetes Deployment, declared as intent in a Git repo, discovered from a live or recorded cluster, matched by natural key, compared field by field, with the resulting discrepancies visible and clearable in a browser.

### What the slice is NOT

- A second kind or a second collector (phase 3 widens collectors; kinds are added as data thereafter)
- Precedence policy (phase 4)
- Discrepancy suppression, acknowledgement, or full lifecycle (phase 4; phase 1 has only open and resolved)
- Change history and audit trail beyond what the lifecycle needs (phase 4)
- Multi-tenancy enforcement and roles (phase 5; but the schema carries `tenant_id` from the first migration and every query is written tenant-scoped from day one)
- Authentication (phase 5)
- The synthetic estate generator as a polished tool (a minimal fixture is enough for phase 1)

### Build order within the slice

Each step ends in something runnable. Do not proceed to the next until the current one runs.

1. **Kind and resource schema.** The `Kind` table (name, attribute schema) and a `resource` representation carrying the global core columns from ADR-005 (`tenant_id`, `kind`, `name`, `scope`, `provider_id`, timestamps) plus a JSONB attribute bag. Two physical resource tables, `declared_resource` and `discovered_resource`, separate. Seed a single Deployment kind.
2. **Discovered plane, minimal collector.** A collector that reads Deployments from a cluster and writes `discovered_resource` rows. For phase 1 it may read from a recorded JSON fixture rather than a live cluster. The collector records a run with counts. Idempotent: running it twice produces the same rows, not duplicates.
3. **Declared plane, minimal intent ingestion.** A Git repo containing one Deployment declared as a document. Ingest it into an immutable intent revision and project it into `declared_resource`. A malformed document fails validation and leaves no partial state. The resource traces to its commit.
4. **Matching.** Match the declared Deployment to the discovered one by natural key (kind, tenant, scope, name). Write a `match` row carrying `strategy`, `confidence`, and `state` per DESIGN section 12. The one-to-one database constraint is in place.
5. **Diff engine.** Compare the matched pair field by field. Produce field-level discrepancies plus orphan discrepancies for anything unmatched on either side. Deterministic: identical input produces an identical discrepancy set. This is kernel code; it meets the kernel test bar and is reviewed by the non-authoring model.
6. **Read-only API.** Endpoints to list resources and list discrepancies, tenant-scoped, paginated.
7. **Review queue UI.** One screen: the list of discrepancies with the declared and discovered values side by side, the authoritative side unmistakable, and an action to mark a discrepancy resolved. Keyboard operable. No bulk actions yet.

### Acceptance test

1. A Git repo declares one Deployment named `web` in scope `default` with `replicas: 3`.
2. Ingestion projects it into the declared plane, traceable to the commit.
3. The collector fixture reports the same Deployment with `replicas: 5`.
4. A collector run projects it into the discovered plane.
5. Matching links the two by natural key, writing a high-confidence match.
6. The diff engine produces exactly one field-level discrepancy: `replicas`, declared 3, discovered 5. No orphans.
7. The discrepancy appears in the review queue UI with 3 and 5 shown and the declared side marked authoritative.
8. Marking it resolved moves it out of the open queue.
9. Re-running the diff on unchanged input produces the identical discrepancy set (determinism check).

Plus three negative checks:
- A malformed intent document is rejected and the previous revision stays active.
- A Deployment present in discovery but absent from intent produces one "discovered, undeclared" orphan.
- A Deployment present in intent but absent from discovery produces one "declared, missing" orphan.

### Definition of done for phase 1

- The acceptance test and its negative checks pass.
- The diff engine and matcher meet the kernel bar: branch-covered, complexity under 10, reviewed by the non-authoring model against the checklist.
- The daily build runs the acceptance test as its smoke test.
- DESIGN.md section 24 ("how this design could fail") is revisited with real code in hand.
- No scope from a later phase has been pulled forward.

### The one rule that matters most

When in doubt, build less. A working slice that does one kind is worth more than a half-built system that intends to do ten.

---

## Carried forward from Phase 1 close (2026-07-26)

Three defects found while building the Phase 1 slice. All are **invisible at one kind, one collector, one record** — which is exactly why the slice did not fail on them, and exactly why they must not be forgotten. Each is reproduced, not suspected.

### Scheduling decision (2026-07-27)

**All three are executed in Phase 3, under work package 1.4.5.** Two of them could be done sooner — CF-2's natural home is Phase 2 and CF-3 is not phase-gated at all — and they are deliberately not being done sooner. The Phase 1 slice is green, reviewed, and closed; the decision is to leave working code alone and batch the remediation into one package rather than reopening closed work piecemeal.

Two consequences to hold onto, so this is a chosen trade rather than a forgotten one:

- **Each fix still lands in the module that owns it, whatever phase does the work.** Scheduling is not the same as layering. CF-2's code belongs in `datum/intent/`; building it inside a collector during Phase 3 would put intent validation in the wrong layer and is not what this decision authorizes.
- **CF-2 accepts known rework.** Phase 2 builds out 1.3.1, the intent validator — the very module CF-2 patches. Deferring means Phase 2 will construct that validator with this hole knowingly left in it, and Phase 3 will reopen it. That is a real cost, accepted for the sake of not touching closed code now. If Phase 2 ends up rewriting `_parse_all` regardless, fold CF-2 in there and strike it from 1.4.5 rather than doing the work twice.

### Update (2026-07-27, Phase 2 open): CF-2 folded into 1.3.1, struck from 1.4.5

The escape hatch above fired exactly as written. Phase 2 replaces the document format (Datum-native envelope, DESIGN §10) and therefore rewrites `_parse_all` and `documents.py` wholesale. Adding the duplicate-natural-key check to a validator that is being rebuilt anyway costs a check and a test; deferring it would mean deliberately constructing the new validator with a known hole and reopening it one phase later.

**CF-2 is done in 1.3.1 and removed from 1.4.5.** Work package 1.4.5 now carries CF-1 and CF-3 only. This is the cheap outcome the deferral decision was designed to allow, not a reversal of it.

All three are now fixed. The mitigations below are kept as the record of what stood between each defect and a user while it was deferred, and of which package actually closed it.

| ID | Defect | Owning module | Mitigation while deferred |
|---|---|---|---|
| CF-1 | ~~Collector drops good records on one bad record~~ | `datum/discovery/collector.py` | **Fixed 2026-07-29 in 1.4.1.** Folded into the collector framework rather than patched: the fetch/normalize split means an adapter is never handed the collection it would have to abort. |
| CF-2 | ~~Duplicate declared identity caught by a DB constraint, not the validator~~ | `datum/intent/documents.py`, `ingest.py` | **Fixed 2026-07-27 in 1.3.1.** No longer deferred; see the update above. |
| CF-3 | ~~CI never builds or tests `web/`~~ | `.github/workflows/ci.yml` | **Fixed 2026-07-29 in 1.4.5.** A `web` job runs lint, type check, build, and tests; the Python job asserts the generated enums still match their source. No `web/` change depends on anyone remembering three commands. |

### CF-1 → **1.4.4 Partial-failure and idempotency handling** (Phase 3, Discovery) · executed in **1.4.5**

**One malformed record discards every good record in the same read, and the run record misreports what was seen.**

`datum/discovery/collector.py:35`. `_read` wraps the whole-file parse in one `try`, so a single `MalformedProviderData` returns `[None]` and every healthy record in that payload is dropped. Reproduced with a 3-record fixture (`api` valid, `broken` missing `spec.replicas`, `worker` valid):

```
resources_read    = 1      (three records were in the file)
resources_written = 0      (both healthy resources lost)
rows in DB        = 0
status            = partial
```

Two distinct failures. The data loss is the obvious one. The second is that `resources_read = 1` is false — the run record is the audit trail for what the collector observed, and it under-reports 3 as 1, so an operator cannot detect that this happened.

This is precisely the risk already named against 1.4.4 in the WBS dictionary: *"A partial read read as mass deletion."* Phase 1 could not surface it because the fixture has exactly one record.

It also directly contradicts the stated quality objective for this component. The ranking above says collectors put **robustness** first: *"A cloud API will return junk, time out, and rate-limit. The collector keeps going and records what it could not read. A partial run is a valid outcome."* The current collector does the opposite — one piece of junk and it keeps nothing. This is not merely a defect to schedule; it is the collector failing its first-priority quality attribute.

**Fix direction:** normalize per record rather than per file. Accumulate valid snapshots, count each rejection into `errors`, persist the valid ones, and report `resources_read` as the true item count so `PARTIAL` means what it claims. Requires deciding the policy question 1.4.4 exists to answer: at what error ratio does a partial read become untrustworthy enough to reject wholesale rather than persist?

### CF-2 → **1.3.1 Intent format and validator** (owning layer: Phase 2) · executed in **1.4.5** (Phase 3)

> **Layer vs. schedule.** The fix belongs to intent validation and its code lands in `datum/intent/`. The *work* is scheduled into Phase 3 by the decision above. Doing it in Phase 3 must not mean building it in the Discovery layer — if 1.4.5 finds itself adding duplicate detection to a collector, that is the wrong turn.

**Duplicate declared identities are caught by a Postgres constraint, not by the validator — inverting the barricade.**

`DESIGN.md §12` states that two declared resources claiming one identity are "rejected at intent validation, not at matching." They are not. `datum/intent/documents.py` validates one document at a time with no cross-document check, and `ingest.py::_project` loops `create()`. A repo declaring `web` twice in one revision produces:

```
django.db.utils.IntegrityError (not a domain exception)
duplicate key value violates unique constraint "uq_declared_natural_key_per_revision"
```

The rejection is real but accidental — it depends on a constraint firing mid-transaction, and it breaks the contract every other malformed-intent path honours. Callers catching `InvalidRevision` (as `tests/test_intent.py` does) will not catch this. ADR-008 inverted: the boundary passed bad data inward and the database caught it.

Related: the Phase 1 kernel now asserts against duplicate natural keys in `match_by_natural_key` (added at Phase 1 close after the non-authoring-model review). That assertion is a last line of defence and should stay, but it is not a substitute for validating at the boundary — by the time the kernel sees it, the loader has already accepted it.

**Fix direction:** a duplicate-natural-key check across the document set in `_parse_all`, raising `InvalidRevision` before anything is written. Pairs naturally with 1.3.4 (line-level validation errors), which should name both conflicting files.

### CF-3 → **1.1.2 Repo, Compose, CI, test harness** (owning layer: Phase 0 infrastructure) · executed in **1.4.5** (Phase 3)

**CI does not build or test the frontend at all.**

`.github/workflows/ci.yml` contains no Node setup, no `npm ci`, no `npm test`, no `npm run build` — grep for `node|npm|web|vitest` returns nothing. Everything under `web/` therefore has zero automated protection: the review queue, the API client, and the generated `web/src/enums.ts`.

The last of those is the sharp edge. `scripts/gen_ts_enums.py` is the single source of truth binding Python enums to TypeScript, and nothing verifies the generated file still compiles or still matches its Python source. Python and TypeScript can drift silently with CI green.

This is the same failure class as the Ratchet incident named in the WBS risk column for 1.1.2 — the safety net exists but is not covering the thing. Nothing blocks it technically; it waits for 1.4.5 by choice, not by dependency. Note the standing cost: every `web/` change made before then is protected only by someone remembering to run `npm run lint`, `npm run build`, and `npm test` locally.

**Fix direction:** a second CI job — `actions/setup-node`, `npm ci`, `npm run build` (which runs `tsc`), `npm test` — plus a step asserting `python scripts/gen_ts_enums.py` leaves no diff, so drift between the two languages fails the build.

---

## Found in Phase 3 (2026-07-29): the concurrency defect class

Found while designing the collector lock for 1.4.4, not by a failing test. Both are in **already-merged Phase 2 code**, and neither is a data-loss defect: in both cases a Postgres constraint holds the invariant. What fails is the *contract*. The conflict reaches the caller as `IntegrityError`, wearing the database's abstraction rather than the module's, so no caller can be written to expect it.

That is the same inversion as CF-2, which is why these are recorded as a class rather than two more one-off entries. The rule they violate, and the mechanism decisions that fix them, are in DESIGN §11 under "concurrency and isolation". Both are stated relative to Postgres's default READ COMMITTED, which DESIGN now names explicitly.

| ID | Defect | Owning module | Scheduled | Mitigation while deferred |
|---|---|---|---|---|
| CF-4 | Check-then-insert on `(tenant, commit_sha)` in `ingest_revision` races with itself | `datum/intent/ingest.py` | 1.4.4 | Run exactly one Celery beat worker. Do not add the §10 webhook trigger before this is fixed. |
| CF-5 | `_project` deactivates then inserts, so two revisions can both become active | `datum/intent/ingest.py` | 1.4.4 | As above. |

**Why 1.4.4 rather than a Phase 2 reopen.** 1.4.4 is already the package that owes the collector lock, and all three races share one mechanism decision. Fixing the intent pair anywhere else would mean making that decision twice. This is the same trade the CF-1/CF-2/CF-3 deferral made, with the same condition attached: **the code lands in the module that owns it.** CF-4 and CF-5 are fixed in `datum/intent/`, not in a collector, whatever package does the work.

**The standing cost, stated plainly.** Until 1.4.4 runs, the mitigation is operational rather than structural — it depends on nobody scaling the worker to two replicas and nobody landing the webhook. Neither is enforced by anything in the repository. The webhook is the one to watch: it is listed in §10 as calling the same entry point deliberately, which is correct design and is exactly what makes simultaneous delivery certain rather than unlikely.

---

## Construction practice: usability and visualization references

Three reference skills are available to the build. They are recorded here rather than remembered, for the same reason the defect-removal plan is a table: a practice that depends on someone recalling it at the right moment is not a practice.

Each is bound to the work package that needs it. None of them authorizes scope.

| Package | Reference | What it is for |
|---|---|---|
| 1.5.5 Review queue UI | `dont-make-me-think-ui` | The named risk against this package is *"authoritative side visually ambiguous"*, and D8 requires a fifty-item backlog clearable without leaving the keyboard. Both are usability claims, and neither is settled by taste. |
| 1.5.6 Resource explorer UI | `dont-make-me-think-ui` | Navigation and information scent across the graph. Also a guard on this package's own risk, *"scope sprawl into a general admin tool"*: the discipline of one obvious path is what keeps an explorer from growing into an admin console. |
| 1.7.1 Drift timeline visualization | `big-book-dashboard-design`, `Information Dashboard Design` | The only charting work anywhere in this project. Chart selection, colour, and whether the thing can be read accurately rather than merely looks informative. |

**Why the dashboard references appear once, against an expansion.** The exclusions forbid monitoring, metrics, and health alerting, and nothing in D1–D14 is a dashboard. The resource explorer is search, filter, detail, and history — applying dashboard design to it would mean inventing a KPI screen the scope statement rules out. Recording them against 1.7.1 keeps them available for the one case that is genuinely data visualization, and keeps them from justifying a case that is not.

### The ordering that already holds, stated so it is not disturbed

The usability work on 1.5.5 and 1.5.6 must not be the first `web/` change protected by nothing. It will not be: **CF-3 delivers frontend CI in 1.4.5, which is Phase 3, and both UI packages are Phase 4.** The dependency is satisfied by the existing phase order, so nothing needs rescheduling — this paragraph exists to record *why* the order matters, so a later compression pass does not move CF-3 later without noticing what it is holding up.

The standing rule from the CF-3 entry still applies until 1.4.5 runs: any `web/` change before then is protected only by someone running `npm run lint`, `npm run build`, and `npm test` by hand. That is a reason not to open the review queue for polish early, not a reason to move CF-3.

---

## Scheduled at the Phase 3 second-kind decision (2026-07-29): WBS 1.5.0

Adding the second kind in 1.4.3 was the event DESIGN §10 and §24 named as the trigger for two deferred simplifications. Only one of them actually fired, and the reason the other did not is recorded here so the deferral is a decision rather than an omission.

### What was deferred, and the corrected trigger

| Simplification | Where | Status |
|---|---|---|
| Every attribute in a kind's `attribute_schema` is required; optionality is not expressible | `datum/intent/documents.py` (§10) | **Still deferred.** The second kind is expressible with required attributes only, so the stated trigger — "a second kind exposes optional fields" — has not fired. |
| An absent key and an explicit null are compared distinctly but reported identically | `datum/reconcile/diff.py` (§24) | **Scheduled as 1.5.0**, before 1.5.3 and 1.5.4. |

**The doc's trigger for null-versus-absent was the wrong trigger.** "When a second kind exposes optional fields" describes when the ambiguity becomes *reachable*, not when it becomes *expensive*. Two things in Phase 4 set the real deadline:

- **Precedence (1.5.3)** answers which plane is authoritative for a field, and that question is ill-posed when "absent" and "null" are one value. Declared-absent plausibly means intent has no opinion and discovery's value stands; declared-null plausibly means intent requires the field empty. Those demand opposite outcomes, and D6 requires the policy be explainable **for any single field** — a decision whose input is ambiguous cannot be explained.
- **The lifecycle (1.5.4)** is where discrepancy records stop being disposable. Today `run_reconciliation` deletes and rewrites open discrepancies every run, so the ambiguity is transient — recompute and it is gone. Once suppression carries a reason and an expiry, records persist, and changing the semantics afterwards makes historical records retroactively ambiguous: a suppressed discrepancy would no longer mean what it meant when someone suppressed it.

So the cost curve is not smooth. It is cheap now, cheap through Phase 3, and steps up sharply at 1.5.3. 1.5.0 exists to land before that step.

### Why optionality is safe to leave, and null-versus-absent is not

They were deferred together and are now separated, which needs a reason. **Loosening validation later is not a breaking change.** Making a required attribute optional never invalidates a document that already supplied it, so this deferral creates no migration burden for any intent repository — the asymmetry genuinely favours waiting until a real kind motivates the design, which is what §10 wanted in the first place.

Null-versus-absent has no such reprieve, because it does not loosen a rule; it changes what a discrepancy *means*.

### The condition attached to the second kind

The second kind was chosen with required attributes only, which is what keeps optionality deferred. That choice is only legitimate while it is also honest: **if a realistic provider payload cannot be modelled without an optional attribute, the trigger has fired and optionality is done then, not later.** Recorded so the constraint is testable rather than convenient.

Related limit noticed while choosing the schema, not scheduled: `attribute_schema` is shared by both planes, so an attribute that only discovery can observe — a lifecycle state, a provider-assigned address — cannot be modelled without intent being required to declare it. Not a defect today, since no kind needs one. It belongs with the precedence work if it ever bites, because "this field is observed, never declared" is a precedence statement.

---

## Phase 3 close (2026-07-29)

Walked rather than declared. Each clause is matched to the test that holds it, because "we did that" and "there is a test that fails if we stop doing that" are different claims, and only the second survives the next phase.

### D3, clause by clause

> *Kubernetes and Oracle Cloud collectors run on a schedule, are idempotent, record run counts and errors, and degrade to a partial run rather than a failed one on single-resource failure.*

| Clause | Status | Held by |
|---|---|---|
| Two collectors | Met | `discovery/kubernetes.py` and `discovery/oci.py`, each one adapter over the shared framework |
| Run on a schedule | Met for Kubernetes; qualified for OCI, below | Beat entries `collect-kubernetes` and `collect-oci`; `test_configured_source_runs_the_collector_and_returns_the_run_id`, `test_oci_configured_runs_the_collector` |
| Idempotent | Met | `test_running_twice_is_idempotent` in both collector suites, `test_running_twice_over_a_partial_payload_is_also_idempotent`, `test_the_task_is_idempotent_across_ticks` |
| Record run counts and errors | Met | `test_read_equals_written_plus_errors`, parametrized over clean, all-bad, and mixed payloads in both suites; `test_resources_read_counts_what_the_provider_returned_not_what_survived` |
| Degrade to partial, not failed, on single-resource failure | Met | `test_one_malformed_record_does_not_discard_the_good_ones`, `test_a_partial_run_is_reported_as_partial`, `test_one_bad_record_does_not_take_the_good_ones_with_it` |

**The qualification, stated rather than glossed: the Oracle Cloud collector reads a recorded payload, not a live estate.** §11 authorised this explicitly — the live read waits for credentials that do not exist, and the normalizer is the part that can be wrong, which is fully testable against a captured response. A deliberate deferral, not an oversight.

But "runs on a schedule" is a weaker claim for OCI than the words suggest. What is scheduled and proven is the task, the framework, the normalizer, and the run record. What is unproven is the SDK call, its pagination, and its error shapes — exactly the three things the Kubernetes collector's live path exists to exercise. **D3 is met end to end for Kubernetes and met-minus-the-live-read for OCI.** Recording it as unqualified would be redefining the criterion instead of meeting it.

### Packages

| Package | Status |
|---|---|
| 1.4.1 Collector framework, scheduling, run records | Complete. CF-1 folded in rather than patched. |
| 1.4.2 Kubernetes collector | Complete, including the live path, pagination, bounded retry, and partial-read gaps. |
| 1.4.3 Oracle Cloud collector | Complete against recorded payloads. Added the second kind. |
| 1.4.4 Partial-failure and idempotency handling | Complete. Absence semantics, run exclusion, CF-4 and CF-5. |
| 1.4.5 Phase 1 carry-forward remediation | Complete. CF-3 closed; CF-1 closed earlier, in 1.4.1. |

**All five carry-forwards are closed** — the three from Phase 1 close (CF-1 in 1.4.1, CF-2 in 1.3.1, CF-3 in 1.4.5) and the two this phase found in Phase 2 code (CF-4 and CF-5 in 1.4.4).

### Measurements, per the Step 3 plan

| Measure | Phase 3 |
|---|---|
| Tests | 124 at Phase 2 close, 268 now, plus 2 skipped that require a live cluster |
| Branch coverage on gated modules | 100% on `reconcile`, `workflow`, `intent`, and now `discovery` |
| Defects found by review | Three, all by the non-authoring model. Two were coding defects; the third was a design error, which is the more valuable catch. |
| Defects found after a phase closed | Two, both in Phase 2 code, found while designing the collector lock (CF-4, CF-5) |
| Worst kernel routine complexity | Unchanged. No kernel routine was touched this phase. |

### Three changes to how the work is done

Each comes from something that happened this phase, not from a principle.

**1. A boundary tier, named.** The review rule had two tiers: kernel gets non-authoring review, bulk does not. But CI's coverage gate covers `reconcile`, `workflow`, `intent`, *and* `discovery` — Phase 2 and Phase 3 each concluded those modules were too consequential to leave ungated. The instinct was encoded in coverage and not in review, and **CF-1 lived in exactly that gap**: `discovery/collector.py` is bulk by module and kernel by consequence.

Boundary code — the interfaces bulk code is written against, and the barricades — now gets non-authoring review like the kernel. Today that is `discovery/collector.py`, `discovery/errors.py`, `intent/documents.py`, and `reconcile/domain.py`. The adapters written against them stay bulk. This aligns the review rule with what CI already enforced.

The reasoning is the strongest finding of the phase: **for code nobody reads line by line, the protection is the interface it is written against, not the review it does not get.** CF-1 was fixed by making the mistake unavailable rather than by making the collector careful, and the evidence that this is a different class of fix is that the OCI collector — a second adapter, written later — inherited the behaviour without knowing the rule, and the reviewer tried to write an adapter that reintroduces CF-1 and could not.

**2. An enforcement column on the quality objectives.** Added above, in the quality objectives section. The collectors' robustness objective was stated from Phase 0 and violated anyway; the column makes an objective with nothing behind it visible before it becomes a defect. Two entries currently read "nothing yet" and one reads "intent only", which is the point of writing it down.

**3. Hostile-implementer tests, named as a category.** The reviewer's most valuable single action was attempting to write an adapter that reintroduces CF-1 and reporting it impossible. That was a review activity, so it happened once and left nothing behind. Several tests of this shape already exist without a name — the collector that dies mid-`fetch`, the one declaring a kind it does not produce, the one naming an unseeded kind. Naming the category means the next framework gets asked where its hostile-implementer tests are.

### Process facts, recorded rather than buried

**1.4.4 was merged before its review ran, and the review then found a real defect.** The reviewer hit a session limit and the code was merged to clear a red CI rather than wait. The defect — a deadlock escaping as `OperationalError` where only `IntegrityError` was caught — was fixed in a follow-up. Nothing was lost. The sequence is named because it worked here and is not a precedent to lean on.

**The 1.4.4 specification was written and reviewed before its implementation, and that order earned its cost once.** The review found that absence scoped by `(tenant, collector)` was sound only while a collector owns one kind — a hole in the *rule*, not the code. Under the usual order that hole would have been implemented, confirmed by tests written to match it, and handed over as a self-consistent package. Worth repeating for packages where a wrong rule is expensive; not worth it generally, because it is slower and most packages do not earn it.

**1.4.5 shipped a commit whose message described work that was not in it.** Both `ci.yml` edits were uncommitted when a `git reset --hard` discarded them, and `git add -A` then captured only the PROJECT_PLAN edit claiming CF-3 was fixed. CI went green on main **because the missing job could not fail** — this project's own named failure class, the safety net existing but not covering the thing, reproduced in the act of closing it. Caught by reading the workflow on main, not by any gate, because no gate inspects the workflow.

The transferable lesson is not about `git reset`: **verification has to run after the last edit, and a config change has to be read back out of the commit.** `pytest`, `ruff`, `mypy`, and the coverage gate all passed on a commit that did nothing it claimed to do.

---

## Phase 4 entry condition (set at Phase 3 close)

**Build the interfaces before the bulk work that sits on them.** Phase 4 carries two UI packages and a resource explorer, all bulk, all written against the API and the precedence evaluator. Those interfaces are where kernel-level care multiplies across code nobody will read line by line — the reallocation Phase 3's finding argues for, stated as a sequencing rule rather than an aspiration.

The specific instance, and the reason this is a condition rather than advice: **the precedence evaluator returns the deciding rule as part of its result type.** D6 requires the policy be explainable for any single field. A log line satisfies the words and not the requirement; a return type makes an unexplainable decision unrepresentable. That is the structural version of the objective, and the enforcement column currently reads "nothing yet" against it.

1.5.0 (null-versus-absent) already blocks 1.5.3 and 1.5.4 for a related reason: precedence cannot answer which plane is authoritative for a field while "absent" and "null" are one value.

---

# START HERE at the opening of Phase 4

> **STATUS as of 2026-07-30: every decision this section demanded has been made.** All six questions are answered in DESIGN §23, and the spec-first call is made below. **Read "Phase 4 opening decisions" at the end of this document first** — it carries the answers. This section is kept unedited as the record of what was due and why, because the reasoning for *when* a question becomes expensive is worth more than the answer it produced.
>
> The one thing below that did not survive contact: the spec-first default it proposed was drawn on code tier, and tier turned out to be the wrong predictor. See the decisions section.

Written at Phase 3 close for whoever opens Phase 4, which may be a session with no memory of this one. Nothing below is a suggestion; each item is a decision that must be made *before* the code that depends on it, and each one has a reason it cannot be deferred again.

Phase 4 is the largest phase and the whole of the critical path: matching, diff, precedence, discrepancy lifecycle, and two UIs. It is also where four questions deliberately left open finally come due, because Phase 4 is the thing that needed them.

## 1. Answer the four open questions that Phase 4 makes due

DESIGN §23 lists these as "open, but not needed until phase 4". That phase is now. Answer them in DESIGN before writing the code that assumes an answer, because each one is a shape decision rather than a detail.

| Question | Why it blocks | Blocks |
|---|---|---|
| **§23.2** Does a discrepancy attach to a field, a resource, or both? | The `Discrepancy` model already carries both a `field_name` and a natural key, which is an implicit answer nobody decided. The lifecycle and the review queue both need it settled: resolving "this resource has drifted" and "this field has drifted" are different actions with different blast radii. | 1.5.4, 1.5.5 |
| **§23.6** Does a missing precedence rule default to intent-wins, or is it a hard error? | DESIGN §6 already lists this as a condition that legitimately happens and defers the response to Phase 4. A default is convenient and silent; a hard error is loud and will annoy. The quality objective for this component ranks correctness first, which argues for the error. | 1.5.3 |
| **§23.4** How much drift history is retained, and per resource or per discrepancy? | Full-rebuild projection already grows declared rows per revision, so this is now about two planes plus drift rather than drift alone. Retention is cheap to add before records are durable and expensive after. | 1.5.4 |
| **§23.5** Is the synthetic estate generator a permanent product feature or a test fixture? | 1.5.1 and 1.5.2 depend on it for adversarial data (activity F in the schedule), and "product feature" implies a UI, docs, and support that nothing has scoped. | 1.5.1 |

Also due, from this document's own "known incompleteness" note, which says these are resolved before Phase 4 rather than now:

- **What happens when an intent revision deletes a resource that has open discrepancies.** Absence semantics answered the discovered side of this in 1.4.4; the declared side is still unanswered, and full-rebuild projection means a resource can vanish from the declared plane between two commits.
- **Whether tenants may define their own kinds or only use global ones.** Kinds are data, so nothing in the schema currently prevents either.

**Not due:** DESIGN §23.8 (multi-kind collector absence). Its trigger is a collector *owning* more than one kind, and although a second kind now exists, each collector still owns exactly one. The assertion in `run_collector` fails loudly if that changes. Leave it open.

## 2. Two gates already set, which need no new decision

- **WBS 1.5.0 (null-versus-absent) completes before 1.5.3 and 1.5.4.** Precedence cannot say which plane is authoritative for a field while "absent" and "explicitly null" are one value, and once suppression makes records durable, changing the semantics makes historical records retroactively ambiguous. Rationale in the "Scheduled at the Phase 3 second-kind decision" section above.
- **The Phase 4 entry condition: build the interfaces before the bulk work that sits on them**, and specifically, the precedence evaluator returns the deciding rule as part of its result type. D6 requires explainability for any single field; a log line satisfies the words and not the requirement. Rationale in the "Phase 4 entry condition" section above.

## 3. One call to make fresh, with the phase in front of you

**Does Phase 4 use the spec-first order, and for which packages?**

Phase 3 used it once, for 1.4.4, and it earned its cost: the review of the specification found that absence scoped by `(tenant, collector)` was sound only while a collector owns one kind — a hole in the *rule*, which under the normal order would have been implemented, confirmed by tests written to match it, and handed over as a self-consistent package.

The reason not to adopt it as standing process is that it is slower and most packages do not earn it. But Phase 4 is different from Phase 3 in the way that matters: 1.5.1 through 1.5.4 are **all kernel**, where a wrong rule is expensive by definition, whereas Phase 3 was mostly bulk with one kernel-adjacent package.

So the honest answer is that the balance has shifted and the decision should be re-made rather than inherited. Deciding it at the start, per package, is the point. A reasonable default to argue against: spec-first for 1.5.3 (precedence) and 1.5.4 (lifecycle), because both encode rules that outlive their code; normal order for 1.5.1 and 1.5.2, which have the adversarial corpus in DESIGN §12 already acting as a specification written before the code.

## 4. Standing facts a new session will not know

- **Local tests need `POSTGRES_PORT=5544` and a running Docker daemon**, or they block on the connection rather than failing. `docker compose up -d postgres valkey` first.
- **Two collectors exist and each owns one kind.** Kubernetes has a live path (`DATUM_K8S_LIVE=1` to run it); OCI reads recorded payloads only, and D3 is recorded as met-minus-the-live-read for that reason.
- **The review tiers are three now, not two** — kernel, boundary, bulk. See CLAUDE.md. Boundary code gets non-authoring review, and the reason is in the Phase 3 close.
- **The quality objectives have an enforcement column.** Two entries read "nothing yet"; one of them is precedence explainability, which is Phase 4's job.

---

# Phase 4 opening decisions (2026-07-30)

Seven decisions, taken before any Phase 4 code. Full rationale for the six question-answers is in DESIGN §23, §14, and §15; this is the index and the two findings that only appeared while deciding.

| Decision | Answer | Written in |
|---|---|---|
| §23.2 Discrepancy attachment | **Field**, identity `(tenant, kind, scope, name, type, field_name)`, two partial unique indexes | DESIGN §23.2, §15 |
| §23.6 Missing precedence rule | **Undecidable discrepancy, run completes.** Not silent, not fatal | DESIGN §23.6, §6, §14 |
| §23.4 Retention | **Per discrepancy, age-based, terminal states only.** Open never pruned, history never pruned | DESIGN §23.4 |
| §23.5 Estate generator | **Fixture plus a seeding command.** No UI, no API, no support commitment | DESIGN §23.5 |
| Intent deletes a resource with open discrepancies | **System-closed to a state distinct from human resolution**, carrying actor and revision | DESIGN §23 |
| Re-added resource inherits suppressions? | **No.** Suppression is scoped to the record, not the natural key | DESIGN §23 |
| Spec-first, and for which packages | **1.5.0, 1.5.2, 1.5.3, 1.5.4.** 1.5.1 excluded | below, and DESIGN §13 |

## The first finding: six of these were one question

They were deferred separately and read as independent. They are not. Five of the seven are the same question wearing different hats — **when does a discrepancy acquire a durable identity, and what is that identity made of?** Attachment defines it. Suppression depends on it. Retention bounds it. Intent deletion tests it. Re-adding a deleted resource attacks it.

Answered one at a time in the order the START HERE section listed them, they would have produced four locally sensible answers that do not compose. The resurrection case shows why: under a natural-key identity, a deleted-then-re-added resource inherits its old suppressions *by default*, silently, and nothing in the attachment question or the retention question would have surfaced that. It is only visible once the identity rule is written down as a rule.

**The transferable part is not the answer, it is the grouping.** A list of deferred questions accumulates in the order the questions were noticed, which is unrelated to the order they depend on each other. Sorting them before answering them cost one pass and changed three answers.

## The second finding: the spec-first default was drawn on the wrong axis

The START HERE section proposed spec-first for 1.5.3 and 1.5.4, with 1.5.1 and 1.5.2 taking the normal order because DESIGN §12's adversarial corpus already acts as a specification written before the code. **That reasoning does not survive reading §12 and §13 next to each other.**

§12 (matching, 1.5.1) is marked **Decided**. It carries the strategy priority table, confidence per strategy, the error-bias rule, the one-to-one Postgres constraint, and a seven-case corpus with expected outcomes. That is a specification, written before the code, already reviewed.

§13 (diff engine, 1.5.2) carries four bullets, one of which lists five comparison-semantics questions and answers none of them, and it has no corpus. **§12's corpus is about matching and does not exercise a single comparison rule.** The claim that it covered both packages was made by proximity, not by reading.

So the largest package in the phase, which this design calls the correctness kernel, was scheduled for the normal order on the strength of a specification belonging to a different package.

| Package | Spec state before this decision | Order |
|---|---|---|
| 1.5.0 Null-versus-absent | §24 names the defect; no representation decided | **Spec-first**, short |
| 1.5.1 Matching | §12 Decided — rules, confidence, corpus with outcomes | Normal |
| 1.5.2 Diff engine | §13 — five open questions, no corpus | **Spec-first** |
| 1.5.3 Precedence | §14 TO WRITE | **Spec-first** |
| 1.5.4 Lifecycle | §15 TO WRITE | **Spec-first** |
| 1.5.5, 1.5.6 UIs | Bulk | Normal |

1.5.0 earns a short pass despite being one week and sitting first on the critical path, because its output is a **cross-language representation**: "absent" must be expressible in the `declared_value` and `discovered_value` JSONB columns, in the API schema, and in the generated TypeScript, and `null` is already taken. This repository has a CI enum-drift check precisely because cross-language disagreement is a known failure mode here.

**The rule that replaces the tier heuristic: spec-first is earned by a package whose rules are not yet written down, not by a package's code tier.** Tier predicts how carefully code is reviewed. It does not predict whether anyone has decided what the code should do, and those are different gaps with different remedies.

## What this does not decide

The two gates set at Phase 3 close stand unchanged and needed no new decision: 1.5.0 completes before 1.5.3 and 1.5.4, and interfaces are built before the bulk work sitting on them. DESIGN §23.8 (multi-kind collector absence) remains open and not due; its trigger is still unfired, since each collector owns exactly one kind.

Nothing here schedules work. The next act is 1.5.0's specification.
