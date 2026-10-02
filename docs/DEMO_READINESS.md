# Reconciliation checkpoint for a demo

Scope: the supplied handoff's deployment-readiness checkpoint. No deployment,
authentication, provider writes, new collectors, schema provenance, precedence
engine or lifecycle expansion is included.

## Implemented on this branch

- Production reconciliation resolves declared types and operator policy in two
  named steps, then calls the configured field comparison dispatcher.
- Kind identity flows through FieldConfig; the comparison dictionary placeholder
  machinery is removed.
- Each run owns one audit writer. Tests cover supported logging emission and
  independent sampling for same-named fields in different kinds.
- Uncovered/unusable policy produces `missing_comparison_policy`, preserving
  presence and values without asserting an authoritative plane or normal drift.
  The existing review queue explains the required operator action.
- ComparisonPolicy is separate from Kind.attribute_schema. Its supported write
  function validates complete policy before saving; migration 0004 seeds the two
  existing kinds. Configuration instructions live in DIFF_SEMANTICS.md.
- The existing null/absent corpus remains, alongside 150 production dispatcher
  cases and a property test for order and sampling independence.

## Before calling this deployment-ready

The required two-stage blind review by a different model has not run. This is
kernel and boundary code and must remain unmerged until the verdict is posted
to the PR. Review any finding against the current tree; production corrections
need a new review unless the verdict explicitly grants the exact-change exemption.

Retain the two-stage review restrictions in AGENTS.md. Stage 1 sees the code and
diff and infers the task before reading this document, DIFF_SEMANTICS.md, the
handoff, or task-specific PROJECT_PLAN material. Stage 2 receives the required
plan/design/checklist and answers all four named scope-and-fit questions.

## Resuming checks

Use the Python 3.12 Compose image: the host's default Python 3.14 does not have
the check tools. `docker compose run --rm --no-deps app` reaches Postgres at
the internal port 5432; native host runs need POSTGRES_PORT=5544.

Run the five backend gates in AGENTS.md, the acceptance smoke test with
`--no-cov`, and frontend lint/build/tests. Never run two pytest jobs against this
database concurrently. Freeze Python source files while collecting coverage;
line-changing docstring edits during a run invalidate source-line attribution.

## Open issues outside this handoff

- #69: fractional declared values; the seeded kinds currently require integers.
- #70: optional declared fields and governing-schema provenance. This branch
  preserves separate resolution steps but does not implement those features.
- #57: classifying ambiguous collector writes; no new collector behavior here.
- #73: intermittent lock-concurrency test. The baseline passed; investigate
  before depending on concurrent operational runs. A serial recorded demo does
  not exercise that disputed behavior.

Public deployment, authentication/roles, tenant isolation, TLS, backups and
restore testing still require their own deployment scope. Passing this
reconciliation checkpoint alone does not establish readiness for public hosting.
