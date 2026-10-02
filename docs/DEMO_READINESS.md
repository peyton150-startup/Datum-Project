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
- The existing null/absent corpus remains, alongside 150 production reconciliation
  cases, all 14 numbered null/missing/empty cases, keyed-object statement cases,
  and a property test for order and sampling independence. Null remains a distinct
  list element in set mode, as approved on 2026-10-02.

## Independent review

The required two-stage blind review by a different model is posted on
[PR #88](https://github.com/peyton150-startup/Datum-Project/pull/88).
The reviewer found malformed stored declared schemas could abort the batch.
A new integration test reproduced the AttributeError before the correction;
it passes after the exact prescribed correction. The reviewer verified that
correction and the stale audit-example edits, and approved them subject to the
local gates passing, explicitly exempting the exact correction from re-review.
The final full suite passed: 1,162 tests, two live-cluster tests skipped.

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
- [#89](https://github.com/peyton150-startup/Datum-Project/issues/89): remove
  Claude Flow completely from the project. Requested as separate backlog work;
  no Claude Flow cleanup is included in this reconciliation patch.

Public deployment, authentication/roles, tenant isolation, TLS, backups and
restore testing still require their own deployment scope. Passing this
reconciliation checkpoint alone does not establish readiness for public hosting.
The user's subsequent deployment handoff chooses Vercel for `web/`. The user
then selected Supabase PostgreSQL for the database and their own Ubuntu server
for the Django API, one worker with Beat and Valkey, replacing the handoff's
Railway backend and database services.
Preserve that runtime; use recorded discovery for the first private portfolio demo. Protect
both the frontend and the backend, and verify a scheduled task plus visible drift
on the hosted application before claiming deployment complete.

### Deployment checkpoint (2026-10-02)

- Created [datum-demo](https://supabase.com/dashboard/project/ptmctjexmsggnzojqmpx)
  in the `peyton150-startup` organization through the Supabase connector.
- Project reference: `ptmctjexmsggnzojqmpx`; region: `us-east-1`.
- Supabase quoted $0/month, and the user explicitly approved that quoted cost.
- Project status is `ACTIVE_HEALTHY`. A connector SQL query verified PostgreSQL
  17.11 and a working database connection.
- Django is not connected yet, and Datum's migrations have not been applied to
  Supabase. The connector does not expose the admin database password. No
  application login, private schema, or local credential file has been created.
- Resume by creating a dedicated Django database login and private schema,
  storing its generated credentials outside Git, configuring encrypted database
  connections, and running Django's migration executor against Supabase. Verify
  the applied migration history and seeded comparison policies through the ORM.
- The user is preparing the Ubuntu server. Its hostname/IP and SSH username are
  still needed for backend deployment. Keep passwords and private keys out of
  chat; never put database credentials in Vercel's browser environment.
- Reconciliation CI passed on commit `cf71e81`:
  [run 37065394458](https://github.com/peyton150-startup/Datum-Project/actions/runs/37065394458).

The user requested a committed and pushed stopping point before connection work
continued. This checkpoint records completed infrastructure and pending work;
it does not claim a deployed application or completed hosted migrations.

## Acceptance walk for WBS 1.5.2

| Requirement | Evidence in this patch |
|---|---|
| 2H: production uses configured comparisons | `diff.py` calls `compare_field`; a service test changes 3 vs 5 from drift to agreement with tolerance 2 |
| 2H: service resolves and passes schemas | `_load_comparison_schemas` calls separate declared-type and operator-policy resolutions |
| 2H: typed kind identity; no placeholder machinery | `ComparisonSchema` validates identity and passes it into `FieldConfig`; the three temporary names and dict lookups are deleted |
| 2H: undecidable policy; batch continues | Kernel, policy-loader and API tests retain both plane statements, use the distinct discrepancy type, and preserve neighboring comparisons |
| 2H: real audit emission | Integration test uses Datum's supported logging configuration; disabling its INFO logger makes the test fail |
| 2H: independent sampling streams | Two kinds share `replicas`; distinct declared values distinguish the correct emitted resource from a field-only counter mutation |
| 2H: absence survives integration | Existing presence tests and the production corpus pass; the API preserves absent vs explicit null |
| 2I: essential adversarial corpus | 150 presence/value cases, 14 explicit specification cases, keyed-object statements, and the existing typed comparison corpus |
| 2I: deterministic result | Hypothesis changes field insertion order and audit sample rate without changing the discrepancy set |
| 2J: existing-kind policy seed | Migration 0004 separately seeds Deployment and ComputeInstance policy and preserves an existing policy on repeat |
| 2J: configuration instructions | DIFF_SEMANTICS documents the supported validated write function and derived field types |

The implementation covers these criteria; the independent verdict and exact
correction verification are posted. No precedence engine,
historical schema provenance, additional declared types, or Phase 5 work was
pulled forward.

## Tests added or materially changed

- `test_diff_comparison.py`: policy-driven drift, missing-policy identity,
  kind validation, cross-kind sampling, 150 presence/value cases, 14 numbered
  specification cases and 16 keyed-object statement cases.
- `test_diff_determinism.py`: property-based field-order and logging-rate invariance.
- `test_comparison_integration.py`: service-level configured tolerance, actual
  audit emission, API presence/authority for uncovered policy, and a malformed
  declared schema that leaves the other kind's comparison operational.
- `test_comparison_policy.py`: independent storage/type resolution, seeds,
  complete activation validation, corrupt-policy isolation and malformed
  timestamp precision rejection.
- Existing comparison/schema/audit tests now supply explicit typed kind identity.
  Existing diff/null tests use explicit test policy; production no longer
  invents a default policy for calls that omit it.
- `ReviewQueue.test.tsx`: missing policy displays the operator action and both
  values without an authority badge.

Mutation probes reject plain plane equality, shared field-only audit counters,
and disabled supported audit logging. They run in isolated processes without
editing production source or replacing coverage from the complete suite.
The audit integration test restores its logger outside pytest's temporary
capture stream; the property test checks verdicts without imposing a timing
deadline on audit I/O. Neither change removes a correctness assertion.

## Verification and readiness judgment

- Ruff formatting and lint/complexity: passed (118 Python files).
- Strict mypy: passed (32 source files).
- Complete backend suite: 1,162 passed, two live-cluster tests skipped; three
  upstream Pydantic deprecation warnings.
- Exact CI-gated branch coverage: 100% across reconciliation, workflow, intent,
  discovery and locks. Overall package coverage is 99%; the gated figure is
  deliberately reported separately.
- Acceptance smoke test: four passed.
- Migration consistency: no changes detected.
- Frontend lint, TypeScript/Vite build and six tests: passed.
- Independent gpt-6-sol blind review: required scope-and-fit sections posted;
  both findings corrected and verified under the reviewer's explicit exemption.

The reconciliation core is stable enough to switch focus to the agreed demo
deployment. No confirmed reconciliation blocker remains for the existing
recorded slice. Hosting still needs a production application server, platform
configuration, protected API/frontend access, the selected Supabase database,
and proof of a scheduled task and visible drift on the deployed application.
Those are deployment tasks rather than reasons to widen reconciliation scope.

## Exact changed files relative to the starting main revision

Starting revision: `3d1d73736bbe149c22b3e88f6a8e3315db1d71e3`.
The user's preexisting untracked AGENTS.md is excluded.

```text
README.md
datum/api/schemas.py
datum/enums.py
datum/reconcile/attribute_types.py
datum/reconcile/audit.py
datum/reconcile/comparison.py
datum/reconcile/diff.py
datum/reconcile/domain.py
datum/reconcile/migrations/0004_seed_comparison_schemas.py
datum/reconcile/models.py
datum/reconcile/policy.py
datum/reconcile/schema.py
datum/reconcile/service.py
docs/DEMO_READINESS.md
docs/DESIGN.md
docs/DIFF_SEMANTICS.md
tests/kernel/test_attribute_types.py
tests/kernel/test_audit.py
tests/kernel/test_comparison_boolean.py
tests/kernel/test_comparison_list.py
tests/kernel/test_comparison_numeric.py
tests/kernel/test_comparison_object.py
tests/kernel/test_comparison_presence.py
tests/kernel/test_comparison_string.py
tests/kernel/test_comparison_timestamp.py
tests/kernel/test_comparison_unreached_branches.py
tests/kernel/test_diff.py
tests/kernel/test_diff_comparison.py
tests/kernel/test_diff_determinism.py
tests/kernel/test_null_versus_absent.py
tests/kernel/test_schema.py
tests/reconcile_fixtures.py
tests/test_comparison_integration.py
tests/test_comparison_policy.py
web/src/ReviewQueue.test.tsx
web/src/ReviewQueue.tsx
web/src/api.ts
web/src/enums.ts
```
