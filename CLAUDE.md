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

**Overall Status:** Phases 2A–2F complete and **all merged to main**. Phases 2G–2J remain. `main` is CI-green.

**Merged to main:** 2A Schema Validation, 2B Numeric, 2C String, 2D List, 2E Timestamp (PRs #18–#25); 2F Object Comparison and the absent/null presence fix (PR #27); the CF-6 anchor write half (PR #28) and its test completion (PR #29).

**Do not trust a phase's "tests passing" as "CI passing."** They are different claims, and this section once conflated them — `main` was red for three gates while the status section said otherwise. Before claiming CI-green, run all five locally. **The bare commands do not resolve on this machine; use `python -m`:**

```
python -m ruff format --check .
python -m ruff check .
python -m mypy datum/reconcile datum/intent datum/graph
python -m pytest
python -m coverage report --include="datum/reconcile/*,datum/workflow/*,datum/intent/*,datum/discovery/*,datum/locks.py" --fail-under=100
```

The full suite needs Postgres (`docker-compose up -d`), and the compose Postgres is on **port 5544** while settings default to 5432 — set `POSTGRES_PORT=5544` or ~165 tests error with `OperationalError` and only the kernel subset is meaningful. A coverage gate that never executed reports nothing, not success.

**Two findings worth carrying forward:**

- **`python-dateutil` was an undeclared dependency.** Phase 2E imports it directly; it resolved only as a transitive dependency of `kubernetes`. Now declared.
- **Phases 2B–2E collapsed absence into null.** All four read their planes with `resolve(on_absent=lambda: None, ...)`, making `missing` and `null` indistinguishable, against row one of the Null / Missing / Empty table. Latent because `diff.py` still uses `PlaneValue.__eq__`; it would have gone live at Phase 2H with every phase's own tests still green. Fixed on the third branch, with presence now travelling beside the value through one shared routine. **Phase 2H must not reintroduce it** — that phase is exactly where the latent defect becomes real.

**Remaining Phases (in order):**
1. **Phase 2G: Logging Infrastructure** (2 hours)
   - AuditLogEntry structure already defined in comparison.py
   - Implement 3 logging levels: debug (all), discrepancy (mismatches only), sampled_audit (every Nth)
   - File: `datum/reconcile/comparison.py` — add `_write_audit_log()` and logging config
   - **`AuditLogEntry._kind_name` is always `"unknown"` today.** Nothing sets it; it is inert scaffolding waiting for 2H. Do not assume audit entries carry a kind name.
   - `AuditLogEntry.declared_raw` is `None` for both absent and null, with presence carried only by the transformed field. If 2G persists audit entries, that pair needs presence flags the way `Discrepancy` already has them.

2. **Phase 2H: Integration & Refactoring** (2 hours)
   - Update `_field_discrepancies()` in `datum/reconcile/diff.py` to use `compare_field()` dispatcher
   - Update `reconcile()` to accept `schema_map` parameter
   - Add `_load_comparison_schemas()` to `datum/reconcile/service.py`
   - Update `run_reconciliation()` to pass schema to reconcile()
   - Handle MissingFieldConfig gracefully (log error, treat as discrepancy)
   - Files: `datum/reconcile/diff.py`, `datum/reconcile/service.py`
   - **The sharp edge.** This phase replaces `PlaneValue.__eq__` — which gets absence right — with the comparison functions, which had to be fixed to get it right. `tests/kernel/test_null_versus_absent.py` and `tests/kernel/test_comparison_presence.py` are the two that must both stay green through it; the first tests the rule through `diff.py`, the second through the comparison functions, and 2H is where the two paths meet.

3. **Phase 2I: Adversarial Corpus Tests** (5 hours)
   - **Settle issues #34 and #35 before writing the corpus.** Both concern how `version`/`identity` handle presence, and a 150-case corpus written over current behaviour will encode it as intended — a corpus is far harder to change than a function. #34 is a confirmed defect; #35 is a decision nobody has made.
   - 150+ test cases from DIFF_SEMANTICS.md across all 5 types
   - 14 null/missing/empty cases across all types
   - Property-based tests for determinism using Hypothesis
   - File: `tests/kernel/test_diff_comparison.py` — comprehensive adversarial corpus
   - File: `tests/kernel/test_diff_determinism.py` — property-based determinism tests

4. **Phase 2J: Schema Seeders & Documentation** (1 hour)
   - Migration to seed default Kind.attribute_schema for existing kinds (Deployment, ComputeInstance)
   - Documentation for configuring schema for new kinds
   - Files: `datum/reconcile/migrations/0004_seed_comparison_schemas.py`, update `docs/DIFF_SEMANTICS.md`

## Session handoff — 2026-07-31 (second session of the day; supersedes the first)

Read this before picking anything up. It is the state a session ended in, not a plan. **Run `git fetch --all --prune && gh pr list && gh issue list` first and believe that over this section.**

**Where the work is.** Everything from the previous handoff is merged. `main` is CI-green — verified, not inferred. One PR is open:

- **PR #32** `fix/reconcile-run-exclusion` — CI green, **awaiting non-authoring review**. Kernel + boundary tier, authored by Claude Opus 5. That review was dispatched at the end of the session; its verdict may not have been seen. **Check the PR for review comments before merging.** It closes #30 automatically.

The `backup/main-30af0a2-stale-claudemd` branch and the Phase 2C `stash@{0}` mentioned in the previous handoff are both gone — the stash was verified to be superseded Phase 2D work containing the absence-collapsing defect, and dropped (recoverable at `0145390` until gc).

**What was done.** The CF-6 anchor write half (#28) — `_write_matches` wrote no anchor columns, so every match row anchored to `('', '', '')`, and a second pair in one run collided. That defect was hiding a second: a re-run wrote a fresh proposal over a standing confirmed decision. Test completion for it (#29). Non-authoring reviews of Phase 2F, the presence fix, and #28 — the merge gate the previous handoff listed first. Reconciliation run exclusion (#32).

**What is next, in order.**

1. **Land PR #32** once its review is read.
2. **Settle #34 and #35** — both concern `version`/`identity` presence handling, and both must be answered before 2I.
3. **Phase 2G**, then **2H**. 2H is still the sharp one; see its entry above.

**Open issues, all logged so none of this lives only in a chat transcript:**

| | |
|---|---|
| #31 | `intent/ingest.py` — the two remaining DESIGN §11 races. Scoped to the Git webhook, which §11 says makes them certain. Do not reach for the advisory lock there: those are check-then-insert writes, not runs. |
| #33 | `recurse(N)` / `tolerance(N)` crash rather than degrade when a malformed parameter reaches them past the barricade. Two phases, one mistake. |
| #34 | **Confirmed defect.** `version`/`identity` report `null` and the string `"None"` as equal, because the inner comparison stringifies. Before 2I. |
| #35 | **Decision needed.** Both-sides-absent reporting a discrepancy is inconsistent with row 4 of the Null/Missing/Empty table. Before 2I. |

**What would have bitten the next session, had nobody written it down.**

- **A gate that never executed reports nothing, not success.** Each gate hid the next this session: ruff and mypy hid a test failure, which hid a coverage gap. Run all five, in order, and read the exit code.
- **`main` was red on CI for a full session while the status section claimed otherwise.** Verify; do not infer from a green `pytest`.
- **A test whose docstring claims more than it demonstrates is this project's recurring failure mode.** Phases 2B–2E each passed their own tests while collapsing absence into null, because every phase tested both-absent and both-null and none tested one against the other. The same thing recurred in #28's migration test, which claimed to hold two `if`s apart while covering three of four combinations. Branch coverage does not catch this and cannot.
- **Concurrency claims need reproducing, not reasoning.** The #32 race was reproduced with two threads before being fixed, and the reproduction is now a permanent regression test. A first draft of that design also asserted PostgreSQL exposes uncommitted reads under READ COMMITTED, which is false; it was caught in review. Verify database semantics against the database.
- **`pytest` locally needs Postgres on port 5544**, and the tools need `python -m`. Both are in the gate block above.

## Phase 4 Code Review (WBS 1.5.1–1.5.4)

**IMPORTANT:** All Phase 4 code must be rechecked in the next session before proceeding to Phase 5. This includes:

- **WBS 1.5.1** (Identity Matching with CF-6 fix): merged, including the write half (#28) and its run exclusion (#32, pending)
- **WBS 1.5.2** (Diff Engine): Phases 2A–2F merged, 2G–2J pending
- **WBS 1.5.3** (Precedence Policy): Spec document complete, implementation pending
- **WBS 1.5.4** (Discrepancy Lifecycle): Spec document complete, implementation pending

**Checklist:**
- [x] Merge Phase 2A–2E PRs to main
- [x] Check branch coverage for all kernel modules (100% on comparison, diff, domain, matcher, schema)
- [x] Verify CI passes on main — green as of 2026-07-31, confirmed on the merge commit rather than inferred
- [x] Run the full suite against Postgres, not just the kernel subset — 632 passing on port 5544
- [x] Non-authoring review of Phase 2F and the presence fix, both kernel tier — done by Claude Sonnet, verdict *approve with changes*; the changes are issues #33, #34, #35
- [ ] Non-authoring review of PR #32 — dispatched, verdict may be unread. Check the PR.
- [ ] Validate determinism properties hold for reconciliation
- [ ] Spot-check integration: schema loading → comparison → discrepancy creation
- [ ] Review DIFF_SEMANTICS, PRECEDENCE_POLICY, DISCREPANCY_LIFECYCLE specs still align with implementation

**The review gate has a mechanism now.** Kernel-tier code needs a reviewer that is a different model from the author. Dispatch a subagent pinned to another model with a **blind** prompt: give it the diff, CLAUDE.md, and the DESIGN review checklist, and withhold the author's reasoning entirely. A prompt that explains the fix leads the witness. Blind reviews this session found a confirmed equality defect, an unguarded parse, and a design table that was right for the wrong reason — and also produced two incorrect claims about PostgreSQL isolation, so treat findings as claims to verify rather than conclusions.

This delay protects against integration issues across the 4 WBS items and allows human review before Phase 5.
