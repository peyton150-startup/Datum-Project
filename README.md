# Datum

A self-hosted source of truth for a cloud and Kubernetes estate.

Datum holds **declared intent** in Git, ingests **discovered state** from providers on a schedule, and drives every difference between the two through an auditable reconciliation workflow. It is **read-only toward the estate**: it never applies, provisions, or remediates anything.

---

## The problem

> A team cannot answer "is our infrastructure actually what we think it is," and the documents that claim to answer it are wrong within weeks of being written.

The naive fix — importing discovered state over the record — destroys the curated intent that made the record valuable in the first place. Datum's answer is to refuse to merge the two. Declared and discovered state live in **separate tables that no query can accidentally blend**, and the gap between them is promoted to a first-class entity: a `Discrepancy` with a lifecycle, an owner, a suppression reason, and an expiry. Drift is stored, typed, assigned, resolved, and audited — never silently closed by an importer.

## How it works

```
   Git repo                         Provider APIs
 (intent docs)                  (Kubernetes, Oracle Cloud)
       │                                   │
       │ poll HEAD on a schedule           │ scheduled collector run
       ▼                                   ▼
  ┌──────────┐                       ┌───────────┐
  │  intent  │  validate → revision  │ discovery │  normalize → run record
  └────┬─────┘                       └─────┬─────┘
       │ project                           │ project
       ▼                                   ▼
 declared_resource                  discovered_resource
       └─────────────┬─────────────────────┘
                     ▼
                ┌──────────┐
                │reconcile │  match (natural key) → diff → discrepancies
                └────┬─────┘
                     ▼
          ┌─────────────────────┐
          │ api  →  review queue│
          └─────────────────────┘
```

1. **Intent** is authored as declarative YAML in a Git repository. A Celery beat task polls the repo; when `HEAD` moves, the documents are validated as a whole set and projected into a new immutable `IntentRevision`. A bad revision is rejected entirely and the previous one stays active.
2. **Discovery** runs collectors against provider APIs on a schedule, recording each run with counts of what was read, written, and failed. A partial read is a valid outcome, never a signal that resources vanished.
3. **Reconciliation** matches discovered resources to declared ones by natural key, then compares them field by field, producing field-level discrepancies plus orphan discrepancies for anything unmatched on either side.
4. **The review queue** shows each discrepancy with declared and discovered values side by side, the authoritative side unmistakable, fully keyboard operable.

## Differentiation from NetBox

This is a scope requirement, not a disclaimer. Each row is a design decision that changes the architecture.

| Dimension | NetBox | Datum |
|---|---|---|
| Estate | Racks, devices, interfaces, cables, circuits, IPAM | Cloud and Kubernetes resources. No physical modeling. |
| Where intent lives | Records edited in a UI; the database is the origin | Declarative documents in Git, reviewed by pull request. The database is a projection of a commit, never the origin. |
| Data model shape | Fixed, richly specific models per device concept | A typed resource graph with schema-defined kinds. A new kind is data, not a migration. |
| Discovered data | Imported, with the known risk of overwriting curated records | Never written into the intent projection. Stored separately, only ever compared. |
| The diff | A changelog recorded after the fact | A first-class `Discrepancy` entity with lifecycle, assignee, suppression reason, expiry |
| Conflict handling | Largely human convention | An explicit, versioned, per-field precedence policy the engine evaluates and can explain |
| Direction | Increasingly bidirectional in commercial editions | Strictly read-only toward the estate, permanently and by design |

The schema was derived from first principles; NetBox source was deliberately not read while designing it.

---

## Current status

**Phase 2 complete.** The end-to-end vertical slice is built and green; Phase 3 (Discovery) has not started.

| | State |
|---|---|
| **Phase 0** — repo, Compose stack, CI, pre-commit, test harness | ✅ complete |
| **Phase 1** — resource graph, minimal collector, matcher, diff engine, read-only API, review queue | ✅ complete |
| **Phase 2** — intent document format, validator, Git polling, immutable revisions, projection, line-level errors | ✅ complete |
| **Phase 3** — collector framework, Kubernetes and Oracle Cloud collectors, partial-failure semantics | ⬜ not started |
| **Phase 4** — matching with confidence, precedence policy, discrepancy lifecycle, review queue and resource explorer UI | ⬜ not started |
| **Phase 5** — auth, roles, tenant isolation, deploy, TLS, backups, public demo | ⬜ not started |

