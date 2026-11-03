# 02. System architecture

## Hierarchy
Super admin → Hotel (tenant, one subscription) → Outlet (restaurant, bar, room service) → Areas → Tables.

## Components
```
Flutter apps (staff, admin, super admin, diner web)
        |  HTTPS REST + WebSocket
FastAPI (stateless, N instances)
   |-- PostgreSQL (RLS, source of truth)
   |-- Redis (pub/sub for realtime, rate limits, short locks, cache of public menu)
   |-- Worker (arq): notifications, reports, subscription renewals, ratings requests
   |-- Object storage (item images, QR sheets, invoices)
   |-- Razorpay (plan billing, bill payments, webhooks)
   |-- FCM (push)
```

## Stack
Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, PostgreSQL 16, Redis 7, arq, pytest. OpenAPI is the contract; the Flutter client uses a typed API layer derived from it.

## Tenancy
Single database, shared schema. Every tenant table carries `hotel_id`. Postgres RLS policies compare `hotel_id` to `current_setting('app.hotel_id')`, set per request from the verified token. Super admin uses a separate role that bypasses RLS for platform tables only. Chosen over schema-per-tenant for simpler migrations at hundreds of hotels; revisit if a customer needs hard isolation.

## Config layers
plan (what is allowed) → hotel settings → outlet settings (what is chosen). Outlet settings are validated against the plan on write and re-checked on use.

## Money and time
Integer paise (`_minor`). Timestamps in UTC, rendered in the outlet's timezone. Business day (day close) is an outlet setting, since restaurants trade past midnight.

## Module layout (original plan; the Phase 1 layout as built is in README.md)
```
app/
  core/        config, db, security, tenancy, errors
  platform/    hotels, plans, subscriptions, entitlements (super admin)
  iam/         users, roles, permissions, auth
  catalog/     menus, items, modifiers, stations
  floor/       outlets, areas, tables, qr, table_sessions
  ordering/    orders, order_items, state machine
  kitchen/     chefs, shifts, routing (pure), tickets, kds
  billing/     bills, tax, payments
  feedback/    ratings
  realtime/    ws hub, redis fan-out
  rooms/       (phase 8)
```
Routing and pricing are pure modules with no DB or framework imports, so they can be tested exhaustively.

## Deployment (initial)
Docker images; single region India (data residency); managed Postgres and Redis; staging and production; migrations run as a release step.

## Phase 1 implementation decisions

**Request path (hotel staff).** JWT decode (no I/O), then one transaction: `set_config` for the hotel context (one round trip), one indexed access query that re-validates session, user, membership, role and hotel and returns permissions and the entitlements version (0.21 ms measured with 20,000 members), then the handler. Entitlements come from a process-local cache keyed by `(hotel_id, entitlements_version)`; database triggers bump the version on any plan, override, subscription or catalogue change, so the cache is never stale and needs no cross-process invalidation.

**Platform vs hotel tables.** Hotel data is RLS-forced. Platform tables (hotel, app_user, plan, feature_catalog, plan_feature, subscription, hotel_entitlement_override, auth_session, refresh_token, invite_token, platform_audit_log) are unscoped by decision: they are global catalogues, the tenancy root, or token-keyed auth state needed before the hotel is known. None holds guest or order data. `tests/test_db_safety.py` enforces the split from the live catalogue.

**Sessions.** One `auth_session` per device; refresh tokens form a chain (`refresh_token`). Rotation is one atomic `UPDATE ... WHERE used_at IS NULL RETURNING`. A rotated token presented within 30 s is a client race (`REFRESH_TOKEN_SUPERSEDED`, session kept); later it is a replay (`REFRESH_TOKEN_REUSED`, session revoked, audited).

**Transactions commit before the response.** DB dependencies use `Depends(..., scope="function")`; FastAPI's default scope commits after the response is sent (verified on 0.162).

**Concurrency.** Plan limits and the last-admin rule take transaction-scoped advisory locks keyed per hotel, so concurrent requests cannot overshoot a limit or remove the last admin; neither lock touches the hotel row, so ordinary inserts are never blocked.

## Performance (measured, Phase 1)

Environment: one 4-vCPU container running Postgres, the API and the load generator together, so absolute numbers are a floor.

| Measure | Result |
|---|---|
| Access-check query (20k members, 20k sessions) | 0.21 ms execution, index scans, 13 buffer hits |
| `/health` (no DB), 2 workers | ~13,800 req/s |
| Authenticated DB endpoint, 1 worker, 16 concurrent | ~640 req/s (was ~510 before the two optimisations below) |
| Same, 2 workers (box saturated by client + DB) | ~690-840 req/s, p50 53-67 ms at 48 concurrent |

Optimisations applied after profiling with py-spy (the worker was CPU-bound in Python, Postgres nearly idle): `pool_pre_ping` off (one fewer round trip per request, +12%), Core `AsyncConnection` instead of the ORM session (+12%). Capacity rule of thumb: ~1.5-2 ms of API CPU per authenticated request, so one vCPU serves ~500-650 req/s; scale by adding workers or instances. Next lever if a hot path needs it (orders, kitchen display): raw asyncpg for that path, measured first.
