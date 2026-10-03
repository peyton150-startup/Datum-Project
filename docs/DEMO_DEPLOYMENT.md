# Datum demo deployment

Target: Vercel frontend, the user's Ubuntu server for Django/Gunicorn, Valkey
and one Celery worker with Beat, and Supabase PostgreSQL. Preserve the existing
reconciliation runtime and use recorded discovery for the first protected demo.

## Verified on 2026-10-03

- Ubuntu host: `129.213.153.25`; SSH user: `ubuntu`. The saved local SSH entry
  `trellis` connects successfully using the user's existing key.
- Docker and Compose are installed. The Datum API and worker now run source
  from `/home/ubuntu/datum/Datum-Project-demo-stage` against Supabase. Ubuntu's
  one-off probe and the running API both verified certificate-checked TLS.
- The server checkout advanced to main at `7b941ec` while deployment was in
  progress. Its production Dockerfile, Gunicorn dependency, WSGI entry point
  and template settings are preserved in this branch. The existing server
  checkout and its Compose configuration remain the rollback deployment.
- The existing Caddy gateway serves HTTP on port 80 without an authentication
  directive. The user approved a shared demo login for the website and API,
  with Ubuntu SSH access unchanged. That gateway is being prepared; access
  protection remains incomplete until its deployed checks pass.
- Supabase project [datum-demo](https://supabase.com/dashboard/project/ptmctjexmsggnzojqmpx)
  is healthy in `peyton150-startup`, region `us-east-1`, PostgreSQL 17.11.
  Project creation was approved at the connector's quoted $0/month.
- Provisioned login `datum_app`: no superuser, database-creation, role-creation,
  replication or RLS-bypass privileges. It owns the private `datum` schema and
  uses that schema as its database-specific search path.
- `anon` and `authenticated` cannot use the `datum` schema. Supabase's security
  advisor returned no findings after provisioning.
- Generated credentials are in the local Git-ignored `.env.supabase` and the
  server's private `/home/ubuntu/datum/.supabase/connection.env` (mode 600).
  The CA certificate is alongside it. `.dockerignore` now also excludes
  `.env.*`, PEM files and key files from image build contexts.
- Backend URL:
  `https://instance-20260305-1808.chinchilla-kanyu.ts.net`. Tailscale Funnel is
  enabled and proxies this URL to `127.0.0.1:8001` on the Ubuntu server.
- `web/vercel.json` contains the user's requested `/api/:path*` rewrite to this
  backend's `/api/:path*`. Vercel project `datum-demo` is deployed at
  [datum-demo-seven.vercel.app](https://datum-demo-seven.vercel.app).
  The production build succeeded; the homepage, Django Ninja documentation
  at `/api/docs`, and rewritten discrepancy API return HTTP 200. The browser
  renders `Deployment default/web` with declared replicas 3 and discovered 5.
  Frontend lint, production build and all six UI tests passed locally.
- Backed up the Ubuntu deployment files, uncommitted changes and local database
  under `/home/ubuntu/datum/deployment-backup-20261003` before the runtime switch.

## Verified Supabase connection and migrations

The Ubuntu server has no IPv6 default route, so use Supabase's session pooler
on port 5432. Copy the exact host from this project's Connect dialog; the pooler
cluster index cannot be inferred from the region. The pooled login is
`datum_app.ptmctjexmsggnzojqmpx`, and the database is `postgres`.

The authenticated project Connect dialog confirmed the host is
`aws-0-us-east-1.pooler.supabase.com`. Downloaded the Supabase CA certificate
from the dashboard-provided link into the Git-ignored `.env.supabase-ca.crt`.
The connection environment requests `PGSSLMODE=verify-full` and points
`PGSSLROOTCERT` to this certificate's container path.

Verified a certificate-checked client connection as `datum_app` with current
schema `datum`. Ran Django's migration executor from the reconciliation
checkpoint: all 26 migrations applied, including
`reconcile.0004_seed_comparison_schemas`. The ORM reports no pending migrations,
both seeded kinds (`Deployment`, `ComputeInstance`), and both seeded comparison
policies. The Psycopg client confirms TLS is in use.

Django's existing `POSTGRES_*` settings and Psycopg/libpq's `PGSSL*` environment
variables can express this connection without introducing a Supabase SDK or
changing the ORM. Preserve encryption and certificate verification.

## Runtime and frontend work remaining

1. Maintain the API and worker with `deploy/compose.supabase.yml`.
   It overlays the existing server Compose file;
   pass both the existing `.env` and the private Supabase connection environment,
   and set `DATUM_APP_SOURCE` to the staged source path. Keep the original
   checkout and local database for rollback.
2. Deploy and verify the approved shared demo login on both frontend and API.

The worker overlay uses recorded discovery and a read-only mount of a local
sample intent repository. Collection and intent polling are scheduled by the
existing Beat configuration. Reconciliation is currently an explicit operation;
no new automatic reconciliation schedule is introduced by this deployment.

Supabase migrations and the runtime switch are complete. The worker ingested
sample intent commit `a178fa6` and collected one recorded Deployment without
errors. Beat then dispatched both tasks on its five-minute schedule: intent
polling succeeded and collection run 2 reported success with no gap. Explicit
reconciliation produced the replicas discrepancy, verified through the ORM
and the hosted React queue. No live estate was read or changed.

Local gates after preserving the production entry point: 1,162 backend tests
passed (two live tests skipped), four acceptance smoke tests passed, all five
backend gates passed with 100% gated branch coverage. Template settings and
WSGI are deployment scaffolding; reconciliation behavior is unchanged.

Connection references:
[Supabase endpoints](https://supabase.com/docs/guides/database/connecting-to-postgres),
[certificate verification](https://supabase.com/docs/guides/platform/ssl-enforcement).

## Approved access model

The user selected one shared demo login for the website and API, with SSH access
unchanged. `web/middleware.ts` checks the server-only `DATUM_DEMO_AUTH_SHA256`
verifier before serving frontend assets or API rewrites. No login verifier or
infrastructure credential uses a `VITE_*` name or enters the browser bundle.
The original relative `/api` calls and exact `web/vercel.json` rewrite remain.

Caddy independently authenticates requests before proxying to Gunicorn. The
Compose overlay gives Caddy the existing loopback port 8001 and removes
Gunicorn's host port, so the existing Funnel reaches the protected gateway
without changing Tailscale configuration. State-changing requests also need an
allowed Origin; the production Vercel hostname and the backend docs hostname
are allowed. Reads remain available to authenticated command-line clients.

The generated username and password are in the ignored local `.env.demo-access`.
Caddy receives only its password hash in the private `demo-access.env`; Compose
reads that file with `format: raw` to preserve dollar signs in bcrypt hashes.
Vercel receives only the SHA-256 verifier as a Secret in Production and Preview.
Browsers prompt for demo credentials; Ubuntu SSH continues using the existing
SSH key. No SSH daemon, Oracle firewall, Django secret key, database credential,
or Trellis service change is part of this gate.

Verification before publishing: frontend lint/type-check/build and all 22 tests
passed; runtime npm audit reported zero vulnerabilities. An isolated Caddy
container returned 401 for missing or wrong credentials, 200 for authenticated
reads, 403 for untrusted write origins, and passed a trusted write through to
Django. The independent two-stage gpt-6-sol boundary review approved the gate;
its four scope-and-fit sections are posted on PR #90. No merge has occurred.
The gate makes direct public access to the unprotected Gunicorn port
unavailable, rather than relying on callers visiting only the Vercel hostname.