**What actually runs today**, end to end, as an automated acceptance test:

1. A Git repo declares one Deployment `web` in scope `default` with `replicas: 3`.
2. Ingestion validates it and projects it into the declared plane, traceable to the commit.
3. A collector reads the same Deployment from a recorded fixture reporting `replicas: 5`.
4. Matching links the two by natural key at high confidence.
5. The diff engine produces exactly one field-level discrepancy — `replicas`, declared 3, discovered 5 — and no orphans.
6. The discrepancy appears in the review queue with both values and the declared side marked authoritative; pressing `r` resolves it.
7. Re-running the diff on unchanged input produces an identical discrepancy set.

Plus the negative checks: a malformed document is rejected and the previous revision stays active; a Deployment discovered but not declared yields one `discovered_undeclared` orphan; declared but not discovered yields one `declared_missing` orphan.

**Deliberately not built yet:** a second kind, a second collector, precedence policy, discrepancy suppression/acknowledgement, change history beyond the lifecycle, multi-tenancy enforcement, and authentication. The schema carries `tenant_id` from the first migration and every query is written tenant-scoped, but isolation is not yet enforced.

---

## Architecture

### Module boundaries

Each module has one responsibility and an explicit list of what it must not do.

| Module | Single responsibility | Must not |
|---|---|---|
| `datum/kinds` | Define and validate resource kind schemas | Know about planes, matching, or diffs |
| `datum/graph` | Persist and query declared resources | Decide what is authoritative |
| `datum/intent` | Turn commits into immutable revisions and project them | Touch the discovered plane |
| `datum/discovery` | Run collectors, record runs, normalize provider shapes | Write to the declared plane, ever |
| `datum/reconcile` | Match, diff, apply precedence, emit discrepancies | Read from providers or Git directly |
| `datum/workflow` | Discrepancy lifecycle, suppression, audit | Recompute diffs |
| `datum/api` | Serve the read model | Contain business rules |

### Core concepts

- **Kind** — a schema-defined resource type. Data, not code. Adding one is a row plus a fixture, not a migration.
- **Declared plane** — the projection of an intent revision. Origin is Git.
- **Discovered plane** — the projection of a collector run. Origin is the estate.
- **Intent revision** — an immutable snapshot tied to a commit, unique on `(tenant_id, commit_sha)`.
- **Collector run** — an immutable snapshot tied to a schedule execution, with read/written/error counts.
- **Match** — a link between a declared and a discovered resource, carrying strategy, confidence, and state.
- **Discrepancy** — a difference between the planes, with a lifecycle.
- **Natural key** — `(kind, tenant_id, scope, name)`. `scope` is the provider-neutral word for a Kubernetes namespace or an OCI compartment.

### The barricade

Datum has an explicit trust boundary (ADR-008). Outside it, all data is hostile; inside it, code may assume its inputs are valid.

| Zone | What is there | Discipline |
|---|---|---|
| Dirty | Provider API responses, Git document contents, HTTP request bodies | Validate everything. Convert to domain types at the boundary, immediately. |
| Clean | `graph`, `reconcile`, `workflow` | Inputs are already typed and valid. Assertions are for conditions that indicate a bug. |

Provider dicts and YAML never travel inward raw — `discovery/kubernetes.py` and `intent/documents.py` are the two barricades, and both emit the same domain type (`ResourceSnapshot`). Assertions carry no side effects and are never used for conditions that can legitimately happen.

### Data model

Kinds are data, not a model per type. Every resource of every kind shares one set of typed columns — `tenant_id`, `kind`, `name`, `scope`, `provider_id`, timestamps, and the match/state foreign keys — with kind-specific fields in JSONB. A field earns a typed column only if a query filters, sorts, joins, or constrains on it (ADR-005). Promotion is a deliberate migration, and a promoted field leaves JSONB entirely: a field stored in two places drifts, which is the exact bug this product exists to catch.

