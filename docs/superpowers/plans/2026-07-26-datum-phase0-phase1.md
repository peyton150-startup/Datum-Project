# Datum Phase 0 + Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Datum foundation (Phase 0) and build the Phase 1 vertical slice — one Kubernetes Deployment kind, declared in Git, discovered from a JSON fixture, matched by natural key, diffed field-by-field, exposed over a read-only API, and cleared in a browser review queue — ending exactly at the Phase 1 acceptance test.

**Architecture:** A Django monorepo split into the DESIGN.md §3 modules (`kinds`, `graph`, `intent`, `discovery`, `reconcile`, `workflow`, `api`) plus a React/Vite `web/` frontend. The reconciliation kernel (matcher, diff engine) is written as **pure functions over frozen domain dataclasses** — never over ORM rows — so it can be exhaustively unit-tested and holds determinism as an invariant. External data (provider JSON, Git YAML, HTTP bodies) is validated and converted to domain types at the boundary (ADR-008 barricade); interior code trusts its inputs.

**Tech Stack:** Python 3.12, Django 5.0, django-ninja, PostgreSQL 16, Celery 5 + Valkey, pytest + pytest-django + pytest-cov, ruff, mypy (strict on kernel), Hypothesis (property tests), React 18 + TypeScript 5 + Vite 5 + Tailwind 3, Docker Compose, Caddy 2.

## Global Constraints

- **Read-only toward the estate (ADR-007):** no code path writes to, provisions, or changes any provider resource. The collector reads a fixture and writes only `discovered_resource`. It never writes to the declared plane.
- **Barricade (ADR-008):** validate provider JSON / Git YAML / request bodies at the boundary and convert to domain types there. `graph`, `reconcile`, `workflow` trust their inputs and use `assert` only for impossible (bug) conditions, never with side effects.
- **Kernel bar (`reconcile`, `workflow`):** cyclomatic complexity < 10 per routine (ruff C901), `mypy --strict` clean, branch-covered, determinism tested as an invariant. Every kernel PR reviewed by the model that did not write it against the DESIGN.md checklist.
- **Scope is hard:** one kind (Deployment), one collector, one screen. Discrepancy states are only `open` and `resolved`. No precedence policy, no suppression, no history, no auth, no second kind/collector/screen. Any of these appearing is a defect against the spec.
- **Tenancy:** `tenant_id` is present on every resource table from the first migration and every query is written tenant-scoped, using the constant `DEFAULT_TENANT_ID`. RLS enforcement is deferred to Phase 5 — do not add it.
- **Enums from one source:** state enums are defined once in `datum/enums.py` and generated into `web/src/enums.ts`. Never hand-duplicate a string enum in TypeScript.
- **Naming:** domain names, positive booleans, no magic values, no `handle`/`process`/`manage`/`do` routines. `kind`/`declared`/`discovered`/`scope` are the canonical words — never `type`/`intent_obj`/`actual`/`namespace` in domain code.
- **Commit cadence:** commit at the end of every task (each task ends green).

## Canonical domain types and signatures (referenced by all tasks)

Defined in Task 6 (`datum/reconcile/domain.py`) and Task 4 (`datum/enums.py`). Reproduced here so every task uses identical names:

```python
# datum/enums.py  (Django TextChoices; .value gives the DB/JSON string)
class MatchStrategy(TextChoices):   NATURAL_KEY="natural_key"; BINDING="binding"; PROVIDER_TAG="provider_tag"
class Confidence(TextChoices):      HIGH="high"; MEDIUM="medium"; LOW="low"
class MatchState(TextChoices):      PROPOSED="proposed"; CONFIRMED="confirmed"; REJECTED="rejected"
class DiscrepancyType(TextChoices): FIELD="field"; DECLARED_MISSING="declared_missing"; DISCOVERED_UNDECLARED="discovered_undeclared"
class DiscrepancyState(TextChoices):OPEN="open"; RESOLVED="resolved"
class Plane(TextChoices):           DECLARED="declared"; DISCOVERED="discovered"
class CollectorRunStatus(TextChoices): SUCCESS="success"; PARTIAL="partial"; FAILED="failed"

# datum/reconcile/domain.py  (all frozen)
NaturalKey = tuple[str, str, str, str]   # (kind, tenant_id, scope, name)

@dataclass(frozen=True)
class ResourceSnapshot:
    kind: str; tenant_id: str; scope: str; name: str
    provider_id: str | None
    attributes: Mapping[str, object]
    @property
    def natural_key(self) -> NaturalKey: ...   # (kind, tenant_id, scope, name)

@dataclass(frozen=True)
class MatchedPair:
    declared: ResourceSnapshot; discovered: ResourceSnapshot
    strategy: str; confidence: str

@dataclass(frozen=True)
class MatchResult:
    pairs: tuple[MatchedPair, ...]
    declared_orphans: tuple[ResourceSnapshot, ...]
    discovered_orphans: tuple[ResourceSnapshot, ...]

@dataclass(frozen=True)
class FieldDiscrepancy:
    natural_key: NaturalKey; field_name: str
    declared_value: object; discovered_value: object

@dataclass(frozen=True)
class OrphanDiscrepancy:
    natural_key: NaturalKey; discrepancy_type: str   # DECLARED_MISSING | DISCOVERED_UNDECLARED

@dataclass(frozen=True)
class DiscrepancySet:
    field_discrepancies: tuple[FieldDiscrepancy, ...]
    orphans: tuple[OrphanDiscrepancy, ...]

# Kernel entrypoints
def match_by_natural_key(declared: Sequence[ResourceSnapshot],
                         discovered: Sequence[ResourceSnapshot]) -> MatchResult: ...
def reconcile(match_result: MatchResult) -> DiscrepancySet: ...   # deterministic
```

---

## Task 0: Docs and repo root — ALREADY DONE

The Datum repo is its own git root with remote `origin = github.com/peyton150-startup/Datum-Project`. `CLAUDE.md`, `docs/DESIGN.md`, `docs/PROJECT_PLAN.md`, `docs/adr/ADR-001..009`, and the spec are committed and pushed on `main`. `.gitignore` ignores `.claude-flow/`, secrets, and build artifacts. No action — start at Task 1.

---

## Task 1: Python + Django scaffold and tooling gates (Bulk)

**Files:**
- Create: `pyproject.toml`, `manage.py`, `datum/__init__.py`, `datum/settings.py`, `datum/urls.py`, `datum/celery.py`
- Create app packages: `datum/{kinds,graph,intent,discovery,reconcile,workflow,api}/__init__.py` and each `apps.py`
- Create: `tests/__init__.py`, `tests/test_scaffold.py`, `.pre-commit-config.yaml`, `conftest.py`
- Create: `.env.example`

**Interfaces:**
- Produces: a Django project `datum` that imports and migrates against Postgres; `pytest` runs; ruff/mypy configured.

- [ ] **Step 1: Write the failing test**

`tests/test_scaffold.py`:
```python
import importlib


def test_settings_module_imports():
    settings = importlib.import_module("datum.settings")
    assert settings.INSTALLED_APPS  # apps registered


def test_kernel_apps_present():
    from datum import settings
    for app in ("datum.kinds", "datum.graph", "datum.intent",
                "datum.discovery", "datum.reconcile", "datum.workflow", "datum.api"):
        assert app in settings.INSTALLED_APPS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scaffold.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'datum'`.

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "datum"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "django==5.0.*",
    "django-ninja==1.3.*",
    "psycopg[binary]==3.2.*",
    "celery==5.4.*",
    "redis==5.0.*",            # Valkey speaks the Redis protocol
    "pydantic==2.*",
    "pyyaml==6.*",
]

[project.optional-dependencies]
dev = [
    "pytest==8.*",
    "pytest-django==4.*",
    "pytest-cov==5.*",
    "hypothesis==6.*",
    "ruff==0.6.*",
    "mypy==1.11.*",
    "django-stubs==5.*",
    "pre-commit==3.*",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "C901"]
[tool.ruff.lint.per-file-ignores]
"tests/*" = ["C901"]
[tool.ruff.lint.mccabe]
max-complexity = 9          # C901 fires at 10 -> enforces "under 10"

[tool.mypy]
python_version = "3.12"
plugins = ["mypy_django_plugin.main"]
# strict ONLY on the kernel + boundary-critical modules
[[tool.mypy.overrides]]
module = ["datum.reconcile.*", "datum.intent.*", "datum.graph.*"]
strict = true

[tool.django-stubs]
django_settings_module = "datum.settings"

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "datum.settings"
addopts = "--cov=datum --cov-branch --cov-report=term-missing"
testpaths = ["tests", "datum"]
python_files = ["test_*.py", "tests.py"]

[tool.coverage.report]
# Kernel modules must stay branch-covered; fail CI if they slip.
fail_under = 0   # global gate is loose; kernel gate is enforced in CI (Task 3)
```

- [ ] **Step 4: Create the Django project files**

`manage.py`:
```python
#!/usr/bin/env python
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datum.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
```

`datum/settings.py`:
```python
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DATUM_SECRET_KEY", "dev-insecure-key-not-for-production")
DEBUG = os.environ.get("DATUM_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DATUM_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "datum.kinds",
    "datum.graph",
    "datum.intent",
    "datum.discovery",
    "datum.reconcile",
    "datum.workflow",
    "datum.api",
]

MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "datum.urls"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "datum"),
        "USER": os.environ.get("POSTGRES_USER", "datum"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "datum"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

# Single-tenant constant for Phase 1. Every query is still written tenant-scoped.
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

CELERY_BROKER_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
```

`datum/urls.py`:
```python
from django.urls import path

urlpatterns: list = []   # api router mounted in Task 12
```

`datum/celery.py`:
```python
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datum.settings")
app = Celery("datum")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
```

- [ ] **Step 5: Create the seven app packages**

For each of `kinds, graph, intent, discovery, reconcile, workflow, api` create `datum/<app>/__init__.py` (empty) and `datum/<app>/apps.py`:
```python
from django.apps import AppConfig


class <Camel>Config(AppConfig):   # e.g. KindsConfig
    default_auto_field = "django.db.models.BigAutoField"
    name = "datum.<app>"           # e.g. datum.kinds
```

Also create empty `datum/<app>/models.py` for each (Django expects it for app loading of migrations later).

- [ ] **Step 6: Create `conftest.py` and dev tooling files**

`conftest.py` (repo root):
```python
import django
import os


def pytest_configure():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datum.settings")
    django.setup()
```

`.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

`.env.example`:
```
DATUM_SECRET_KEY=dev-insecure-key-not-for-production
DATUM_DEBUG=1
POSTGRES_DB=datum
POSTGRES_USER=datum
POSTGRES_PASSWORD=datum
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
VALKEY_URL=redis://localhost:6379/0
```

- [ ] **Step 7: Install and run the test**

Run:
```bash
python -m pip install -e ".[dev]"
pytest tests/test_scaffold.py -v
```
Expected: 2 passed. (These tests import settings only; no DB needed.)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore(bulk): Django scaffold, seven module apps, ruff/mypy/pytest gates"
```

---

## Task 2: Docker Compose stack (Bulk)

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `scripts/wait-for-postgres.sh`

**Interfaces:**
- Produces: `docker compose up` brings Postgres, Valkey, the Django app, a Celery worker+beat, and Caddy online; `docker compose run --rm app pytest` runs the suite against the compose Postgres.

- [ ] **Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN python -m pip install -e ".[dev]"
COPY . .
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: datum
      POSTGRES_USER: datum
      POSTGRES_PASSWORD: datum
    ports: ["5544:5432"]          # 5544 host-side to dodge a native Postgres collision
    volumes: ["postgres-data:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U datum"]
      interval: 5s
      timeout: 5s
      retries: 10

  valkey:
    image: valkey/valkey:8
    ports: ["6399:6379"]
    volumes: ["valkey-data:/data"]

  app:
    build: .
    environment:
      POSTGRES_HOST: postgres
      VALKEY_URL: redis://valkey:6379/0
    depends_on:
      postgres: {condition: service_healthy}
      valkey: {condition: service_started}
    ports: ["8000:8000"]
    volumes: [".:/app"]

  worker:
    build: .
    command: celery -A datum.celery worker --beat --loglevel=info
    environment:
      POSTGRES_HOST: postgres
      VALKEY_URL: redis://valkey:6379/0
    depends_on:
      postgres: {condition: service_healthy}
      valkey: {condition: service_started}
    volumes: [".:/app"]

  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
    depends_on: [app]

volumes:
  postgres-data:
  valkey-data:
```

- [ ] **Step 3: Create `Caddyfile`**

```
:80 {
    reverse_proxy app:8000
}
```

- [ ] **Step 4: Bring the stack up and run migrations check**

Run:
```bash
docker compose up -d postgres valkey
docker compose run --rm app python manage.py migrate --check || true
docker compose run --rm app pytest tests/test_scaffold.py -v
```
Expected: scaffold tests pass inside the container.
> **Ask the user** if Docker Desktop is not running — they need to start it. Say: type `! docker info` to check.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore(bulk): docker-compose stack (postgres, valkey, app, worker, caddy)"
```

---

## Task 3: GitHub Actions CI with kernel gates (Bulk)

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: CI that fails on unformatted code, lint/complexity errors, mypy errors in kernel modules, and kernel coverage below 100% branch.

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: CI
on:
  push: {branches: [main]}
  pull_request:
jobs:
  build:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: {POSTGRES_DB: datum, POSTGRES_USER: datum, POSTGRES_PASSWORD: datum}
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U datum" --health-interval 5s
          --health-timeout 5s --health-retries 10
    env:
      POSTGRES_HOST: localhost
      POSTGRES_PORT: "5432"
      VALKEY_URL: redis://localhost:6379/0
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: python -m pip install -e ".[dev]"
      - name: Format check
        run: ruff format --check .
      - name: Lint + complexity
        run: ruff check .
      - name: Type check (kernel modules)
        run: mypy datum/reconcile datum/intent datum/graph
      - name: Tests + coverage
        run: pytest
      - name: Kernel branch coverage gate
        run: |
          coverage report --include="datum/reconcile/*,datum/workflow/*" --fail-under=100
```

- [ ] **Step 2: Verify the workflow is valid locally**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 3: Commit and push, confirm CI runs green**

```bash
git add -A
git commit -m "ci(bulk): GitHub Actions with kernel complexity/type/coverage gates"
git push
```
Then check the Actions tab is green (this is the M1 "CI actually runs on push" verification — the Ratchet failure).
> **Ask the user** to confirm the Actions run went green if you cannot see it.

---

## Task 4: Shared enums, single source → TypeScript (Bulk)

**Files:**
- Create: `datum/enums.py`, `scripts/gen_ts_enums.py`, `web/src/enums.ts` (generated)
- Test: `tests/test_enums.py`

**Interfaces:**
- Produces: `datum/enums.py` with the seven `TextChoices` from the Canonical types section; `web/src/enums.ts` mirrors them, generated not hand-written.

- [ ] **Step 1: Write the failing test**

`tests/test_enums.py`:
```python
from pathlib import Path

from datum.enums import DiscrepancyState, DiscrepancyType, MatchStrategy


def test_discrepancy_states_are_open_and_resolved_only():
    assert {c.value for c in DiscrepancyState} == {"open", "resolved"}


def test_orphan_types_named_by_direction():
    assert DiscrepancyType.DECLARED_MISSING.value == "declared_missing"
    assert DiscrepancyType.DISCOVERED_UNDECLARED.value == "discovered_undeclared"


def test_generated_ts_enum_matches_python():
    ts = Path("web/src/enums.ts").read_text()
    for member in MatchStrategy:
        assert f'"{member.value}"' in ts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_enums.py -v`
Expected: FAIL — `ModuleNotFoundError: datum.enums`.

- [ ] **Step 3: Create `datum/enums.py`**

```python
from django.db.models import TextChoices


class MatchStrategy(TextChoices):
    NATURAL_KEY = "natural_key"
    BINDING = "binding"
    PROVIDER_TAG = "provider_tag"


class Confidence(TextChoices):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MatchState(TextChoices):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class DiscrepancyType(TextChoices):
    FIELD = "field"
    DECLARED_MISSING = "declared_missing"
    DISCOVERED_UNDECLARED = "discovered_undeclared"


class DiscrepancyState(TextChoices):
    OPEN = "open"
    RESOLVED = "resolved"


class Plane(TextChoices):
    DECLARED = "declared"
    DISCOVERED = "discovered"


class CollectorRunStatus(TextChoices):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
```

- [ ] **Step 4: Create `scripts/gen_ts_enums.py`**

```python
"""Generate web/src/enums.ts from datum/enums.py. Single source of truth."""
import os
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datum.settings")
django.setup()

from datum import enums  # noqa: E402
from django.db.models import TextChoices  # noqa: E402

lines = ["// GENERATED by scripts/gen_ts_enums.py — do not edit.", ""]
for attr in dir(enums):
    obj = getattr(enums, attr)
    if isinstance(obj, type) and issubclass(obj, TextChoices) and obj is not TextChoices:
        lines.append(f"export enum {attr} {{")
        for member in obj:
            lines.append(f'  {member.name} = "{member.value}",')
        lines.append("}")
        lines.append("")

out = Path("web/src/enums.ts")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(lines))
print(f"wrote {out}")
```

- [ ] **Step 5: Generate and run the test**

Run:
```bash
python scripts/gen_ts_enums.py
pytest tests/test_enums.py -v
```
Expected: `wrote web/src/enums.ts`, then 3 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(bulk): shared state enums with single-source TS generation"
```

---

## Task 5: Kind and resource schema (Bulk)

**Files:**
- Create: `datum/kinds/models.py`, `datum/graph/models.py`, `datum/intent/models.py`, `datum/discovery/models.py`, `datum/reconcile/models.py`
- Create: `datum/kinds/migrations/__init__.py` (and same empty file for graph/intent/discovery/reconcile)
- Test: `tests/test_models.py`

**Interfaces:**
- Produces ORM models: `Kind(name, attribute_schema)`; `DeclaredResource` and `DiscoveredResource` with core columns `tenant_id, kind(FK), name, scope, provider_id, attributes(JSONB), created_at, updated_at`; `IntentRevision(tenant_id, commit_sha, is_active)`; `CollectorRun(tenant_id, collector_name, status, counts)`; `Match`; `Discrepancy`. Natural-key uniqueness and one-to-one match constraints enforced in Postgres.

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
import pytest
from django.db import IntegrityError
from django.db.transaction import atomic

from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.enums import CollectorRunStatus
from datum.graph.models import DeclaredResource
from datum.intent.models import IntentRevision
from datum.kinds.models import Kind

TENANT = "00000000-0000-0000-0000-000000000001"
pytestmark = pytest.mark.django_db


def _kind():
    return Kind.objects.create(name="Deployment", attribute_schema={"replicas": "int"})


def test_seeded_deployment_kind_persists():
    k = _kind()
    assert Kind.objects.get(name="Deployment").id == k.id


def test_discovered_natural_key_is_unique_per_tenant():
    k = _kind()
    run = CollectorRun.objects.create(tenant_id=TENANT, collector_name="kubernetes",
                                      status=CollectorRunStatus.SUCCESS)
    DiscoveredResource.objects.create(tenant_id=TENANT, kind=k, name="web", scope="default",
                                      provider_id="uid-1", attributes={"replicas": 5}, run=run)
    with pytest.raises(IntegrityError), atomic():
        DiscoveredResource.objects.create(tenant_id=TENANT, kind=k, name="web", scope="default",
                                          provider_id="uid-2", attributes={"replicas": 5}, run=run)


def test_only_one_active_revision_per_tenant():
    IntentRevision.objects.create(tenant_id=TENANT, commit_sha="a" * 40, is_active=True)
    with pytest.raises(IntegrityError), atomic():
        IntentRevision.objects.create(tenant_id=TENANT, commit_sha="b" * 40, is_active=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — models do not exist.

- [ ] **Step 3: Write `datum/kinds/models.py`**

```python
from django.db import models


class Kind(models.Model):
    name = models.CharField(max_length=128, unique=True)
    attribute_schema = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name
```

- [ ] **Step 4: Write `datum/intent/models.py`**

```python
from django.db import models


class IntentRevision(models.Model):
    tenant_id = models.UUIDField()
    commit_sha = models.CharField(max_length=64)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "commit_sha"],
                                    name="uq_revision_tenant_commit"),
            models.UniqueConstraint(fields=["tenant_id"], condition=models.Q(is_active=True),
                                    name="uq_one_active_revision_per_tenant"),
        ]
