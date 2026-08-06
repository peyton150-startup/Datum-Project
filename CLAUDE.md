# CLAUDE.md

Guidance for Claude Code working in the Datum repository. Read this first, every session.

**Nothing in this file is state.** It holds rules, and rules only. For where the work actually is — what is merged, what is open, what is next — run this and believe it:

```
git fetch --all --prune && gh pr list && gh issue list
```

Open decisions live in their GitHub issues, which carry the detail and the options. The phase plan lives in `docs/PROJECT_PLAN.md`. If this file ever tells you which PR is open, that line is a bug in this file.

## What Datum is

A self-hosted source of truth for a cloud and Kubernetes estate. It holds declared intent in Git, ingests discovered state from providers on a schedule, and drives every difference between the two through an auditable reconciliation workflow. It is read-only toward the estate: it never applies, provisions, or remediates anything.

## Read before writing code

These documents are binding, not background. Read the ones relevant to your task before starting.

- **`docs/PROJECT_PLAN.md`, the `START HERE at the opening of Phase 4` section at the very end. Read this first.** Phases 0 through 3 are closed. That section lists the decisions that must be made before Phase 4 code is written. Do not start Phase 4 work without it. The remaining WBS 1.5.2 phases (2G–2J) are at the end of the same file.
- `docs/DESIGN.md` — architecture, data model, module boundaries, per-component quality objectives, the ADRs, and the construction conventions (naming, routines, control flow, error handling, the review checklist)
- `docs/PROJECT_PLAN.md` — scope, phase order, deliberate exclusions, the per-phase close-outs, and the enforcement column on the quality objectives

## The rules that override instinct

**Build the current slice, not the whole layer.** The phase plan reads layer by layer, but the build is vertical: one kind, one collector, one screen, end to end, then widen. Do not build breadth ahead of the slice you are on. The phase 1 slice is closed and widened — two kinds and two collectors now exist — so the scope boundary that binds is the current phase's, and the current phase is 4.

**When in doubt, build less, and ask.** If a task needs a decision the docs do not cover, stop and ask rather than inventing scope. Pulling a later phase forward is a defect, not initiative.

**Read-only toward the estate is permanent.** No code path writes to, provisions, or changes any provider resource. A collector that mutates the estate, or writes to the declared plane, is wrong by definition.

**The barricade (ADR-008).** Validate all external data at the boundary and convert it to domain types there. Provider dicts, Git document contents, and request bodies never travel inward raw. Interior code (`graph`, `reconcile`, `workflow`) trusts its inputs and uses assertions for impossible conditions. Assertions never contain side effects.

**Two encodings of one rule drift apart.** This is the project's most productive bug family, and it has produced at least three: a barricade that range-checked a parameter while the kernel checked nothing, so the kernel crashed on inputs its own validator had already excluded; two `-1` literals in two modules that must stay equal for a mode to keep its meaning; and two degrade paths reporting the same condition under two different audit strings. When you find one rule written twice, the fix is to make it one encoding, not to make both correct — both being correct today is exactly what makes the drift invisible tomorrow.

**A document row is a claim about code.** When the code changes, the claim is wrong until someone edits it. DESIGN §11's table said `IntegrityError` for three days after the commit that fixed it, and issue #31 was filed against working code by someone reading the table rather than the tree. Correct stale documentation in whatever PR you notice it from.

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

## The review gate

Kernel-tier and boundary-tier code needs a reviewer that is **a different model from the author**, before merge.

Dispatch a subagent pinned to another model with a **blind** prompt: give it the diff, this file, and the DESIGN review checklist, and withhold the author's reasoning entirely — including the PR description, the commit messages, and the issue being fixed. A prompt that explains the fix leads the witness. Ask it to try to break the code rather than to confirm it works; "CI is green" is not evidence of correctness, because whether the right tests exist is part of what is under review.

**Post the verdict to the PR the moment it arrives, before summarising it in chat.** A review that lives only in the conversation does not survive the session.

**Do not merge before the verdict is posted, and say plainly when a PR is still waiting on one.** The gate is the verdict arriving, not the review having been dispatched. On 2026-08-03 three PRs were merged while their reviews were still running, and one of them put a kernel defect on `main`: a structured value and a string that spelled it began comparing equal, which the review had already caught and reported about ten minutes later. Every one of those merges looked safe — CI was green on all three, and green CI is what the review exists to distrust.

Reviews here have taken five to thirteen minutes. If that wait is not affordable, merge and say so explicitly, then treat the verdict as a defect report against `main` rather than as advice — but the cheap version is to wait.

**A fix written in response to a review is new code and needs its own review.** The reviewer never saw it. This is where the loop actually terminates: a fix that only tightens comments or adds a test can be merged on the strength of the original verdict, but one that changes how a result is decided starts over.

**Treat findings as claims to verify, not conclusions.** Blind reviews here have found real defects — a confirmed equality defect, an unguarded parse, a design table that was right for the wrong reason — and have also produced confidently wrong claims about PostgreSQL isolation, and have under-rated a real one (`tolerance(inf)` was reported as harmless degradation when it silently suppresses every discrepancy on the field). Check each finding yourself before acting on it.

