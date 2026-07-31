# CLAUDE.md

Guidance for Claude Code working in the Datum repository. Read this first, every session.

## What Datum is

A self-hosted source of truth for a cloud and Kubernetes estate. It holds declared intent in Git, ingests discovered state from providers on a schedule, and drives every difference between the two through an auditable reconciliation workflow. It is read-only toward the estate: it never applies, provisions, or remediates anything.

## Read before writing code

These documents are binding, not background. Read the ones relevant to your task before starting.

- **`docs/PROJECT_PLAN.md`, the `START HERE at the opening of Phase 4` section at the very end. Read this first.** Phases 0 through 3 are closed. That section lists the decisions that must be made before Phase 4 code is written — four open questions that Phase 4 makes due, two gates already set, and one call to make fresh rather than inherit. Do not start Phase 4 work without it.
- `docs/DESIGN.md` — architecture, data model, module boundaries, per-component quality objectives, the ADRs, and the construction conventions (naming, routines, control flow, error handling, the review checklist)
- `docs/PROJECT_PLAN.md` — scope, phase order, deliberate exclusions, the per-phase close-outs, and the enforcement column on the quality objectives

## The rules that override instinct

**Build the current slice, not the whole layer.** The phase plan reads layer by layer, but the build is vertical: one kind, one collector, one screen, end to end, then widen. Do not build breadth ahead of the slice you are on. The phase 1 slice is closed and widened — two kinds and two collectors now exist — so the scope boundary that binds is the current phase's, and the current phase is 4.

**When in doubt, build less, and ask.** If a task needs a decision the docs do not cover, stop and ask rather than inventing scope. Pulling a later phase forward is a defect, not initiative.

**Read-only toward the estate is permanent.** No code path writes to, provisions, or changes any provider resource. A collector that mutates the estate, or writes to the declared plane, is wrong by definition.

**The barricade (ADR-008).** Validate all external data at the boundary and convert it to domain types there. Provider dicts, Git document contents, and request bodies never travel inward raw. Interior code (`graph`, `reconcile`, `workflow`) trusts its inputs and uses assertions for impossible conditions. Assertions never contain side effects.

## Two tiers of code

**Kernel** — `reconcile` (matching, diff, precedence), `workflow` (discrepancy lifecycle). Correctness is the top priority; a wrong result is worse than no result. Kernel code:
- has cyclomatic complexity under 10 per routine, enforced in CI
- is branch-covered, with boundary cases and at least one case designed to break it
- has determinism tested as an invariant where it applies (identical input, identical output)
- is reviewed against the checklist in DESIGN.md by the model that did not write it, before merge

**Boundary** — the interfaces bulk code is written against, and the barricades: `discovery/collector.py`, `discovery/errors.py`, `intent/documents.py`, `reconcile/domain.py`. Bulk by module, kernel by consequence. Gets non-authoring review and the 100% branch gate; does not need the determinism rule, which only applies where there is a result to be deterministic about.

Added at Phase 3 close, because CF-1 lived in exactly the gap between the other two tiers. **For code nobody reads line by line, the protection is the interface it is written against, not the review it does not get.** CF-1 was fixed by making the mistake unavailable rather than by making the collector careful, and the second collector then inherited the fix without knowing the rule existed. That is where care spent here multiplies.

**Bulk** — scaffolding, CRUD, collectors and their normalizers, UI components, fixtures. Robustness and clarity matter; the kernel ceremony does not fully apply. Still formatted, linted, typed, and tested, just not line-by-line reviewed.

Label each PR with which tier it is and which model authored it.

When writing or changing a boundary interface, say in the PR **what mistake it makes unavailable**. If the answer is "none", it is documentation rather than a boundary, and the code written against it is protected by nothing.

## Non-negotiable tooling

Formatting and linting are settled by tools, never discussed in review. CI fails on: unformatted code, lint errors, type errors in kernel modules (`mypy --strict`), complexity over 10 in the kernel, and coverage below threshold on gated modules. If the formatter has an opinion, it is right.

## Naming and structure, in one breath

Names come from the problem domain, describe what a thing is not how it is stored, read as positive boolean statements, and never differentiate by number. Routines do one thing their name fully describes; no `handle`, `process`, `manage`, or `do`. Guard clauses over nesting; nesting past three levels is a smell. Repeated branching on one value is a table, not an if-chain, which applies with force to precedence rules and comparison semantics. Magic values are named constants. State machines use enums shared across Python, the database, and TypeScript from one source. Full detail is in the construction conventions in DESIGN.md.

## Testing bar

Prefer tests that try to break the code over tests that confirm it works; the natural bias runs the other way. Hand-verifiable test data over realistic-looking noise. Boundaries tested just below, at, and just above. Branch coverage, not statement coverage. Review test code as carefully as production code. For the diff engine and matcher, the adversarial corpus in DESIGN.md section 12 is part of the definition of done.

## Debugging