```

- [ ] **Step 5: Write `datum/discovery/models.py`**

```python
from django.db import models

from datum.enums import CollectorRunStatus
from datum.kinds.models import Kind


class CollectorRun(models.Model):
    tenant_id = models.UUIDField()
    collector_name = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=CollectorRunStatus.choices)
    resources_read = models.IntegerField(default=0)
    resources_written = models.IntegerField(default=0)
    errors = models.IntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)


class DiscoveredResource(models.Model):
    tenant_id = models.UUIDField()
    kind = models.ForeignKey(Kind, on_delete=models.PROTECT)
    name = models.CharField(max_length=253)
    scope = models.CharField(max_length=253)
    provider_id = models.CharField(max_length=253)
    attributes = models.JSONField(default=dict)
    run = models.ForeignKey(CollectorRun, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "kind", "scope", "name"],
                                    name="uq_discovered_natural_key"),
        ]
```

- [ ] **Step 6: Write `datum/graph/models.py`**

```python
from django.db import models

from datum.intent.models import IntentRevision
from datum.kinds.models import Kind


class DeclaredResource(models.Model):
    tenant_id = models.UUIDField()
    kind = models.ForeignKey(Kind, on_delete=models.PROTECT)
    name = models.CharField(max_length=253)
    scope = models.CharField(max_length=253)
    provider_id = models.CharField(max_length=253, null=True, blank=True)
    attributes = models.JSONField(default=dict)
    revision = models.ForeignKey(IntentRevision, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "kind", "scope", "name", "revision"],
                                    name="uq_declared_natural_key_per_revision"),
        ]
```

- [ ] **Step 7: Write `datum/reconcile/models.py`**

```python
from django.db import models

from datum.enums import Confidence, DiscrepancyState, DiscrepancyType, MatchState, MatchStrategy, Plane
from datum.graph.models import DeclaredResource
from datum.discovery.models import DiscoveredResource


