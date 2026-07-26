# Datum — Phase 0 + Phase 1 Build Design

**Date:** 2026-07-26
**Status:** Approved for implementation planning
**Scope:** Phase 0 (Foundation) and the Phase 1 vertical slice **only**. Nothing from Phase 2+ is pulled forward.
**Binding sources:** `CLAUDE.md`, `docs/DESIGN.md`, `docs/PROJECT_PLAN.md` (to be landed on disk as the first work item). Where this spec and those documents disagree, those documents win.

---

## 1. Purpose of this document

This is not a re-derivation of Datum's architecture — that is already decided in DESIGN.md and PROJECT_PLAN.md and their ADRs. This document designs the **build structure**: how Phase 0 and the Phase 1 vertical slice decompose into runnable steps, and where each local construction-quality skill is invoked so DESIGN.md's construction conventions are enforced in practice rather than merely quoted.

The one rule that governs everything below (from PROJECT_PLAN.md): **build the current slice, not the whole layer.** When in doubt, build less and ask.

---

## 2. Repository facts established during brainstorming

- `C:\Users\nicol\Datum Project` is now its own git repository (`git init`), remote `origin` = `https://github.com/peyton150-startup/Datum-Project.git` (was previously resolving to the home-directory repo whose origin is Ratchet — corrected). The remote is empty.
- This directly retires the WBS top risk for work package 1.1.2: "Repo rooted wrong so CI never runs (happened on Ratchet)."
- The three binding documents currently exist only in the originating prompt; landing them on disk is the first implementation step (§5, Phase 0, step 0).

---

## 3. Decisions locked in brainstorming

| Decision | Choice |
|---|---|
| Scope of this build | Phase 0 + Phase 1 vertical slice only; hard stop at the Phase 1 acceptance test |
| Phase 1 collector data source | Committed **JSON fixture** of Kubernetes Deployments — no live cluster dependency (permitted by PROJECT_PLAN.md for Phase 1) |
| Coordination model | **Sequential, review-gated kernel/bulk model split** per DESIGN.md §21 — *not* parallel swarm spawning. The Datum docs are binding over CLAUDE.md's swarm guidance for this repo. |
| PR labeling | Every PR labeled with tier (kernel/bulk) and authoring model, per DESIGN.md §21 |

---

## 4. Skill → construction-convention mapping

"Using the coding skills" means invoking a specific local skill at each point in the build where its guidance bites. The mapping below is the operational core of this design.

| Build concern | Skill(s) invoked | Convention enforced |
|---|---|---|
| Kind/resource schema, plane separation, JSONB-vs-typed-column split, RLS, Postgres-level constraints | `ai-postgres-backend`, `designing-data-intensive-applications` | ADR-005 promotion rule; constraints in Postgres not only Python |
| Kernel routines (matcher, diff engine, orphan detection) | `code-complete-routines`, `code-complete-control-flow` | cyclomatic complexity < 10; guard clauses over nesting; precedence/comparison as tables not if-chains |
| Naming across Python / DB / TypeScript; enums from one source | `code-complete-naming`, `code-complete-naming-types` | domain names; positive booleans; no magic values; shared state enums |
| Module boundaries and the barricade (dirty/clean zones) | `code-design`, `code-complete-design` | ADR-008; each module's single responsibility and what it hides; fan-out ≤ ~7 |
| Kernel tests + adversarial corpus | `code-complete-quality-testing-debugging` | basis testing (both branches of every decision), boundaries below/at/above, break-it tests, branch coverage |
| Non-authoring-model kernel review | `code-complete-quality-testing-debugging` (one-page checklist) | the DESIGN.md review checklist applied to every kernel PR |
| Any defect encountered during the build | `code-complete-debugging-refactoring-tuning` | reproduce reliably first; fix cause not symptom; test a nearby non-triggering case |

Distributed-systems skills (`distributed-data-systems`, `distributed-systems-ddia`) are **available but not required** for Phase 1; the slice is single-node. They are noted so later phases can reach for them.

---

## 5. Phase 0 — Foundation (Bulk tier)

**Objective:** an empty but production-shaped skeleton where CI is green and the smoke harness runs, before any domain code.

**Step 0 — Land binding docs.** Commit `CLAUDE.md` (repo root) and `docs/DESIGN.md`, `docs/PROJECT_PLAN.md`. Split the ADRs into `docs/adr/ADR-001…ADR-008.md`, writing up 001, 004, and 008 in full (they are inherited before modeling code); 002/003/005/006/007 as stubs with their decided one-liners.

**Step 1 — Repo scaffold.** Monorepo layout:
- `datum/` — Django project (apps: `kinds`, `graph`, `intent`, `discovery`, `reconcile`, `workflow`, `api`, matching DESIGN.md §3 module boundaries)
- `web/` — React + TypeScript + Vite + Tailwind
- `fixtures/` — the recorded Kubernetes Deployment JSON and the intent Git fixture
- `docs/`, `docs/adr/`, `docs/superpowers/specs/`

**Step 2 — Compose stack.** `docker-compose.yml`: PostgreSQL, Valkey, Django app, Celery worker + beat, Caddy. One-command local bring-up. Named ports chosen to avoid host collisions (Ratchet lesson).

**Step 3 — Tooling gates.** `pyproject.toml` with `ruff` (format + lint + C901 max-complexity 10), `mypy --strict` scoped to `reconcile`, `intent`, `graph`; `pytest` + branch coverage. `pre-commit` config. `eslint` + `prettier` + `strict: true` for `web/`.

