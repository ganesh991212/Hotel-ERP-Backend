# Hotel ERP Backend

Multi-tenant hotel and restaurant ERP API. A super admin onboards hotels and controls their subscription; each hotel admin manages outlets, staff and roles; later phases add menus, QR and waiter ordering, kitchen routing and billing.

- **This repo:** Python 3.12+, FastAPI, PostgreSQL 16 (RLS), plus **all design docs** (single source of truth)
- **Clients:** [Hotel-ERP-Frontend](https://github.com/ganesh991212/Hotel-ERP-Frontend), Flutter for mobile, tablet and web

**Status:** Phase 1 (tenancy, auth, RBAC, plans and entitlements, super admin, hotel admin) implemented and tested. See `docs/BUILD-PLAN.md`.

## Run it locally

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
scripts/dev_db.sh hotel_erp            # roles, database, migrations, grants
cp .env.example .env                   # then set DATABASE_URL / OWNER_DATABASE_URL / JWT_SECRET
PLATFORM_ADMIN_PASSWORD='choose-a-long-one' .venv/bin/python scripts/create_platform_admin.py --name "You" --login you@example.com
.venv/bin/uvicorn app.main:app --reload --port 8000     # API docs at http://localhost:8000/docs
```

## Test

```bash
scripts/dev_db.sh hotel_erp_test --reset
.venv/bin/pytest -q          # real Postgres, as the non-superuser app role
.venv/bin/ruff check app tests scripts alembic && .venv/bin/mypy app
```

## Layout

```
app/
  core/       config-free building blocks: errors, security, identifiers (phone, GSTIN), permissions
  db/         engine and hotel-context transactions (the most safety-critical file)
  services/   audit, entitlements (version-keyed cache), auth sessions, RBAC guards, staff
  api/        deps (request context), schemas, routers
alembic/      migrations (run as the owner role, never as the app role)
scripts/      dev_db.sh, create_app_role.sql, create_platform_admin.py
tests/        unit, database safety, isolation, auth, platform, hotel admin
docs/         design docs and BUILD-PLAN (the tracker)
```

## Docs

| Doc | Topic |
|---|---|
| `docs/BUILD-PLAN.md` | Phases, tasks, definition of done |
| `docs/01-market-analysis.md` | Competitors, gaps, hypotheses to validate |
| `docs/02-system-architecture.md` | Components, stack, tenancy, Phase 1 decisions and measurements |
| `docs/03-domain-model.md` | Entities, order states |
| `docs/04-kitchen-routing.md` | Chef queue, assignment algorithm, worked examples |
| `docs/05-subscription-entitlements.md` | Plans, feature flags, limits |
| `docs/06-api-surface.md` | Endpoints (Phase 1 implemented) |
| `docs/07-realtime-offline-printing.md` | WebSocket, offline, KOT printing, devices |
| `docs/08-security-tenancy.md` | Auth, RBAC, RLS, QR abuse controls |
| `docs/09-open-questions.md` | Decisions still needed |