Constraints live in Postgres, not only in Python:

- one active revision per tenant (partial unique index on `is_active`)
- one revision per `(tenant_id, commit_sha)` — the idempotency key for ingestion
- declared natural key unique per revision; discovered natural key unique per tenant
- matching is one-to-one, enforced by `OneToOneField` on both sides

### The intent document format

Intent documents speak **Datum's vocabulary, not a provider's** — a direct consequence of kinds being data. A Deployment and an OCI compute instance share one envelope and differ only in `kind` and the contents of `attributes`.

```yaml
apiVersion: datum.dev/v1
kind: Deployment          # names a Kind row, not a Kubernetes kind
metadata:
  name: web
  scope: default
attributes:
  replicas: 3
```

`provider_id` is **forbidden** in intent: intent is authored before the resource exists, so it cannot know the provider's identifier. A document carrying one is rejected rather than ignored, because silently dropping it would let an author believe they had pinned an identity.

Validation runs in four ordered layers — syntax, envelope, schema, referential — and **all of them block**. Validation is whole-revision rather than fail-fast: every document is checked and every error accumulated before anything is raised, so one push surfaces every problem at once instead of one problem per push. Errors name the file and, where the parser gives a position, the line; duplicate-identity errors name **both** conflicting files.

Projection is a **full rebuild**: each accepted revision writes a complete new set of `declared_resource` rows keyed to that revision, in one transaction with one flip of `is_active`. Incremental projection was rejected because it would itself be a second diff engine — a second thing that must be correct and kernel-reviewed — to save rows the 10,000-resource ceiling does not require saving. The accepted cost is that row count grows with revisions.

### Architecture decision records

Full records in [`docs/adr/`](docs/adr/). Each carries context, options, decision, consequences, and the cost of reversing it.

| ADR | Decision |
|---|---|
| [001](docs/adr/ADR-001-schema-defined-kinds.md) | Schema-defined kinds rather than a model per resource type |
| [002](docs/adr/ADR-002-valkey-over-redis.md) | Valkey over Redis (Redis relicensed away from BSD in 2024) |
| [003](docs/adr/ADR-003-django-ninja.md) | django-ninja over Django REST Framework |
| [004](docs/adr/ADR-004-intent-in-git.md) | Intent lives in Git, not in the database |
| [005](docs/adr/ADR-005-typed-column-vs-jsonb.md) | Typed-column versus JSONB split |
| [006](docs/adr/ADR-006-discrepancy-first-class.md) | Discrepancy as a first-class entity rather than a changelog |
| [007](docs/adr/ADR-007-read-only-estate.md) | Read-only toward the estate, permanently |
| [008](docs/adr/ADR-008-barricade-error-handling.md) | Error handling strategy and the barricade boundary |
| [009](docs/adr/ADR-009-t-shaped-integration.md) | T-shaped integration, vertical slice inside phase 1 |

---

## Technology

| Layer | Choice | Note |
|---|---|---|
| Backend | Django 5.0 (BSD) | |
| API | django-ninja 1.3 (MIT) | Typed schemas pair with the TypeScript client |
| Database | PostgreSQL 16 | |
| Queue | Celery 5.4 (BSD) | Beat schedule drives intent polling |
| Broker and cache | Valkey 8 (BSD) | Chosen over Redis, which relicensed in 2024 |
| Frontend | React 18, TypeScript 5.5, Vite, Tailwind (MIT) | |
| Collectors | Kubernetes Python client, OCI SDK (Apache 2.0) | Phase 3 |
| Proxy and TLS | Caddy 2 (Apache 2.0) | |
| Runtime | Docker Compose | |
| CI | GitHub Actions | |

Every component is OSI-approved open source and free at the scale used. Rejected: Redis and Elasticsearch (relicensed), anything whose free tier is a commercial trial, anything whose self-hosted edition is feature-crippled.

---

## Running it

### With Docker Compose

```bash
cp .env.example .env      # then edit DATUM_INTENT_REPO_URL if you have one
docker compose up --build
```

