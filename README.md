# Hotel ERP Backend

Multi-tenant hotel and restaurant ERP. A super admin onboards hotels and controls their subscription; each hotel admin manages outlets, tables, menus and staff; diners order by scanning a table QR or through a waiter; orders route automatically to chefs.

- **This repo:** Python, FastAPI, PostgreSQL, Redis, plus **all design docs** (single source of truth)
- **Clients:** [Hotel-ERP-Frontend](https://github.com/ganesh991212/Hotel-ERP-Frontend), Flutter for mobile, tablet and web
- **Market:** India (INR, UPI, GST). Dining first, rooms in a later phase.

**Status:** Phases 1-4 and 7 partly built (see `docs/BUILD-PLAN.md`). Billing (Phase 5), ratings and rooms not started.

## Run locally

```bash
docker compose up -d                       # Postgres 16 (+ roles from scripts/db-roles.sql) and Redis
pip install -e ".[dev]"
cp .env.example .env                       # set JWT_SECRET
alembic upgrade head
PLATFORM_ADMIN_EMAIL=you@example.com PLATFORM_ADMIN_PASSWORD=change-me python -m app.seed --demo
uvicorn app.main:app --reload             # http://localhost:8000/docs
```

Tests need the roles plus an empty `hotel_erp_test` database owned by `hotel_owner`:

```bash
psql -h localhost -U postgres -c "CREATE DATABASE hotel_erp_test OWNER hotel_owner"
pytest -q
```

Start with `docs/BUILD-PLAN.md`. It is the single tracker; nothing in it is duplicated elsewhere.

| Doc | Topic |
|---|---|
| `docs/BUILD-PLAN.md` | Phases, tasks, definition of done |
| `docs/01-market-analysis.md` | Competitors, gaps, hypotheses to validate |
| `docs/02-system-architecture.md` | Components, stack, tenancy |
| `docs/03-domain-model.md` | Entities, order states |
| `docs/04-kitchen-routing.md` | Chef assignment algorithm and worked examples |
| `docs/05-subscription-entitlements.md` | Plans, feature flags, limits |
| `docs/06-api-surface.md` | Endpoint groups |
| `docs/07-realtime-offline-printing.md` | WebSocket, offline, KOT printing |
| `docs/08-security-tenancy.md` | Auth, RBAC, RLS, QR abuse controls |
| `docs/09-open-questions.md` | Decisions still needed |

## Deploying
Staging and production on Render + Supabase: see `docs/10-deployment.md` (runbook) and `deploy/env.staging.example` (every value).
