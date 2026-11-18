# 02. System architecture

## Hierarchy
Super admin → Hotel (tenant, one subscription) → Outlet (restaurant, bar, room service) → Areas → Tables.

## Components
```
Flutter apps (staff, admin, super admin, diner web)
        |  HTTPS REST + WebSocket
FastAPI (one instance today: realtime fan-out is in-process; see docs/10 "Scaling limit")
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

## Module layout (api/)
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