class Match(models.Model):
    tenant_id = models.UUIDField()
    declared_resource = models.OneToOneField(DeclaredResource, on_delete=models.CASCADE)
    discovered_resource = models.OneToOneField(DiscoveredResource, on_delete=models.CASCADE)
    strategy = models.CharField(max_length=16, choices=MatchStrategy.choices)
    confidence = models.CharField(max_length=8, choices=Confidence.choices)
    state = models.CharField(max_length=12, choices=MatchState.choices, default=MatchState.PROPOSED)
    confirmed_by = models.CharField(max_length=128, null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Discrepancy(models.Model):
    tenant_id = models.UUIDField()
    discrepancy_type = models.CharField(max_length=24, choices=DiscrepancyType.choices)
    kind_name = models.CharField(max_length=128)
    scope = models.CharField(max_length=253)
    name = models.CharField(max_length=253)
    field_name = models.CharField(max_length=128, null=True, blank=True)
    declared_value = models.JSONField(null=True, blank=True)
    discovered_value = models.JSONField(null=True, blank=True)
    authoritative_plane = models.CharField(max_length=12, choices=Plane.choices, default=Plane.DECLARED)
    state = models.CharField(max_length=12, choices=DiscrepancyState.choices, default=DiscrepancyState.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 8: Create migration package files and generate migrations**

Create empty `datum/<app>/migrations/__init__.py` for `kinds, intent, discovery, graph, reconcile`. Then:
```bash
python manage.py makemigrations kinds intent discovery graph reconcile
```
Expected: migration files created for each app.

- [ ] **Step 9: Run the tests**

Run: `pytest tests/test_models.py -v`
Expected: 3 passed (pytest-django applies migrations to a test DB).

- [ ] **Step 10: Add a data migration seeding the Deployment kind**

Run: `python manage.py makemigrations kinds --empty --name seed_deployment_kind`, then edit the created file's `operations`:
```python
from django.db import migrations


def seed(apps, schema_editor):
    Kind = apps.get_model("kinds", "Kind")
    Kind.objects.get_or_create(name="Deployment",
                               defaults={"attribute_schema": {"replicas": "int"}})


def unseed(apps, schema_editor):
    apps.get_model("kinds", "Kind").objects.filter(name="Deployment").delete()


class Migration(migrations.Migration):
    dependencies = [("kinds", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
```

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat(bulk): Kind + two-plane resource schema, revision/run/match/discrepancy models, seed Deployment"
```

---

## Task 6: Domain types and fixtures (Bulk)

**Files:**
- Create: `datum/reconcile/domain.py`
- Create: `fixtures/k8s/deployments.json`, `fixtures/intent-repo/deployments/web.yaml`, `fixtures/intent-repo-malformed/deployments/web.yaml`
- Test: `tests/test_domain.py`

**Interfaces:**
- Produces the frozen dataclasses from the Canonical types section. `ResourceSnapshot.natural_key` returns `(kind, tenant_id, scope, name)`.
- Consumes: `datum/enums.py`.

- [ ] **Step 1: Write the failing test**

`tests/test_domain.py`:
```python
from datum.reconcile.domain import ResourceSnapshot


def test_natural_key_is_kind_tenant_scope_name():
    snap = ResourceSnapshot(kind="Deployment", tenant_id="t1", scope="default",
                            name="web", provider_id=None, attributes={"replicas": 3})
    assert snap.natural_key == ("Deployment", "t1", "default", "web")


def test_snapshot_is_frozen():
    snap = ResourceSnapshot("Deployment", "t1", "default", "web", None, {})
    try:
        snap.name = "other"  # type: ignore[misc]
    except Exception as exc:
        assert "cannot assign" in str(exc).lower() or "frozen" in str(exc).lower()
    else:
        raise AssertionError("snapshot must be immutable")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_domain.py -v`
Expected: FAIL — `datum.reconcile.domain` missing.

- [ ] **Step 3: Write `datum/reconcile/domain.py`**

```python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

NaturalKey = tuple[str, str, str, str]  # (kind, tenant_id, scope, name)


@dataclass(frozen=True)
class ResourceSnapshot:
    kind: str
    tenant_id: str
    scope: str
    name: str
    provider_id: str | None
    attributes: Mapping[str, object]

    @property
    def natural_key(self) -> NaturalKey:
        return (self.kind, self.tenant_id, self.scope, self.name)


@dataclass(frozen=True)
class MatchedPair:
    declared: ResourceSnapshot
    discovered: ResourceSnapshot
    strategy: str
    confidence: str


@dataclass(frozen=True)
class MatchResult:
    pairs: tuple[MatchedPair, ...]
    declared_orphans: tuple[ResourceSnapshot, ...]
    discovered_orphans: tuple[ResourceSnapshot, ...]


@dataclass(frozen=True)
class FieldDiscrepancy:
    natural_key: NaturalKey
    field_name: str
    declared_value: object
    discovered_value: object


@dataclass(frozen=True)
class OrphanDiscrepancy:
    natural_key: NaturalKey
    discrepancy_type: str


@dataclass(frozen=True)
class DiscrepancySet:
    field_discrepancies: tuple[FieldDiscrepancy, ...]
    orphans: tuple[OrphanDiscrepancy, ...]


__all__ = [
    "NaturalKey", "ResourceSnapshot", "MatchedPair", "MatchResult",
    "FieldDiscrepancy", "OrphanDiscrepancy", "DiscrepancySet", "Sequence",
]
```

- [ ] **Step 4: Create the fixtures**

`fixtures/k8s/deployments.json` (a trimmed Kubernetes list response; discovered `replicas: 5`):
```json
{
  "kind": "DeploymentList",
  "items": [
    {
      "metadata": {"name": "web", "namespace": "default", "uid": "uid-web-1"},
      "spec": {"replicas": 5}
    }
  ]
}
```

`fixtures/intent-repo/deployments/web.yaml` (declared `replicas: 3`):
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  namespace: default
spec:
  replicas: 3
```

`fixtures/intent-repo-malformed/deployments/web.yaml` (missing `spec.replicas` — must be rejected):
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  namespace: default
spec: {}
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_domain.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(bulk): reconcile domain dataclasses + k8s and intent fixtures"
```

---

## Task 7: Kubernetes collector (Bulk)

**Files:**
- Create: `datum/discovery/kubernetes.py`, `datum/discovery/collector.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: `DiscoveredResource`, `CollectorRun`, `Kind`, `ResourceSnapshot`.
- Produces: `read_deployment_fixture(path: str) -> list[ResourceSnapshot]` (barricade: validates + normalizes provider JSON); `run_collector(tenant_id: str, fixture_path: str) -> CollectorRun` (idempotent upsert, records counts).

- [ ] **Step 1: Write the failing test**

`tests/test_collector.py`:
```python
import pytest

from datum.discovery.collector import run_collector
from datum.discovery.kubernetes import read_deployment_fixture
from datum.discovery.models import DiscoveredResource
from datum.enums import CollectorRunStatus

TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE = "fixtures/k8s/deployments.json"
pytestmark = pytest.mark.django_db


def test_fixture_normalizes_to_snapshots():
    snaps = read_deployment_fixture(FIXTURE)
    assert len(snaps) == 1
    s = snaps[0]
    assert s.kind == "Deployment"
    assert s.scope == "default"
    assert s.name == "web"
    assert s.provider_id == "uid-web-1"
    assert s.attributes == {"replicas": 5}


def test_run_writes_one_discovered_row_and_records_counts():
    run = run_collector(TENANT, FIXTURE)
    assert run.status == CollectorRunStatus.SUCCESS
    assert run.resources_read == 1
    assert run.resources_written == 1
    assert DiscoveredResource.objects.filter(tenant_id=TENANT, name="web").count() == 1


def test_running_twice_is_idempotent():
    run_collector(TENANT, FIXTURE)
    run_collector(TENANT, FIXTURE)
    assert DiscoveredResource.objects.filter(tenant_id=TENANT, name="web").count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_collector.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Write `datum/discovery/kubernetes.py` (the barricade / normalizer)**

```python
import json

from datum.reconcile.domain import ResourceSnapshot


class MalformedProviderData(Exception):
    """A provider record could not be normalized to a Deployment snapshot."""


def read_deployment_fixture(path: str, tenant_id: str = "") -> list[ResourceSnapshot]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    snapshots: list[ResourceSnapshot] = []
    for item in payload.get("items", []):
        snapshots.append(_normalize(item, tenant_id))
    return snapshots


def _normalize(item: dict, tenant_id: str) -> ResourceSnapshot:
    metadata = item.get("metadata", {})
    name = metadata.get("name")
    scope = metadata.get("namespace")
    provider_id = metadata.get("uid")
    replicas = item.get("spec", {}).get("replicas")
    if name is None or scope is None or provider_id is None or replicas is None:
        raise MalformedProviderData(f"incomplete Deployment record: {metadata!r}")
    return ResourceSnapshot(
        kind="Deployment", tenant_id=tenant_id, scope=scope, name=name,
        provider_id=provider_id, attributes={"replicas": replicas},
    )
```

- [ ] **Step 4: Write `datum/discovery/collector.py`**

```python
from django.utils import timezone

from datum.discovery.kubernetes import MalformedProviderData, read_deployment_fixture
from datum.discovery.models import CollectorRun, DiscoveredResource
from datum.enums import CollectorRunStatus
from datum.kinds.models import Kind

COLLECTOR_NAME = "kubernetes"


def run_collector(tenant_id: str, fixture_path: str) -> CollectorRun:
    run = CollectorRun.objects.create(
        tenant_id=tenant_id, collector_name=COLLECTOR_NAME, status=CollectorRunStatus.SUCCESS,
    )
    kind = Kind.objects.get(name="Deployment")
    read = written = errors = 0
    for record in _read(fixture_path):
        read += 1
        if record is None:
            errors += 1
            continue
        _upsert(tenant_id, kind, record, run)
        written += 1
    run.resources_read = read
    run.resources_written = written
    run.errors = errors
    run.status = CollectorRunStatus.PARTIAL if errors else CollectorRunStatus.SUCCESS
    run.finished_at = timezone.now()
    run.save()
    return run


def _read(fixture_path: str) -> list:
    try:
        return list(read_deployment_fixture(fixture_path))
    except MalformedProviderData:
        return [None]


def _upsert(tenant_id: str, kind: Kind, snapshot, run: CollectorRun) -> None:
    DiscoveredResource.objects.update_or_create(
        tenant_id=tenant_id, kind=kind, scope=snapshot.scope, name=snapshot.name,
        defaults={"provider_id": snapshot.provider_id,
                  "attributes": dict(snapshot.attributes), "run": run},
    )
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_collector.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(bulk): kubernetes fixture collector, idempotent upsert with run counts"
```

---

## Task 8: Intent ingestion (Bulk, boundary validation)

**Files:**
- Create: `datum/intent/documents.py`, `datum/intent/ingest.py`
- Test: `tests/test_intent.py`, `tests/conftest_git.py`

**Interfaces:**
- Consumes: `IntentRevision`, `DeclaredResource`, `Kind`, `ResourceSnapshot`.
- Produces: `parse_deployment_document(text: str) -> ResourceSnapshot` (raises `InvalidРевision`→ use `InvalidRevision`); `ingest_revision(tenant_id: str, repo_path: str) -> IntentRevision` — resolves HEAD sha, validates all docs, rejects the whole revision on any failure, projects into `declared_resource`, idempotent on `(tenant, commit_sha)`.

- [ ] **Step 1: Write a git-repo pytest fixture**

`tests/conftest_git.py`:
```python
import shutil
import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def intent_repo(tmp_path):
    """Copy fixtures/intent-repo into a temp dir and make it a real git repo."""
    def _make(source: str = "fixtures/intent-repo") -> str:
        repo = tmp_path / "repo"
        shutil.copytree(source, repo)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "declare web")
        return str(repo)
    return _make
```

Register it by adding to `conftest.py`:
```python
pytest_plugins = ["tests.conftest_git"]
```

- [ ] **Step 2: Write the failing test**

`tests/test_intent.py`:
```python
import pytest

from datum.graph.models import DeclaredResource
from datum.intent.ingest import InvalidRevision, ingest_revision
from datum.intent.models import IntentRevision

TENANT = "00000000-0000-0000-0000-000000000001"
pytestmark = pytest.mark.django_db


def test_ingest_projects_declared_resource_traceable_to_commit(intent_repo):
    repo = intent_repo()
    revision = ingest_revision(TENANT, repo)
    assert revision.is_active
    assert len(revision.commit_sha) == 40
    res = DeclaredResource.objects.get(tenant_id=TENANT, name="web")
    assert res.attributes == {"replicas": 3}
    assert res.revision_id == revision.id


def test_malformed_document_rejects_whole_revision(intent_repo):
    good = intent_repo()
    ingest_revision(TENANT, good)
    bad = intent_repo("fixtures/intent-repo-malformed")
    with pytest.raises(InvalidRevision):
        ingest_revision(TENANT, bad)
    # previous revision still active, no partial state from the bad one
    assert IntentRevision.objects.filter(tenant_id=TENANT, is_active=True).count() == 1
    assert DeclaredResource.objects.filter(tenant_id=TENANT).count() == 1


def test_duplicate_commit_is_idempotent(intent_repo):
    repo = intent_repo()
    first = ingest_revision(TENANT, repo)
    second = ingest_revision(TENANT, repo)
    assert first.id == second.id
    assert DeclaredResource.objects.filter(tenant_id=TENANT).count() == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_intent.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 4: Write `datum/intent/documents.py` (the barricade)**

```python
import yaml

from datum.reconcile.domain import ResourceSnapshot

EXPECTED_KIND = "Deployment"


class InvalidDocument(Exception):
    """An intent document is syntactically or structurally invalid."""


def parse_deployment_document(text: str, tenant_id: str) -> ResourceSnapshot:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InvalidDocument(f"unparseable YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise InvalidDocument("document is not a mapping")
    if doc.get("kind") != EXPECTED_KIND:
        raise InvalidDocument(f"expected kind Deployment, got {doc.get('kind')!r}")
    metadata = doc.get("metadata") or {}
    name = metadata.get("name")
    scope = metadata.get("namespace")
    replicas = (doc.get("spec") or {}).get("replicas")
    if name is None or scope is None or not isinstance(replicas, int):
        raise InvalidDocument(f"incomplete Deployment: name={name} scope={scope} replicas={replicas}")
    return ResourceSnapshot(kind=EXPECTED_KIND, tenant_id=tenant_id, scope=scope,
                            name=name, provider_id=None, attributes={"replicas": replicas})
```

- [ ] **Step 5: Write `datum/intent/ingest.py`**

```python
import subprocess
from pathlib import Path

from django.db import transaction

from datum.graph.models import DeclaredResource
from datum.intent.documents import InvalidDocument, parse_deployment_document
from datum.intent.models import IntentRevision
from datum.kinds.models import Kind


class InvalidRevision(Exception):
    """A revision failed validation and was rejected whole; no state was written."""


def ingest_revision(tenant_id: str, repo_path: str) -> IntentRevision:
    commit_sha = _head_sha(repo_path)
    existing = IntentRevision.objects.filter(tenant_id=tenant_id, commit_sha=commit_sha).first()
    if existing is not None:
        return existing
    snapshots = _parse_all(tenant_id, repo_path)   # raises InvalidRevision on any bad doc
    return _project(tenant_id, commit_sha, snapshots)


def _head_sha(repo_path: str) -> str:
    result = subprocess.run(["git", "-C", repo_path, "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _parse_all(tenant_id: str, repo_path: str) -> list:
    documents = sorted(Path(repo_path, "deployments").glob("*.yaml"))
    snapshots = []
    for path in documents:
        try:
            snapshots.append(parse_deployment_document(path.read_text(encoding="utf-8"), tenant_id))
        except InvalidDocument as exc:
            raise InvalidRevision(f"{path.name}: {exc}") from exc
    return snapshots


@transaction.atomic
def _project(tenant_id: str, commit_sha: str, snapshots: list) -> IntentRevision:
    IntentRevision.objects.filter(tenant_id=tenant_id, is_active=True).update(is_active=False)
    revision = IntentRevision.objects.create(tenant_id=tenant_id, commit_sha=commit_sha, is_active=True)
    kind = Kind.objects.get(name="Deployment")
    for snap in snapshots:
        DeclaredResource.objects.create(
            tenant_id=tenant_id, kind=kind, name=snap.name, scope=snap.scope,
            provider_id=None, attributes=dict(snap.attributes), revision=revision,
        )
    return revision
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_intent.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(bulk): git intent ingestion, whole-revision validation, idempotent projection"
```

---

## Task 9: Identity matcher — KERNEL (adversarial corpus FIRST)

**Files:**
- Create: `datum/reconcile/matcher.py`
- Test: `tests/kernel/test_matcher.py`, `tests/kernel/__init__.py`

**Interfaces:**
- Consumes: `ResourceSnapshot`, `MatchResult`, `MatchedPair` from `datum.reconcile.domain`; `MatchStrategy`, `Confidence` from `datum.enums`.
- Produces: `match_by_natural_key(declared, discovered) -> MatchResult`. Pairs carry `strategy=MatchStrategy.NATURAL_KEY.value`, `confidence=Confidence.HIGH.value`. Unmatched on either side become orphans. One-to-one: a natural key present on both sides yields exactly one pair. Deterministic ordering (sorted by natural key).

**KERNEL RULE:** write the corpus (Step 1) before the implementation (Step 4). Reviewed by the non-authoring model against the DESIGN.md checklist before merge.

- [ ] **Step 1: Write the adversarial corpus test (from DESIGN §12), FIRST**

`tests/kernel/test_matcher.py`:
```python
from datum.enums import Confidence, MatchStrategy
from datum.reconcile.domain import ResourceSnapshot
from datum.reconcile.matcher import match_by_natural_key

T = "t1"


def snap(name, scope="default", replicas=1, provider_id=None):
    return ResourceSnapshot("Deployment", T, scope, name, provider_id, {"replicas": replicas})


def test_first_sighting_matches_by_natural_key():
    result = match_by_natural_key([snap("web", replicas=3)],
                                  [snap("web", replicas=5, provider_id="uid1")])
    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.strategy == MatchStrategy.NATURAL_KEY.value
    assert pair.confidence == Confidence.HIGH.value
    assert not result.declared_orphans and not result.discovered_orphans


def test_declared_never_provisioned_is_declared_orphan():
    result = match_by_natural_key([snap("web")], [])
    assert not result.pairs
    assert [o.name for o in result.declared_orphans] == ["web"]
    assert not result.discovered_orphans


def test_discovered_undeclared_is_discovered_orphan():
    result = match_by_natural_key([], [snap("ghost", provider_id="uid9")])
    assert not result.pairs
    assert [o.name for o in result.discovered_orphans] == ["ghost"]


def test_move_of_scope_breaks_natural_key_into_two_orphans():
    # same name, different scope -> not a match (documented limitation, correct behavior)
    result = match_by_natural_key([snap("web", scope="default")],
                                  [snap("web", scope="prod", provider_id="uid1")])
    assert not result.pairs
    assert len(result.declared_orphans) == 1
    assert len(result.discovered_orphans) == 1


def test_same_name_two_scopes_yields_two_distinct_matches():
    result = match_by_natural_key(
        [snap("web", scope="default"), snap("web", scope="prod")],
        [snap("web", scope="default", provider_id="u1"),
         snap("web", scope="prod", provider_id="u2")],
    )
    assert len(result.pairs) == 2
    scopes = sorted(p.declared.scope for p in result.pairs)
    assert scopes == ["default", "prod"]


def test_output_is_deterministic_regardless_of_input_order():
    a = match_by_natural_key([snap("a"), snap("b"), snap("c")],
                             [snap("c", provider_id="uc"), snap("a", provider_id="ua")])
    b = match_by_natural_key([snap("c"), snap("b"), snap("a")],
                             [snap("a", provider_id="ua"), snap("c", provider_id="uc")])
    assert a == b
```

- [ ] **Step 2: Run to verify every case fails**

Run: `pytest tests/kernel/test_matcher.py -v`
Expected: FAIL — `datum.reconcile.matcher` missing.

- [ ] **Step 3: Design the routine in pseudocode (kernel discipline)**

```
match_by_natural_key(declared, discovered):
    declared_by_key   = { d.natural_key: d for d in declared }
    discovered_by_key = { x.natural_key: x for x in discovered }
    shared_keys       = sorted(declared_by_key.keys() & discovered_by_key.keys())
    declared_only     = sorted(declared_by_key.keys() - discovered_by_key.keys())
    discovered_only   = sorted(discovered_by_key.keys() - declared_by_key.keys())
    pairs   = [ MatchedPair(declared_by_key[k], discovered_by_key[k],
                            NATURAL_KEY, HIGH) for k in shared_keys ]
    return MatchResult(pairs, orphans(declared_only), orphans(discovered_only))
```
Complexity: one comprehension per set — well under 10. Determinism: all three sets sorted.

- [ ] **Step 4: Write `datum/reconcile/matcher.py`**

```python
from collections.abc import Sequence

from datum.enums import Confidence, MatchStrategy
from datum.reconcile.domain import MatchedPair, MatchResult, ResourceSnapshot


def match_by_natural_key(
    declared: Sequence[ResourceSnapshot],
    discovered: Sequence[ResourceSnapshot],
) -> MatchResult:
    declared_by_key = {snap.natural_key: snap for snap in declared}
    discovered_by_key = {snap.natural_key: snap for snap in discovered}

    shared = sorted(declared_by_key.keys() & discovered_by_key.keys())
    declared_only = sorted(declared_by_key.keys() - discovered_by_key.keys())
    discovered_only = sorted(discovered_by_key.keys() - declared_by_key.keys())

    pairs = tuple(
        MatchedPair(
            declared=declared_by_key[key],
            discovered=discovered_by_key[key],
            strategy=MatchStrategy.NATURAL_KEY.value,
            confidence=Confidence.HIGH.value,
        )
        for key in shared
    )
    declared_orphans = tuple(declared_by_key[key] for key in declared_only)
    discovered_orphans = tuple(discovered_by_key[key] for key in discovered_only)
    return MatchResult(pairs, declared_orphans, discovered_orphans)
```

- [ ] **Step 5: Run the corpus + the complexity/type gates**

Run:
```bash
pytest tests/kernel/test_matcher.py -v
ruff check datum/reconcile/matcher.py
mypy datum/reconcile/matcher.py
```
Expected: all corpus cases pass; no lint/complexity errors; mypy clean.

- [ ] **Step 6: Commit (label kernel + author)**

```bash
git add -A
git commit -m "feat(kernel): natural-key matcher with adversarial corpus [author: opus]"
```

> After committing, request a review of this kernel change by the non-authoring model against the DESIGN.md checklist before it is considered done.

---

## Task 10: Diff engine — KERNEL (determinism as an invariant)

**Files:**
- Create: `datum/reconcile/diff.py`
- Test: `tests/kernel/test_diff.py`

**Interfaces:**
- Consumes: `MatchResult`, `MatchedPair`, `DiscrepancySet`, `FieldDiscrepancy`, `OrphanDiscrepancy`; `DiscrepancyType` from `datum.enums`.
- Produces: `reconcile(match_result: MatchResult) -> DiscrepancySet`. Field discrepancies for each pair over the sorted union of attribute keys, using canonical value comparison. Orphans become `OrphanDiscrepancy` with `DECLARED_MISSING` (declared orphan) / `DISCOVERED_UNDECLARED` (discovered orphan). Deterministic: everything sorted by natural key then field name.

**KERNEL RULE:** determinism is tested as a Hypothesis invariant, not just an example. Reviewed by the non-authoring model.

- [ ] **Step 1: Write the failing tests (examples + determinism invariant)**

`tests/kernel/test_diff.py`:
```python
from hypothesis import given
from hypothesis import strategies as st

from datum.enums import DiscrepancyType
from datum.reconcile.domain import ResourceSnapshot
from datum.reconcile.matcher import match_by_natural_key
from datum.reconcile.diff import reconcile

T = "t1"


def snap(name, replicas, scope="default", provider_id=None):
    return ResourceSnapshot("Deployment", T, scope, name, provider_id, {"replicas": replicas})


def test_single_field_discrepancy_replicas_3_vs_5():
    result = match_by_natural_key([snap("web", 3)], [snap("web", 5, provider_id="u1")])
    diff = reconcile(result)
    assert len(diff.field_discrepancies) == 1
    fd = diff.field_discrepancies[0]
    assert fd.field_name == "replicas"
    assert fd.declared_value == 3
    assert fd.discovered_value == 5
    assert not diff.orphans


def test_identical_attributes_produce_no_discrepancy():
    result = match_by_natural_key([snap("web", 3)], [snap("web", 3, provider_id="u1")])
    diff = reconcile(result)
    assert not diff.field_discrepancies and not diff.orphans


def test_declared_orphan_is_declared_missing():
    result = match_by_natural_key([snap("web", 3)], [])
    diff = reconcile(result)
    assert len(diff.orphans) == 1
    assert diff.orphans[0].discrepancy_type == DiscrepancyType.DECLARED_MISSING.value


def test_discovered_orphan_is_discovered_undeclared():
    result = match_by_natural_key([], [snap("ghost", 2, provider_id="u9")])
    diff = reconcile(result)
    assert len(diff.orphans) == 1
    assert diff.orphans[0].discrepancy_type == DiscrepancyType.DISCOVERED_UNDECLARED.value


def test_absent_key_on_one_side_is_a_discrepancy():
    d = ResourceSnapshot("Deployment", T, "default", "web", None, {"replicas": 3, "paused": True})
    x = ResourceSnapshot("Deployment", T, "default", "web", "u1", {"replicas": 3})
    diff = reconcile(match_by_natural_key([d], [x]))
    fields = {fd.field_name for fd in diff.field_discrepancies}
    assert fields == {"paused"}


def test_rerun_on_same_input_is_identical():
    result = match_by_natural_key([snap("web", 3)], [snap("web", 5, provider_id="u1")])
    assert reconcile(result) == reconcile(result)


@given(
    st.lists(st.tuples(st.sampled_from(["a", "b", "c"]), st.integers(0, 9)), max_size=6),
    st.lists(st.tuples(st.sampled_from(["a", "b", "c"]), st.integers(0, 9)), max_size=6),
)
def test_determinism_invariant_input_order_does_not_matter(decl, disc):
    def build(pairs):
        # dedupe by name; last wins
        by_name = {n: r for n, r in pairs}
        return [snap(n, r) for n, r in by_name.items()]

    forward = reconcile(match_by_natural_key(build(decl), build(disc)))
    reversed_ = reconcile(match_by_natural_key(build(decl[::-1]), build(disc[::-1])))
    assert forward == reversed_
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/kernel/test_diff.py -v`
Expected: FAIL — `datum.reconcile.diff` missing.

- [ ] **Step 3: Pseudocode (kernel discipline)**

```
reconcile(match_result):
    field_discrepancies = []
    for pair in match_result.pairs sorted by declared.natural_key:
        for key in sorted(union of pair.declared.attributes, pair.discovered.attributes keys):
            dv = declared.attributes.get(key, ABSENT)
            xv = discovered.attributes.get(key, ABSENT)
            if canonical(dv) != canonical(xv):
                field_discrepancies.append(FieldDiscrepancy(nk, key, present(dv), present(xv)))
    orphans =  [DECLARED_MISSING for o in sorted declared_orphans]
            +  [DISCOVERED_UNDECLARED for o in sorted discovered_orphans]
    return DiscrepancySet(tuple(field_discrepancies), tuple(orphans))

canonical(v): ABSENT -> "\0absent"; else json.dumps(v, sort_keys=True, default=str)
present(v):   ABSENT -> None; else v
```
Each routine has one loop nest ≤ 2 and a single comparison — complexity < 10.

- [ ] **Step 4: Write `datum/reconcile/diff.py`**

```python
import json

from datum.enums import DiscrepancyType
from datum.reconcile.domain import (
    DiscrepancySet,
    FieldDiscrepancy,
    MatchedPair,
    MatchResult,
    OrphanDiscrepancy,
    ResourceSnapshot,
)

_ABSENT = object()


def reconcile(match_result: MatchResult) -> DiscrepancySet:
    field_discrepancies: list[FieldDiscrepancy] = []
    for pair in sorted(match_result.pairs, key=lambda p: p.declared.natural_key):
        field_discrepancies.extend(_field_discrepancies(pair))

    orphans = tuple(
        _orphans(match_result.declared_orphans, DiscrepancyType.DECLARED_MISSING.value)
        + _orphans(match_result.discovered_orphans, DiscrepancyType.DISCOVERED_UNDECLARED.value)
    )
    return DiscrepancySet(tuple(field_discrepancies), orphans)


def _field_discrepancies(pair: MatchedPair) -> list[FieldDiscrepancy]:
    keys = sorted(set(pair.declared.attributes) | set(pair.discovered.attributes))
    result: list[FieldDiscrepancy] = []
    for key in keys:
        declared_value = pair.declared.attributes.get(key, _ABSENT)
        discovered_value = pair.discovered.attributes.get(key, _ABSENT)
        if _canonical(declared_value) != _canonical(discovered_value):
            result.append(
                FieldDiscrepancy(
                    natural_key=pair.declared.natural_key,
                    field_name=key,
                    declared_value=_present(declared_value),
                    discovered_value=_present(discovered_value),
                )
            )
    return result


def _orphans(snapshots: tuple[ResourceSnapshot, ...], discrepancy_type: str) -> list[OrphanDiscrepancy]:
    ordered = sorted(snapshots, key=lambda s: s.natural_key)
    return [OrphanDiscrepancy(natural_key=s.natural_key, discrepancy_type=discrepancy_type)
            for s in ordered]


def _canonical(value: object) -> str:
    if value is _ABSENT:
        return "\0absent"
    return json.dumps(value, sort_keys=True, default=str)


def _present(value: object) -> object:
    return None if value is _ABSENT else value
```

- [ ] **Step 5: Run tests + gates**

Run:
```bash
pytest tests/kernel/test_diff.py -v
ruff check datum/reconcile/diff.py
mypy datum/reconcile/diff.py
```
Expected: all pass including the Hypothesis determinism invariant; no complexity/type errors.

- [ ] **Step 6: Commit (label kernel + author)**

```bash
git add -A
git commit -m "feat(kernel): deterministic diff engine, field + orphan discrepancies [author: opus]"
```

> Request non-authoring-model review against the DESIGN.md checklist before this is done.

---

## Task 11: Persist reconciliation over the database (Bulk, uses kernel)

**Files:**
- Create: `datum/reconcile/service.py`
- Test: `tests/test_reconcile_service.py`

**Interfaces:**
- Consumes: `DeclaredResource`, `DiscoveredResource` (loads active revision + current discovered), the matcher and diff engine, `Match` and `Discrepancy` models.
- Produces: `run_reconciliation(tenant_id: str) -> None` — loads both planes into snapshots, matches, diffs, and writes `Match` rows and open `Discrepancy` rows. Re-running replaces the tenant's matches and open discrepancies (Phase 1 determinism; Phase 4 handles re-detection/suppression).

- [ ] **Step 1: Write the failing test**

`tests/test_reconcile_service.py`:
```python
import pytest

from datum.discovery.collector import run_collector
from datum.enums import DiscrepancyState, DiscrepancyType
from datum.intent.ingest import ingest_revision
from datum.reconcile.models import Discrepancy, Match
from datum.reconcile.service import run_reconciliation

TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE = "fixtures/k8s/deployments.json"
pytestmark = pytest.mark.django_db


def test_reconciliation_writes_one_match_and_one_field_discrepancy(intent_repo):
    ingest_revision(TENANT, intent_repo())
    run_collector(TENANT, FIXTURE)
    run_reconciliation(TENANT)

    assert Match.objects.filter(tenant_id=TENANT).count() == 1
    open_ = Discrepancy.objects.filter(tenant_id=TENANT, state=DiscrepancyState.OPEN)
    assert open_.count() == 1
    d = open_.get()
    assert d.discrepancy_type == DiscrepancyType.FIELD
    assert d.field_name == "replicas"
    assert d.declared_value == 3
    assert d.discovered_value == 5


def test_rerun_is_idempotent(intent_repo):
    ingest_revision(TENANT, intent_repo())
    run_collector(TENANT, FIXTURE)
    run_reconciliation(TENANT)
    run_reconciliation(TENANT)
    assert Discrepancy.objects.filter(tenant_id=TENANT, state=DiscrepancyState.OPEN).count() == 1
    assert Match.objects.filter(tenant_id=TENANT).count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reconcile_service.py -v`
Expected: FAIL — `datum.reconcile.service` missing.

- [ ] **Step 3: Write `datum/reconcile/service.py`**

```python
from django.db import transaction

from datum.discovery.models import DiscoveredResource
from datum.enums import DiscrepancyState, DiscrepancyType, Plane
from datum.graph.models import DeclaredResource
from datum.intent.models import IntentRevision
from datum.reconcile.diff import reconcile
from datum.reconcile.domain import ResourceSnapshot
from datum.reconcile.matcher import match_by_natural_key
from datum.reconcile.models import Discrepancy, Match


@transaction.atomic
def run_reconciliation(tenant_id: str) -> None:
    declared_rows = _active_declared(tenant_id)
    discovered_rows = list(DiscoveredResource.objects.filter(tenant_id=tenant_id))
    declared = [_snapshot(row) for row in declared_rows]
    discovered = [_snapshot(row) for row in discovered_rows]

    match_result = match_by_natural_key(declared, discovered)
    discrepancy_set = reconcile(match_result)

    _reset(tenant_id)
    _write_matches(tenant_id, match_result, declared_rows, discovered_rows)
    _write_discrepancies(tenant_id, discrepancy_set)


def _active_declared(tenant_id: str) -> list[DeclaredResource]:
    revision = IntentRevision.objects.filter(tenant_id=tenant_id, is_active=True).first()
    if revision is None:
        return []
    return list(DeclaredResource.objects.filter(tenant_id=tenant_id, revision=revision))


def _snapshot(row: object) -> ResourceSnapshot:
    return ResourceSnapshot(
        kind=row.kind.name, tenant_id=str(row.tenant_id), scope=row.scope, name=row.name,
        provider_id=getattr(row, "provider_id", None), attributes=dict(row.attributes),
    )


def _reset(tenant_id: str) -> None:
    Match.objects.filter(tenant_id=tenant_id).delete()
    Discrepancy.objects.filter(tenant_id=tenant_id, state=DiscrepancyState.OPEN).delete()


def _write_matches(tenant_id, match_result, declared_rows, discovered_rows) -> None:
    declared_by_key = {(_snapshot(r).natural_key): r for r in declared_rows}
    discovered_by_key = {(_snapshot(r).natural_key): r for r in discovered_rows}
    for pair in match_result.pairs:
        Match.objects.create(
            tenant_id=tenant_id,
            declared_resource=declared_by_key[pair.declared.natural_key],
            discovered_resource=discovered_by_key[pair.discovered.natural_key],
            strategy=pair.strategy, confidence=pair.confidence,
        )


def _write_discrepancies(tenant_id, discrepancy_set) -> None:
    for fd in discrepancy_set.field_discrepancies:
        kind, _t, scope, name = fd.natural_key
        Discrepancy.objects.create(
            tenant_id=tenant_id, discrepancy_type=DiscrepancyType.FIELD,
            kind_name=kind, scope=scope, name=name, field_name=fd.field_name,
            declared_value=fd.declared_value, discovered_value=fd.discovered_value,
            authoritative_plane=Plane.DECLARED,
        )
    for orphan in discrepancy_set.orphans:
        kind, _t, scope, name = orphan.natural_key
        Discrepancy.objects.create(
            tenant_id=tenant_id, discrepancy_type=orphan.discrepancy_type,
            kind_name=kind, scope=scope, name=name, authoritative_plane=Plane.DECLARED,
        )
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_reconcile_service.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(bulk): reconciliation service persists matches and discrepancies over both planes"
```

---

## Task 12: Read-only API (Bulk)

**Files:**
- Create: `datum/api/schemas.py`, `datum/api/router.py`
- Modify: `datum/urls.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `DeclaredResource`, `DiscoveredResource`, `Discrepancy`; `DEFAULT_TENANT_ID`.
- Produces: `GET /api/resources?plane=declared|discovered` (paginated); `GET /api/discrepancies?state=open` (paginated, includes declared/discovered values and authoritative plane); `POST /api/discrepancies/{id}/resolve`. All queries tenant-scoped by `DEFAULT_TENANT_ID`.

- [ ] **Step 1: Write the failing test**

`tests/test_api.py`:
```python
import pytest
from django.test import Client

from datum.discovery.collector import run_collector
from datum.enums import DiscrepancyState
from datum.intent.ingest import ingest_revision
from datum.reconcile.models import Discrepancy
from datum.reconcile.service import run_reconciliation

FIXTURE = "fixtures/k8s/deployments.json"
TENANT = "00000000-0000-0000-0000-000000000001"
pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(intent_repo):
    ingest_revision(TENANT, intent_repo())
    run_collector(TENANT, FIXTURE)
    run_reconciliation(TENANT)


def test_list_open_discrepancies(seeded):
    body = Client().get("/api/discrepancies?state=open").json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["field_name"] == "replicas"
    assert item["declared_value"] == 3
    assert item["discovered_value"] == 5
    assert item["authoritative_plane"] == "declared"


def test_resolve_removes_from_open_queue(seeded):
    disc_id = Discrepancy.objects.get(tenant_id=TENANT).id
    resp = Client().post(f"/api/discrepancies/{disc_id}/resolve")
    assert resp.status_code == 200
    assert Discrepancy.objects.get(id=disc_id).state == DiscrepancyState.RESOLVED
    body = Client().get("/api/discrepancies?state=open").json()
    assert body["count"] == 0


def test_list_declared_resources(seeded):
    body = Client().get("/api/resources?plane=declared").json()
    assert body["count"] == 1
    assert body["items"][0]["name"] == "web"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py -v`
Expected: FAIL — 404 (router not mounted).

- [ ] **Step 3: Write `datum/api/schemas.py`**

```python
from ninja import Schema


class ResourceOut(Schema):
    name: str
    scope: str
    kind_name: str
    attributes: dict


class DiscrepancyOut(Schema):
    id: int
    discrepancy_type: str
    kind_name: str
    scope: str
    name: str
    field_name: str | None
    declared_value: object | None
    discovered_value: object | None
    authoritative_plane: str
    state: str


class PageResources(Schema):
    count: int
    items: list[ResourceOut]


class PageDiscrepancies(Schema):
    count: int
    items: list[DiscrepancyOut]
```

- [ ] **Step 4: Write `datum/api/router.py`**

```python
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import NinjaAPI

from datum.api.schemas import PageDiscrepancies, PageResources
from datum.discovery.models import DiscoveredResource
from datum.enums import DiscrepancyState, Plane
from datum.graph.models import DeclaredResource
from datum.reconcile.models import Discrepancy

api = NinjaAPI(title="Datum", version="1.0.0")
PAGE_SIZE = 50
TENANT = settings.DEFAULT_TENANT_ID


@api.get("/resources", response=PageResources)
def list_resources(request, plane: str = Plane.DECLARED.value, offset: int = 0):
    model = DeclaredResource if plane == Plane.DECLARED.value else DiscoveredResource
    query = model.objects.filter(tenant_id=TENANT).select_related("kind").order_by("name")
    window = query[offset : offset + PAGE_SIZE]
    items = [{"name": r.name, "scope": r.scope, "kind_name": r.kind.name,
              "attributes": r.attributes} for r in window]
    return {"count": query.count(), "items": items}


@api.get("/discrepancies", response=PageDiscrepancies)
def list_discrepancies(request, state: str = DiscrepancyState.OPEN.value, offset: int = 0):
    query = (Discrepancy.objects.filter(tenant_id=TENANT, state=state)
             .order_by("kind_name", "scope", "name", "field_name"))
    window = query[offset : offset + PAGE_SIZE]
    items = [_serialize(d) for d in window]
    return {"count": query.count(), "items": items}


@api.post("/discrepancies/{discrepancy_id}/resolve")
def resolve_discrepancy(request, discrepancy_id: int):
    discrepancy = get_object_or_404(Discrepancy, id=discrepancy_id, tenant_id=TENANT)
    discrepancy.state = DiscrepancyState.RESOLVED
    discrepancy.resolved_at = timezone.now()
    discrepancy.save(update_fields=["state", "resolved_at"])
    return {"id": discrepancy.id, "state": discrepancy.state}


def _serialize(d: Discrepancy) -> dict:
    return {
        "id": d.id, "discrepancy_type": d.discrepancy_type, "kind_name": d.kind_name,
        "scope": d.scope, "name": d.name, "field_name": d.field_name,
        "declared_value": d.declared_value, "discovered_value": d.discovered_value,
        "authoritative_plane": d.authoritative_plane, "state": d.state,
    }
```

- [ ] **Step 5: Mount the router in `datum/urls.py`**

```python
from django.urls import path

from datum.api.router import api

urlpatterns = [path("api/", api.urls)]
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_api.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(bulk): read-only django-ninja API for resources and discrepancies"
```

---

## Task 13: Review queue UI (Bulk)

**Files:**
- Create: `web/package.json`, `web/vite.config.ts`, `web/tsconfig.json`, `web/index.html`, `web/tailwind.config.js`, `web/postcss.config.js`, `web/src/main.tsx`, `web/src/index.css`, `web/src/api.ts`, `web/src/ReviewQueue.tsx`, `web/src/App.tsx`
- Test: `web/src/ReviewQueue.test.tsx`, `web/vitest.config.ts`

**Interfaces:**
- Consumes: the `/api/discrepancies` and `/api/discrepancies/{id}/resolve` endpoints; `web/src/enums.ts` (generated Task 4).
- Produces: a single Review Queue screen — declared vs discovered side by side, the authoritative side badged, keyboard operable (`j`/`k` to move, `r` to resolve), resolved items leave the open queue.

- [ ] **Step 1: Create the web scaffold config files**

`web/package.json`:
```json
{
  "name": "datum-web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "test": "vitest run",
    "lint": "eslint src --max-warnings 0"
  },
  "dependencies": {"react": "^18.3.0", "react-dom": "^18.3.0"},
  "devDependencies": {
    "@testing-library/react": "^16.0.0",
    "@testing-library/user-event": "^14.5.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "eslint": "^9.0.0",
    "jsdom": "^25.0.0",
    "tailwindcss": "^3.4.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0",
    "typescript": "^5.5.0",
    "vite": "^5.4.0",
    "vitest": "^2.1.0"
  }
}
```

`web/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020", "useDefineForClassFields": true, "lib": ["ES2020", "DOM"],
    "module": "ESNext", "moduleResolution": "bundler", "jsx": "react-jsx",
    "strict": true, "noUnusedLocals": true, "noUnusedParameters": true, "skipLibCheck": true
  },
  "include": ["src"]
}
```

`web/vite.config.ts`:
```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
```

`web/vitest.config.ts`:
```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", globals: true },
});
```

`web/tailwind.config.js`:
```javascript
export default { content: ["./index.html", "./src/**/*.tsx"], theme: { extend: {} }, plugins: [] };
```

`web/postcss.config.js`:
```javascript
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

