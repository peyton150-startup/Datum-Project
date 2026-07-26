#!/bin/sh
# Block until the compose Postgres accepts connections, then exec the given command.
# Compose healthchecks cover `depends_on`, but one-off `docker compose run` and CI
# steps need their own gate.
set -e

host="${POSTGRES_HOST:-postgres}"
user="${POSTGRES_USER:-datum}"

until pg_isready --host="$host" --username="$user" >/dev/null 2>&1; do
    echo "waiting for postgres at $host..."
    sleep 1
done

exec "$@"