**Step 4 — CI.** GitHub Actions workflow failing on: unformatted code, lint errors, mypy errors in kernel modules, complexity > 10 in the kernel, coverage below threshold on gated modules.

**Step 5 — Smoke harness stub.** A single test entrypoint that will grow into the Phase 1 acceptance test; initially asserts the stack imports and the DB migrates.

**Phase 0 exit (milestone M1):** CI green on an empty skeleton; `docker compose up` brings the stack up; smoke harness runs.

---

## 6. Phase 1 — Vertical slice

The narrowest path that touches every architectural layer once: **one kind, declared in Git, discovered by one collector, matched, diffed, and reviewed in the UI.** Built in the seven runnable steps below; each ends in something runnable and the next does not start until the current one runs.

**Step 1 — Kind + resource schema (Bulk).** `Kind` table (name, attribute schema). `declared_resource` and `discovered_resource` as **two separate physical tables** (DESIGN.md §5), each carrying the ADR-005 global core columns (`tenant_id`, `kind`, `name`, `scope`, `provider_id`, timestamps) plus a JSONB attribute bag. `tenant_id` present from the first migration (RLS deferred to Phase 5). Seed a single Deployment kind. Constraints enforced in Postgres.

**Step 2 — Minimal collector (Bulk).** Reads Deployments from the committed JSON fixture, normalizes provider shape → kind shape at the boundary (ADR-008 dirty zone), writes `discovered_resource` rows, records a collector run with counts. **Idempotent:** running twice produces the same rows, not duplicates. Never writes to the declared plane.

**Step 3 — Intent ingestion (Bulk, kernel-adjacent validation).** A Git fixture repo containing one Deployment declared as a document. Ingest → immutable intent revision → project into `declared_resource`. A malformed document fails validation whole and leaves the previous revision active (no partial state). Every declared resource traces to its commit. Validation lives at the barricade.

**Step 4 — Matching (Kernel).** Match the declared Deployment to the discovered one by natural key (kind, tenant, scope, name). Write a `match` row carrying `strategy`, `confidence`, `state` (DESIGN.md §12) even though every Phase 1 match is high-confidence. One-to-one constraint enforced in Postgres on both sides.

**Step 5 — Diff engine (Kernel).** Compare the matched pair field by field → field-level discrepancies, plus orphan discrepancies for anything unmatched on either side (declared-not-discovered and discovered-not-declared are distinct types). **Deterministic:** identical input → identical discrepancy set, proven by a property-based test. The adversarial corpus (DESIGN.md §12 table) and boundary/break-it cases are written **before** the matcher and diff code. No routine exceeds complexity 10. Reviewed by the non-authoring model against the checklist.

**Step 6 — Read-only API (Bulk).** Endpoints to list resources and list discrepancies, tenant-scoped, paginated. django-ninja typed schemas.

**Step 7 — Review queue UI (Bulk).** One screen: discrepancy list with declared and discovered values side by side, the authoritative side unmistakable, and a keyboard-operable action to mark a discrepancy resolved. Phase 1 discrepancy states are only `open` and `resolved`. No bulk actions.

---

## 7. Phase 1 acceptance test (definition of done)

Automated where possible, written manual script otherwise. The slice is done when this passes end to end:

1. A Git repo declares one Deployment `web` in scope `default` with `replicas: 3`.
2. Ingestion projects it into the declared plane, traceable to the commit.
3. The collector fixture reports the same Deployment with `replicas: 5`.
4. A collector run projects it into the discovered plane.
5. Matching links the two by natural key, writing a high-confidence match.
6. The diff engine produces **exactly one** field-level discrepancy: `replicas`, declared 3, discovered 5. No orphans.
7. The discrepancy appears in the review queue UI with 3 and 5 shown and the declared side marked authoritative.
8. Marking it resolved moves it out of the open queue.
9. Re-running the diff on unchanged input produces the identical discrepancy set (determinism).

Negative checks:
- A malformed intent document is rejected and the previous revision stays active.
- A Deployment present in discovery but absent from intent → one "discovered, undeclared" orphan.
- A Deployment present in intent but absent from discovery → one "declared, missing" orphan.

**Additional definition-of-done items:** diff engine and matcher meet the kernel bar (branch-covered, complexity < 10, non-authoring-model review); the daily build runs the acceptance test as its smoke test; DESIGN.md §24 ("how this design could fail") is revisited with real code in hand; no later-phase scope pulled forward.

---

## 8. Explicitly out of scope for this build

Second kind, second collector, second screen; precedence policy; discrepancy suppression/acknowledgement/full lifecycle (only `open`/`resolved` in Phase 1); change history/audit beyond what the lifecycle needs; multi-tenancy **enforcement** and roles (column present, RLS deferred to Phase 5); authentication; a polished synthetic estate generator (a minimal fixture suffices). Any of these appearing in Phase 1 is a defect against this design.

---

## 9. How this build could go wrong (early-warning signs to watch)

Carried from DESIGN.md §24, to be revisited at Phase 1 close:
- The schema-defined-kind bet fails — early warning: adding the *second* kind (Phase 3) would need a migration.
- Identity matching is the real project and the diff engine is the easy part — early warning: the adversarial corpus keeps growing after the matcher is written.
- Repo/CI foundation regresses — early warning: CI not actually running on push (the Ratchet failure); verified green at M1 before any domain code.
