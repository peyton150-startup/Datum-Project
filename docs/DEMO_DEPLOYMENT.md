# Datum demo deployment

Target: Vercel frontend, the user's Ubuntu server for Django/Gunicorn, Valkey
and one Celery worker with Beat, and Supabase PostgreSQL. Preserve the existing
reconciliation runtime and use recorded discovery for the first protected demo.

## Verified on 2026-10-03

- Ubuntu host: `129.213.153.25`; SSH user: `ubuntu`. The saved local SSH entry
  `trellis` connects successfully using the user's existing key.
- Docker and Compose are installed. An existing Datum API and worker are
  running from `/home/ubuntu/datum/Datum-Project` against local PostgreSQL.
  Its API responds on `127.0.0.1:8001`.
- The server checkout is on main at `3d1d737`, with uncommitted changes to
  Dockerfile, settings, Compose and dependencies, plus an untracked WSGI file.
  These changes have been inspected but not overwritten or committed.
- The existing Caddy gateway serves HTTP on port 80 without an authentication
  directive. A protected frontend and backend gateway are still required before
  claiming this demo is deployed under the agreed access model.
- Supabase project [datum-demo](https://supabase.com/dashboard/project/ptmctjexmsggnzojqmpx)
  is healthy in `peyton150-startup`, region `us-east-1`, PostgreSQL 17.11.
  Project creation was approved at the connector's quoted $0/month.
- Provisioned login `datum_app`: no superuser, database-creation, role-creation,
  replication or RLS-bypass privileges. It owns the private `datum` schema and
  uses that schema as its database-specific search path.
- `anon` and `authenticated` cannot use the `datum` schema. Supabase's security
  advisor returned no findings after provisioning.
- Generated credentials are in the local Git-ignored `.env.supabase`. They
  have not been sent to the Ubuntu server. `.dockerignore` now also excludes
  `.env.*`, PEM files and key files from image build contexts.
- Backend URL:
  `https://instance-20260305-1808.chinchilla-kanyu.ts.net`. Tailscale Funnel is
  enabled and proxies this URL to `127.0.0.1:8001` on the Ubuntu server.
- `web/vercel.json` contains the user's requested `/api/:path*` rewrite to this
  backend's `/api/:path*`. Frontend deployment to Vercel is authorized and in
  progress; no successful deployment URL has been verified yet.
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

1. Reconcile the server's existing deployment edits with the current code before
   switching the API and worker to Supabase. Preserve the current local database
   and deployment files until the new runtime is verified.
2. Finish frontend checks and deploy `web/` to Vercel with the requested rewrite.
3. Verify protected API/frontend access, a scheduled task and visible recorded
   drift on the hosted application.

Supabase migrations are complete. The server's running containers still use
local PostgreSQL; the Supabase runtime switch is not complete yet.

Connection references:
[Supabase endpoints](https://supabase.com/docs/guides/database/connecting-to-postgres),
[certificate verification](https://supabase.com/docs/guides/platform/ssl-enforcement).