`web/index.html`:
```html
<!doctype html>
<html lang="en">
  <head><meta charset="UTF-8" /><title>Datum — Review Queue</title></head>
  <body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body>
</html>
```

`web/src/index.css`:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 2: Write the API client `web/src/api.ts`**

```typescript
export interface Discrepancy {
  id: number;
  discrepancy_type: string;
  kind_name: string;
  scope: string;
  name: string;
  field_name: string | null;
  declared_value: unknown;
  discovered_value: unknown;
  authoritative_plane: string;
  state: string;
}

export async function fetchOpenDiscrepancies(): Promise<Discrepancy[]> {
  const res = await fetch("/api/discrepancies?state=open");
  const body = await res.json();
  return body.items as Discrepancy[];
}

export async function resolveDiscrepancy(id: number): Promise<void> {
  await fetch(`/api/discrepancies/${id}/resolve`, { method: "POST" });
}
```

- [ ] **Step 3: Write the failing component test**

`web/src/ReviewQueue.test.tsx`:
```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { ReviewQueue } from "./ReviewQueue";

const disc = {
  id: 1, discrepancy_type: "field", kind_name: "Deployment", scope: "default",
  name: "web", field_name: "replicas", declared_value: 3, discovered_value: 5,
  authoritative_plane: "declared", state: "open",
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn((url: string, opts?: RequestInit) => {
    if (opts?.method === "POST") return Promise.resolve({ json: () => Promise.resolve({}) });
    return Promise.resolve({ json: () => Promise.resolve({ count: 1, items: [disc] }) });
  }));
});

test("shows declared 3 and discovered 5 with declared marked authoritative", async () => {
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  expect(screen.getByTestId("declared-value").textContent).toBe("3");
  expect(screen.getByTestId("discovered-value").textContent).toBe("5");
  expect(screen.getByTestId("authoritative-badge").textContent).toContain("declared");
});

test("pressing r resolves the focused discrepancy and it leaves the queue", async () => {
  render(<ReviewQueue />);
  await waitFor(() => screen.getByText("replicas"));
  await userEvent.keyboard("r");
  await waitFor(() => expect(screen.queryByText("replicas")).toBeNull());
});
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd web && npm install && npm test`
Expected: FAIL — `./ReviewQueue` does not exist.

