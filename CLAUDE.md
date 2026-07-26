# CLAUDE.md

Guidance for Claude Code working in the Datum repository. Read this first, every session.

## What Datum is

A self-hosted source of truth for a cloud and Kubernetes estate. It holds declared intent in Git, ingests discovered state from providers on a schedule, and drives every difference between the two through an auditable reconciliation workflow. It is read-only toward the estate: it never applies, provisions, or remediates anything.

## Read before writing code

These documents are binding, not background. Read the ones relevant to your task before starting.

- `docs/DESIGN.md` — architecture, data model, module boundaries, per-component quality objectives, the ADRs, and the construction conventions (naming, routines, control flow, error handling, the review checklist)
- `docs/PROJECT_PLAN.md` — scope, phase order, deliberate exclusions, and the phase 1 vertical slice (the current build target)

## The rules that override instinct

**Build the current slice, not the whole layer.** The phase plan reads layer by layer, but the build is vertical: one kind, one collector, one screen, end to end, then widen. Do not build breadth ahead of the slice you are on. If the phase 1 slice in PROJECT_PLAN.md is active, its scope boundary is hard.

**When in doubt, build less, and ask.** If a task needs a decision the docs do not cover, stop and ask rather than inventing scope. Pulling a later phase forward is a defect, not initiative.

**Read-only toward the estate is permanent.** No code path writes to, provisions, or changes any provider resource. A collector that mutates the estate, or writes to the declared plane, is wrong by definition.

**The barricade (ADR-008).** Validate all external data at the boundary and convert it to domain types there. Provider dicts, Git document contents, and request bodies never travel inward raw. Interior code (`graph`, `reconcile`, `workflow`) trusts its inputs and uses assertions for impossible conditions. Assertions never contain side effects.

## Two tiers of code

**Kernel** — `reconcile` (matching, diff, precedence), `workflow` (discrepancy lifecycle). Correctness is the top priority; a wrong result is worse than no result. Kernel code:
- has cyclomatic complexity under 10 per routine, enforced in CI
- is branch-covered, with boundary cases and at least one case designed to break it
- has determinism tested as an invariant where it applies (identical input, identical output)
- is reviewed against the checklist in DESIGN.md by the model that did not write it, before merge

**Bulk** — scaffolding, CRUD, collectors, UI components, fixtures. Robustness and clarity matter; the kernel ceremony does not fully apply. Still formatted, linted, typed, and tested, just not line-by-line reviewed.

Label each PR with which tier it is and which model authored it.

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
