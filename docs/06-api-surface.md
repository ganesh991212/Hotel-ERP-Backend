# 06. API surface (v1, grouped)

Base `/api/v1`. JSON. Errors: `{code, message, details}`. List endpoints are cursor-paginated. Mutating endpoints accept `Idempotency-Key`.

## Auth
`POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /me`, `GET /me/entitlements`

## Platform (super admin)
`/platform/plans` CRUD, `/platform/hotels` CRUD + `/suspend` `/resume`, `/platform/hotels/{id}/subscription`, `/platform/hotels/{id}/overrides`, `/platform/audit`

## Hotel admin
`/outlets`, `/users`, `/roles`, `/areas`, `/tables` (+ `/tables/{id}/qr`, `/tables/qr-sheet`), `/stations`, `/settings`

## Catalog
`/menus`, `/categories`, `/items` (+ `/items/{id}/availability`), `/modifier-groups`

## Diner (token = table QR, then table-session token)
`GET /d/{qr_token}` (outlet + menu, cacheable), `POST /d/{qr_token}/session`, `POST /d/orders`, `GET /d/orders/{id}`, `POST /d/orders/{id}/rating`, `POST /d/call-waiter`, `POST /d/bill-request`

## Staff ordering
`POST /orders`, `GET /orders?status=&table=`, `POST /orders/{id}/items`, `POST /orders/{id}/confirm`, `POST /order-items/{id}/cancel` (permissioned, reason required)

## Kitchen
`GET /kitchen/queue`, `POST /orders/{id}/accept` (atomic claim in CHEF_ACCEPT mode), `/start`, `/ready`, `/hold`, `/serve`, `POST /kitchen/chefs/{id}/queue/reorder`, `POST /kitchen/tickets/{id}/print`, `GET/PUT /kitchen/chefs/{id}` (shift, on-shift flag), `POST /kitchen/assign` (manual override, audited)

## Billing
`POST /bills`, `GET /bills/{id}`, `POST /bills/{id}/split`, `/merge`, `/discount`, `POST /bills/{id}/payments`, `POST /webhooks/razorpay`, `POST /day-close`

## Reports
`/reports/sales`, `/reports/items`, `/reports/chefs`, `/reports/tables`

## Implemented additions (2026-11-18)
- `POST /orders/quote`, `POST /d/{qr}/quote`: server-priced totals for carts (clients never compute money).
- `GET /kitchen/board`: one call for chef screens (new, per-chef queues, ready). `POST /orders/{id}/reassign` (chef or back to board, audited). `PUT /kitchen/chefs/{id}/shift`.
- `GET /d/orders/mine` with `X-Diner-Token`.
- `GET /permissions`, `PUT /roles/{id}/permissions`, `POST /roles` (custom, plan-gated).
- `GET/POST /expense-categories`, `GET /expenses?month=YYYY-MM` (with summary), `POST/PATCH/DELETE /expenses`.
- `GET /dashboard/summary`, `GET /platform/summary`, `PATCH /platform/plans/{id}`.
- `GET /billing/subscription`, `POST /billing/subscription/checkout`, `POST /webhooks/razorpay`.
- `GET/POST /orders/{id}/kot` (preview / print, optional `send_to_kitchen`, `paper_mm` for ESC/POS), `POST /orders/{id}/mark-ready` (paper path).
- `POST /bills`, `GET /bills`, `GET /bills/{id}`, `POST /bills/{id}/print|settle|void`.
- `POST/DELETE /me/devices` (push tokens); `GET /push/config` (public Firebase client config from .env).
- `POST /tables/{id}/qr-print`, `POST /tables/qr-print` (QR tickets as blocks, optional ESC/POS).
- `PATCH/DELETE /expense-categories/{id}` (own only); `GET/POST/PATCH /platform/expense-categories` (global).
- Dashboard summary adds `timings` (avg + p90 per stage) and `servers`.
- Full contract: `docs/api/openapi.json` (CI fails if it drifts).

## Realtime
`WS /ws/outlets/{id}?token=` channels: `kitchen`, `floor`, `admin`; diner: `WS /ws/d/orders/{id}`.
Implemented: one outlet stream (`/ws/outlets/{id}?token=`) carrying all staff events, and `/ws/d?token=<diner session>` per diner session.

## Rooms (phase 8)
`/room-types`, `/rooms`, `/bookings`, `/bookings/{id}/check-in`, `/check-out`, `/folios/{id}`, `/housekeeping/tasks`