This brings up Postgres, Valkey, the Django app, a Celery worker with beat, and Caddy. The app is on `http://localhost:8000`, the API under `/api/`, and the OpenAPI docs at `/api/docs`.

Host-side ports are deliberately non-standard to dodge collisions with a native install: Postgres is on **5544** and Valkey on **6399**.

Apply migrations (this also seeds the `Deployment` kind):

```bash
docker compose exec app python manage.py migrate
```

### Local development

```bash
python -m venv .venv && .venv/Scripts/activate     # or source .venv/bin/activate
python -m pip install -e ".[dev]"
pre-commit install

docker compose up -d postgres valkey               # tests need a live database
export POSTGRES_PORT=5544                          # match the Compose host port
python manage.py migrate
python manage.py runserver
```

> **Note:** the test suite talks to a real Postgres. With `POSTGRES_PORT` unset (defaulting to 5432) or the Docker daemon down, `pytest` blocks on the connection rather than failing fast.

Frontend:

```bash
cd web
npm ci
npm run dev        # Vite dev server
npm run build      # runs tsc, then vite build
npm test           # vitest
npm run lint       # eslint, zero warnings tolerated
```

### Configuration

All configuration is environment-driven; see [`.env.example`](.env.example).

| Variable | Default | Meaning |
|---|---|---|
| `DATUM_SECRET_KEY` | dev placeholder | Django secret key |
| `DATUM_DEBUG` | `1` | Django debug mode |
| `DATUM_ALLOWED_HOSTS` | `*` | Comma-separated allowed hosts |
| `POSTGRES_DB` / `_USER` / `_PASSWORD` / `_HOST` / `_PORT` | `datum` / `datum` / `datum` / `localhost` / `5432` | Database connection |
| `VALKEY_URL` | `redis://localhost:6379/0` | Celery broker |
| `DATUM_INTENT_REPO_URL` | *(empty)* | Intent repository. **Empty disables polling entirely** — the task logs and does nothing rather than failing every interval. |
| `DATUM_INTENT_REPO_BRANCH` | `main` | Branch to track |
| `DATUM_INTENT_WORKTREE` | `<repo>/.intent-worktree` | Local disposable checkout |
| `DATUM_INTENT_POLL_SECONDS` | `300` | Poll interval. This is the bounded staleness: drift between a push and its revision is at most one interval. |

The intent worktree is a **cache of the remote**, never a source of truth. Sync is clone / fetch / hard reset — nothing pushes, commits, or writes into it, and any local edit is discarded on the next sync.

### The API

| Endpoint | Purpose |
|---|---|
| `GET /api/resources?plane=declared\|discovered&offset=` | List resources in either plane, tenant-scoped, paginated (50/page). The declared query reads **through the active revision** — full-rebuild projection means an unfiltered read would see every revision at once. |
| `GET /api/discrepancies?state=open&offset=` | List discrepancies by state, ordered deterministically |
| `POST /api/discrepancies/{id}/resolve` | Mark a discrepancy resolved |
| `GET /api/docs` | OpenAPI schema and browser |

State enums are generated from one source: `scripts/gen_ts_enums.py` reads `datum/enums.py` and writes `web/src/enums.ts`, so a Python enum and its TypeScript counterpart cannot drift.

---

## How this was built

The process is as deliberate as the architecture, and the documents came first.

### Documentation-driven, in this order