Reproduce reliably before hypothesizing. Fix the cause, not the symptom; a special case bolted next to a bug means the logic still does not handle that input generally. Test the fix plus a nearby case that should not trigger it. Look for the same mistake elsewhere before moving on. Under time pressure, slow down: most first-attempt fixes are wrong.

## When you finish a piece of work

- Run the daily build and its smoke test locally.
- Confirm no later-phase scope was pulled forward.
- If you are closing a phase, walk its acceptance criteria in writing and state any not met plainly, rather than redefining them.

## WBS 1.5.2: Diff Engine Implementation Status

**Overall Status:** Phase 2C complete. Phases 2D–2J remain.

**Completed:**
- ✅ Phase 2A: Schema Validation (52 tests, 99% coverage) — PR ready
- ✅ Phase 2B: Numeric Comparison (44 tests, 96% coverage) — PR ready
- ✅ Phase 2C: String Comparison (44 tests) — PR ready

**Remaining Phases (in order):**
1. **Phase 2D: List Comparison** (3 hours)
   - Modes: ordered, unordered_multiset, set
   - 25+ test cases covering duplicates, empty lists, nulls, nested lists
   - File: `datum/reconcile/comparison.py` — add `compare_list()` and 4 helpers
   - File: `tests/kernel/test_comparison_list.py` — create with 25+ tests

2. **Phase 2E: Timestamp Comparison** (2 hours)
   - Modes: string (exact), semantic_utc, semantic_resource_tz
   - Precision levels: day, hour, minute, second
   - 20+ test cases with timezone handling
   - File: `datum/reconcile/comparison.py` — add `compare_timestamp()` and 3 helpers
   - File: `tests/kernel/test_comparison_timestamp.py` — create with 20+ tests

3. **Phase 2F: Object Comparison** (3 hours)
   - Modes: opaque (hash), version (extract field), identity (extract id), ignore (always match), recurse(depth)
   - 20+ test cases with nested objects, key order independence
   - File: `datum/reconcile/comparison.py` — add `compare_object()` and 5 helpers
   - File: `tests/kernel/test_comparison_object.py` — create with 20+ tests

4. **Phase 2G: Logging Infrastructure** (2 hours)
   - AuditLogEntry structure already defined in comparison.py
   - Implement 3 logging levels: debug (all), discrepancy (mismatches only), sampled_audit (every Nth)
   - File: `datum/reconcile/comparison.py` — add `_write_audit_log()` and logging config

5. **Phase 2H: Integration & Refactoring** (2 hours)
   - Update `_field_discrepancies()` in `datum/reconcile/diff.py` to use `compare_field()` dispatcher
   - Update `reconcile()` to accept `schema_map` parameter
   - Add `_load_comparison_schemas()` to `datum/reconcile/service.py`
   - Update `run_reconciliation()` to pass schema to reconcile()
   - Handle MissingFieldConfig gracefully (log error, treat as discrepancy)
   - Files: `datum/reconcile/diff.py`, `datum/reconcile/service.py`

6. **Phase 2I: Adversarial Corpus Tests** (5 hours)
   - 150+ test cases from DIFF_SEMANTICS.md across all 5 types
   - 14 null/missing/empty cases across all types
   - Property-based tests for determinism using Hypothesis
   - File: `tests/kernel/test_diff_comparison.py` — comprehensive adversarial corpus
   - File: `tests/kernel/test_diff_determinism.py` — property-based determinism tests

7. **Phase 2J: Schema Seeders & Documentation** (1 hour)
   - Migration to seed default Kind.attribute_schema for existing kinds (Deployment, ComputeInstance)
   - Documentation for configuring schema for new kinds
   - Files: `datum/reconcile/migrations/0004_seed_comparison_schemas.py`, update `docs/DIFF_SEMANTICS.md`

## Phase 4 Code Review (WBS 1.5.1–1.5.4)

**IMPORTANT:** All Phase 4 code must be rechecked in the next session before proceeding to Phase 5. This includes:

- **WBS 1.5.1** (Identity Matching with CF-6 fix): PR ready for merge
- **WBS 1.5.2** (Diff Engine): Phases 2A–2C complete (3 PRs), 2D–2J pending
- **WBS 1.5.3** (Precedence Policy): Spec document complete, implementation pending
- **WBS 1.5.4** (Discrepancy Lifecycle): Spec document complete, implementation pending

**Checklist for next session:**
- [ ] Merge Phase 2A, 2B, 2C PRs to main
- [ ] Run full test suite (including existing tests to ensure no regressions)
- [ ] Verify CI passes on main
- [ ] Check branch coverage for all kernel modules (≥100%)
- [ ] Validate determinism properties hold for reconciliation
- [ ] Spot-check integration: schema loading → comparison → discrepancy creation
- [ ] Review DIFF_SEMANTICS, PRECEDENCE_POLICY, DISCREPANCY_LIFECYCLE specs still align with implementation
- [ ] Begin Phase 2D (list comparison) or Phase 2E (timestamp comparison) based on priority

This delay protects against integration issues across the 4 WBS items and allows human review before Phase 5.