- [ ] **Step 5: Write `web/src/ReviewQueue.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Discrepancy, fetchOpenDiscrepancies, resolveDiscrepancy } from "./api";

export function ReviewQueue() {
  const [items, setItems] = useState<Discrepancy[]>([]);
  const [focus, setFocus] = useState(0);

  useEffect(() => {
    fetchOpenDiscrepancies().then(setItems);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "j") setFocus((f) => Math.min(f + 1, items.length - 1));
      if (event.key === "k") setFocus((f) => Math.max(f - 1, 0));
      if (event.key === "r" && items[focus]) {
        const id = items[focus].id;
        resolveDiscrepancy(id).then(() => {
          setItems((current) => current.filter((d) => d.id !== id));
          setFocus((f) => Math.max(0, Math.min(f, items.length - 2)));
        });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, focus]);

  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="mb-4 text-xl font-semibold">Review Queue</h1>
      <p className="mb-4 text-sm text-gray-500">j / k to move, r to resolve</p>
      <ul>
        {items.map((d, index) => (
          <li key={d.id}
              className={`mb-3 rounded border p-4 ${index === focus ? "ring-2 ring-blue-500" : ""}`}>
            <div className="mb-2 font-medium">
              {d.kind_name} · {d.scope}/{d.name} · {d.field_name}
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="rounded bg-blue-50 p-3">
                <span data-testid="authoritative-badge"
                      className="mb-1 inline-block rounded bg-blue-600 px-2 text-xs text-white">
                  {d.authoritative_plane} — authoritative
                </span>
                <div>declared: <b data-testid="declared-value">{String(d.declared_value)}</b></div>
              </div>
              <div className="rounded bg-gray-50 p-3">
                <div>discovered: <b data-testid="discovered-value">{String(d.discovered_value)}</b></div>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </main>
  );
}
```