[`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) fixes the scope, the deliverables with acceptance criteria, the work breakdown, the critical path, and the exclusions. [`docs/DESIGN.md`](docs/DESIGN.md) fixes the architecture, data model, module boundaries, error-handling strategy, test strategy, and the construction conventions. Both are **binding, not background**, and both are updated when the code disagrees with them rather than left to rot. [`CLAUDE.md`](CLAUDE.md) is the working agreement that keeps an AI-assisted build inside those boundaries.

One finding from the critical-path analysis changed the build order: the critical path runs through **intent ingestion, not discovery**. Every discovery activity carries roughly twelve weeks of slack. Discovery is the part that feels like the real work, and it is the part scheduled second.

### T-shaped integration

Phase order reads layer by layer, but the build is **vertical**: one kind, one collector, one screen, end to end, then widen (ADR-009). Following the layer order literally would have built the whole graph layer, then the whole intent layer, and discovered at phase 4 that the model was wrong. Building breadth ahead of the current slice is treated as a defect, not initiative.

### Two tiers of code

**Kernel** — `reconcile` (matching, diff, precedence) and `workflow` (discrepancy lifecycle). Correctness is the top priority; a wrong result is worse than no result. Kernel code has cyclomatic complexity under 10 per routine enforced in CI, branch coverage with boundary cases and at least one case designed to break it, determinism tested as an invariant, and a review against the DESIGN checklist **by the model that did not write it** before merge.

**Bulk** — scaffolding, CRUD, collectors, UI components, fixtures. Robustness and clarity matter; the kernel ceremony does not fully apply. Still formatted, linted, typed, and tested.

Every PR is labeled with its tier and its authoring model.

### Quality objectives, ranked per component

Quality attributes trade off, so they are ranked rather than all maximized. The ranking differs by component, which is the point.

| Component | First priority | Explicitly sacrificed |
|---|---|---|
| Diff engine, matching, precedence | **Correctness.** A wrong discrepancy is worse than none — it teaches the operator to distrust the queue. | Robustness. On malformed input it refuses to produce a result rather than guessing. |
| Collectors | **Robustness.** A cloud API will return junk, time out, and rate-limit. The collector keeps going and records what it could not read. | Strict correctness. A partial run is a valid outcome. |
| Intent ingestion | **Correctness.** A bad revision is rejected whole; it never half-applies. | Convenience. Validation is strict and will annoy the author. |
| Web UI | **Usability.** The review queue is the product. | Flexibility. Fewer options, one good path. |
| Whole system | **Understandability.** A reader has fifteen minutes. | Performance. Nothing is tuned until measured. |

### Adversarial review that found real defects

Kernel work is reviewed by the model that did not author it, with reproduction scripts rather than opinions. The Phase 2 cycle is representative: the reviewer found two moderate issues; fixing them surfaced a third and worse one — `git rev-parse HEAD` **searches upward**, so pointing ingestion at a directory that was not a checkout silently returned an ancestor repository's HEAD. Every resource in that revision would have traced to a commit from an unrelated repo, breaking resource traceability outright. `repository.py` now verifies the path is a repository root and refuses otherwise.

### Defects are carried forward in writing, not forgotten

Phase 1 closed with three known defects, each reproduced rather than suspected, each recorded with its owning module, its mitigation while deferred, and its fix direction. One (CF-2, duplicate declared identity caught by a database constraint instead of the validator — inverting the barricade) was folded into Phase 2 when that phase rewrote the validator anyway. The other two are listed under [Known limitations](#known-limitations) below.

---

## Testing and CI

```bash
pytest                                        # 124 tests, branch coverage
pytest tests/test_acceptance_slice.py -v      # the end-to-end smoke test
ruff format --check . && ruff check .         # format, lint, complexity
mypy datum/reconcile datum/intent datum/graph # strict typing on kernel + boundary
cd web && npm test && npm run build && npm run lint
```

### The testing bar

Tests that try to break the code are preferred over tests that confirm it works, because the natural bias runs the other way. Hand-verifiable test data over realistic-looking noise. Boundaries tested just below, at, and just above. Branch coverage, not statement coverage. Test code is reviewed as carefully as production code.

The matcher and diff engine are validated against an **adversarial corpus** written before the code: resource renamed with provider ID stable, resource recreated with a new ID, two resources swapping names (must never produce a crossed match), a resource moving scope, the same name in two scopes, and each orphan direction. Determinism is a property-based test — identical inputs must produce an identical discrepancy set regardless of input ordering.

### CI gates

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main` and every pull request against a real Postgres 16 service, and fails on:

- unformatted code (`ruff format --check`)
- lint errors or cyclomatic complexity ≥ 10 (`ruff check`, C901 with `max-complexity = 9`)
- type errors in `datum/reconcile`, `datum/intent`, `datum/graph` (`mypy --strict` via per-module overrides)
- any test failure
- the Phase 1 acceptance slice failing as a smoke test
- **branch coverage below 100%** on `datum/reconcile/*`, `datum/workflow/*`, `datum/intent/*`

`datum/intent` is gated alongside the kernel because the quality objectives rank intent ingestion correctness-first and it is the barricade for the declared plane.

Formatting and linting are settled by tools and never discussed in review. If the formatter has an opinion, it is right.

---

## Repository layout

```
datum/              Django project
  kinds/            Kind schemas (data, not code)
  graph/            DeclaredResource
  intent/           documents.py (validator/barricade), repository.py (git),
                    ingest.py (projection), tasks.py (celery beat poll), errors.py
  discovery/        collector.py, kubernetes.py (provider barricade), run records
  reconcile/        domain.py (ADTs), matcher.py, diff.py  ← the kernel
  workflow/         discrepancy lifecycle (Phase 4)
  api/              django-ninja read model
  enums.py          single source of truth for state enums
docs/
  DESIGN.md         architecture, data model, conventions, review checklist
  PROJECT_PLAN.md   scope, WBS, critical path, acceptance criteria, carry-forwards
  adr/              nine architecture decision records
tests/
  kernel/           diff and matcher, held to the kernel bar
  test_acceptance_slice.py   the end-to-end smoke test
fixtures/           intent repos and recorded provider payloads
scripts/            gen_ts_enums.py, wait-for-postgres.sh
web/                React + TypeScript review queue
```

---

## Known limitations

Stated plainly rather than discovered later. All are deliberate, all are tracked in [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md).

- **CF-1 — the collector drops good records on one bad record.** `_read` wraps the whole-file parse in one `try`, so a single malformed record discards every healthy record in the same payload *and* under-reports `resources_read`. This directly contradicts the collector's robustness-first objective. Scheduled for Phase 3 (WBS 1.4.4/1.4.5). **Mitigation until then: single-record fixtures only — do not point the collector at a multi-record source.**
- **CF-3 — CI does not build or test `web/`.** No Node setup, no `npm ci`, no `npm test`. The generated `web/src/enums.ts` can therefore drift from `datum/enums.py` with CI green. Scheduled for Phase 3 (WBS 1.4.5). **Mitigation until then: run `npm run lint`, `npm run build`, and `npm test` by hand before any `web/` change.**
- **One kind, one collector.** Only `Deployment` is seeded, and the collector reads a recorded JSON fixture rather than a live cluster.
- **Optional attributes are not expressible.** Every key in a kind's `attribute_schema` is required and unknown keys are rejected. Related: the diff engine *compares* an absent key and an explicit `null` distinctly but *reports* both as `None`. Both simplifications are held deliberately and revisited together when a second kind introduces optional fields — that is the point at which they become correctness bugs rather than simplifications.
- **No authentication, no tenant isolation enforcement.** The schema carries `tenant_id` and every query is written tenant-scoped, but `DEFAULT_TENANT_ID` is a constant and row-level security is Phase 5.
- **No precedence policy and no discrepancy suppression.** Discrepancies have only `open` and `resolved`; the authoritative plane is hard-defaulted to declared. The versioned, explainable per-field policy is Phase 4.
- **Multi-document YAML streams are rejected.** One file declares one resource. Revisit when a real repository makes that annoying, not before.
- **Designed for 10,000 resources and 20 tenants.** Beyond that is explicitly out of scope.

## Roadmap

| Phase | Work |
|---|---|
| 3 | Collector framework and run records; Kubernetes collector; Oracle Cloud collector; partial-failure and idempotency semantics; CF-1 and CF-3 remediation |
| 4 | Identity matching with confidence and stored bindings; diff engine widening; precedence policy model and explainable evaluator; discrepancy lifecycle and audit trail; review queue and resource explorer UI |
| 5 | AuthN and roles; tenant isolation with a test proving a cross-tenant read fails; deploy with TLS; automated backups with a tested restore; public demo seeded with drift |
| Expansions | Drift timeline visualization; webhook notifications; an intent linting CLI on PyPI; a third collector |

Each expansion is rolling-wave: one at a time, each with its own design doc and branch, none before the core build closes.

## License

Apache 2.0. Every dependency is OSI-approved open source and free at the scale used — a hard project constraint, not a preference.
