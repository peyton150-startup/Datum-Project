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

## Connection work remaining

The Ubuntu server has no IPv6 default route, so use Supabase's session pooler
on port 5432. Copy the exact host from this project's Connect dialog; the pooler
cluster index cannot be inferred from the region. The pooled login is
`datum_app.ptmctjexmsggnzojqmpx`, and the database is `postgres`.

The local credential file has an explicit placeholder for the missing pooler
host. Do not attempt migrations until it is replaced with the actual address.
It requests `PGSSLMODE=verify-full`. A client probe with the system trust store
failed certificate verification; download Supabase's server CA certificate
from Database Settings and configure `PGSSLROOTCERT` to its mounted path.
The dashboard browser still needs the user to sign in to obtain the exact
host and CA certificate. The MCP connector itself remains authenticated.

Django's existing `POSTGRES_*` settings and Psycopg/libpq's `PGSSL*` environment
variables can express this connection without introducing a Supabase SDK or
changing the ORM. Preserve encryption and certificate verification.

Once those details are available:

1. Verify a connection as `datum_app`, the `datum` current schema and TLS.
2. Run `python manage.py migrate --noinput` from the reviewed reconciliation
   checkpoint, using the Supabase environment and the project's CA certificate.
3. Verify Django's applied migration history and both seeded comparison policies
   through the ORM. Do not replace Django migration execution with handwritten
   application DDL or mark unapplied migrations as applied.
4. Reconcile the server's existing deployment edits with the current code before
   switching the API and worker to Supabase. Preserve the current local database
   and deployment files until the new runtime is verified.
5. Verify protected API/frontend access, a scheduled task and visible recorded
   drift on the hosted application.

No Django migrations have been applied to Supabase yet. Neither the server's
running containers nor its database settings have been changed this session.

Connection references:
[Supabase endpoints](https://supabase.com/docs/guides/database/connecting-to-postgres),
[certificate verification](https://supabase.com/docs/guides/platform/ssl-enforcement).