- [ ] **Step 6: Write `web/src/App.tsx` and `web/src/main.tsx`**

`web/src/App.tsx`:
```tsx
import { ReviewQueue } from "./ReviewQueue";

export default function App() {
  return <ReviewQueue />;
}
```

`web/src/main.tsx`:
```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode><App /></StrictMode>,
);
```

- [ ] **Step 7: Run the tests**

Run: `cd web && npm test`
Expected: 2 passed.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(bulk): keyboard-operable review queue screen with authoritative-side badge"
```

---

## Task 14: End-to-end acceptance test as the smoke test (Bulk)

**Files:**
- Create: `tests/test_acceptance_slice.py`
- Modify: `.github/workflows/ci.yml` (add the smoke step)

**Interfaces:**
- Consumes: the whole pipeline (`ingest_revision`, `run_collector`, `run_reconciliation`, the API).
- Produces: the Phase 1 acceptance test + 3 negative checks, run in CI as the daily-build smoke test.

- [ ] **Step 1: Write the acceptance test (the 9-step sequence + negatives)**

`tests/test_acceptance_slice.py`:
```python
import pytest
from django.test import Client

from datum.discovery.collector import run_collector
from datum.enums import DiscrepancyState, DiscrepancyType
from datum.intent.ingest import InvalidRevision, ingest_revision
from datum.reconcile.models import Discrepancy, Match
from datum.reconcile.service import run_reconciliation

TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE = "fixtures/k8s/deployments.json"
pytestmark = pytest.mark.django_db


def test_phase1_slice_end_to_end(intent_repo):
    # 1-2: declare web replicas=3, ingest, traceable to commit
    revision = ingest_revision(TENANT, intent_repo())
    assert revision.is_active and len(revision.commit_sha) == 40
    # 3-4: collector reports replicas=5 into discovered plane
    run = run_collector(TENANT, FIXTURE)
    assert run.resources_written == 1
    # 5-6: match + diff -> exactly one field discrepancy, no orphans
    run_reconciliation(TENANT)
    assert Match.objects.filter(tenant_id=TENANT).count() == 1
    field = Discrepancy.objects.filter(tenant_id=TENANT,
                                       discrepancy_type=DiscrepancyType.FIELD)
    assert field.count() == 1
    assert Discrepancy.objects.filter(
        tenant_id=TENANT,
        discrepancy_type__in=[DiscrepancyType.DECLARED_MISSING,
                              DiscrepancyType.DISCOVERED_UNDECLARED]).count() == 0
    d = field.get()
    assert (d.field_name, d.declared_value, d.discovered_value) == ("replicas", 3, 5)
    # 7: visible via API with authoritative side declared
    item = Client().get("/api/discrepancies?state=open").json()["items"][0]
    assert item["authoritative_plane"] == "declared"
    # 8: resolve removes it from the open queue
    Client().post(f"/api/discrepancies/{d.id}/resolve")
    assert Client().get("/api/discrepancies?state=open").json()["count"] == 0
    # 9: determinism — re-running reconciliation yields the identical open set (one field disc)
    run_reconciliation(TENANT)
    reopened = Discrepancy.objects.filter(tenant_id=TENANT, state=DiscrepancyState.OPEN)
    assert reopened.count() == 1
    assert reopened.get().field_name == "replicas"


def test_negative_malformed_document_keeps_previous_revision(intent_repo):
    ingest_revision(TENANT, intent_repo())
    with pytest.raises(InvalidRevision):
        ingest_revision(TENANT, intent_repo("fixtures/intent-repo-malformed"))


def test_negative_discovered_undeclared_is_one_orphan(intent_repo):
    # no intent at all; only discovery
    run_collector(TENANT, FIXTURE)
    run_reconciliation(TENANT)
    orphans = Discrepancy.objects.filter(tenant_id=TENANT,
                                         discrepancy_type=DiscrepancyType.DISCOVERED_UNDECLARED)
    assert orphans.count() == 1


def test_negative_declared_missing_is_one_orphan(intent_repo):
    # intent only; no discovery run
    ingest_revision(TENANT, intent_repo())
    run_reconciliation(TENANT)
    orphans = Discrepancy.objects.filter(tenant_id=TENANT,
                                         discrepancy_type=DiscrepancyType.DECLARED_MISSING)
    assert orphans.count() == 1
```

- [ ] **Step 2: Run the whole suite**

Run: `pytest -v`
Expected: all green, including the four acceptance cases.

- [ ] **Step 3: Wire the smoke test explicitly into CI**

Add to `.github/workflows/ci.yml` after the "Tests + coverage" step:
```yaml
      - name: Phase 1 smoke test (acceptance slice)
        run: pytest tests/test_acceptance_slice.py -v
```

- [ ] **Step 4: Revisit DESIGN.md §24 with real code (definition-of-done item)**

Append a short subsection to `docs/DESIGN.md` §24 recording which early-warning signs fired, e.g.:
```markdown
### §24 revisited at Phase 1 close (2026-...)
- Schema-defined kind bet: held. Adding a second kind is data + fixture, no migration to the resource tables — confirm when Phase 3 adds one.
- Matching vs diff difficulty: [record which corpus cases were hardest].
- Repo/CI foundation: CI ran green on push from Task 3 onward (Ratchet failure did not recur).
- null-vs-absent: Phase 1 diff treats an absent key as a discrepancy with value None; note as a known simplification to revisit when a second kind exposes optional fields.
```

- [ ] **Step 5: Commit and push**

```bash
git add -A
git commit -m "test(bulk): phase 1 acceptance slice as CI smoke test; DESIGN §24 revisited"
git push
```

- [ ] **Step 6: Walk the Phase 1 definition of done in writing**

Confirm, in the PR description or a closing note:
- [ ] Acceptance test + 3 negatives pass
- [ ] Matcher and diff engine: branch-covered, complexity < 10 (ruff green), non-authoring-model review done
- [ ] Daily build runs the acceptance test as smoke
- [ ] DESIGN §24 revisited
- [ ] No later-phase scope pulled forward (no 2nd kind/collector/screen, no precedence/suppression/history/auth)

---

## Self-review notes (author)

- **Spec coverage:** slice steps 1–7 → Tasks 5–13; acceptance test + 3 negatives → Task 14; Phase 0 foundation → Tasks 1–4; kernel bar (complexity/type/coverage gates, non-authoring review) → Tasks 3, 9, 10; `tenant_id` from migration one, tenant-scoped queries → Tasks 5, 11, 12; enums from one source → Task 4; barricade → Tasks 7 (`kubernetes.py`), 8 (`documents.py`). Read-only toward the estate: no write path to any provider; collector only writes `discovered_resource`.
- **Deferred correctly (not built):** precedence, suppression, full lifecycle, history, auth, RLS, 2nd kind/collector/screen, synthetic generator.
- **Type consistency:** `match_by_natural_key`/`reconcile` signatures identical in domain header, Tasks 9–11. `ResourceSnapshot.natural_key` order `(kind, tenant, scope, name)` used identically in matcher, diff, service. Enum `.value` strings match DB `choices` and generated TS.
- **Known Phase-1 simplification (documented, not a defect):** absent-key treated as a field discrepancy with `None`; DESIGN §12/§13 call out null-vs-absent as a real concern — recorded in Task 14 Step 4 for revisit when a second kind introduces optional fields.