## Non-negotiable tooling

Formatting and linting are settled by tools, never discussed in review. CI fails on: unformatted code, lint errors, type errors in kernel modules (`mypy --strict`), complexity over 10 in the kernel, and coverage below threshold on gated modules. If the formatter has an opinion, it is right.

**"Tests passing" and "CI passing" are different claims.** Before claiming CI-green, run all five gates locally. **The bare commands do not resolve on this machine; use `python -m`:**

```
python -m ruff format --check .
python -m ruff check .
python -m mypy datum/reconcile datum/intent datum/graph
python -m pytest
python -m coverage report --include="datum/reconcile/*,datum/workflow/*,datum/intent/*,datum/discovery/*,datum/locks.py" --fail-under=100
```

The full suite needs Postgres (`docker-compose up -d`) and Docker Desktop actually running. **The compose Postgres is on port 5544 while settings default to 5432** — set `POSTGRES_PORT=5544` or ~165 tests error with `OperationalError` and only the kernel subset is meaningful. A coverage gate that never executed reports nothing, not success.

**Never run two `pytest` invocations against the compose Postgres at once.** They share one test database and clobber each other — the result is 60–70 failures spread across unrelated files, which reads exactly like a code regression. It is not. Re-run alone before believing any large failure count. If a run hangs rather than fails, check that Docker is still up before suspecting the code.

## Naming and structure, in one breath

Names come from the problem domain, describe what a thing is not how it is stored, read as positive boolean statements, and never differentiate by number. Routines do one thing their name fully describes; no `handle`, `process`, `manage`, or `do`. Guard clauses over nesting; nesting past three levels is a smell. Repeated branching on one value is a table, not an if-chain, which applies with force to precedence rules and comparison semantics. Magic values are named constants. State machines use enums shared across Python, the database, and TypeScript from one source. Full detail is in the construction conventions in DESIGN.md.

## Testing bar

Prefer tests that try to break the code over tests that confirm it works; the natural bias runs the other way. Hand-verifiable test data over realistic-looking noise. Boundaries tested just below, at, and just above. Branch coverage, not statement coverage. Review test code as carefully as production code. For the diff engine and matcher, the adversarial corpus in DESIGN.md section 12 is part of the definition of done.

**A test whose docstring claims more than it demonstrates is this project's recurring failure mode.** It has recurred in a test written by a session that had just read this warning.

**The check: name the bug the test excludes, then ask whether this fixture could give a different answer under that bug.** If it could not, the test proves nothing, whatever its docstring says. The cheapest way to be sure is to break the fix on purpose and watch the test fail; a test that passes with the fix reverted is documentation, not evidence. When a test is a guard against an over-broad fix rather than a demonstration of the bug, say so in its docstring instead of letting it borrow credit.

## Debugging

Reproduce reliably before hypothesizing. Fix the cause, not the symptom; a special case bolted next to a bug means the logic still does not handle that input generally. Test the fix plus a nearby case that should not trigger it. Look for the same mistake elsewhere before moving on. Under time pressure, slow down: most first-attempt fixes are wrong.

**Reproduce before theorising, and distrust a bug report without its traceback.** One issue here was filed carefully, with two candidate causes and a procedure to tell them apart. Both were wrong, and the real cause was not among them — the original output had been filtered before it was saved, and that was the whole problem.

**Verify an issue against the current tree before working it.** An issue can be filed against already-fixed code; one was, because it was written from a stale documentation table rather than from the source. That check takes two minutes and has saved a day.

**Prefer measuring to reasoning about someone else's semantics.** Four questions about Postgres advisory-lock lifetimes decided a design; probing them against the real database took minutes and overturned two of the four assumptions that had been made from the manual.

## When you finish a piece of work

- Run the daily build and its smoke test locally.
- Confirm no later-phase scope was pulled forward.
- If you are closing a phase, walk its acceptance criteria in writing and state any not met plainly, rather than redefining them.
- If you learned something durable, put it in this file or in the memory directory — both load automatically. Do not put it in a handoff section; that is how it goes stale.

## Session naming convention

Every Claude Code session that touches the Datum project should start its title with the PR number(s) it addresses, in the format `PR. #X` or `PR. #X, #Y` for multiple PRs. This makes it trivial to find which session touched which PR when you need to return to context.

**Automatic naming:** When you create a session or branch, immediately name it with the PR number at the front. For example:
- `PR. #65 — fix(intent): the schema decides a declared scalar's type, YAML does not`
- `PR. #56, #59 — PR #58 split and unblock`

**Status-only sessions:** Sessions that are research, exploration, or status review (no PR opened yet) should be clearly marked as such:
- `Status: Project status and next steps`
- `Exploration: Schema type architecture`

This convention makes your session list scannable at a glance and eliminates the need to search transcripts to find which work touched which PR.
