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
- `POST /tables/{id}/qr-print`, `POST /tables/qr-print` (branded QR tickets: hotel, logo, powered-by, blocks, optional ESC/POS).
- `GET/PUT /branding`, `GET/PUT/DELETE /branding/logo`.
- `PATCH/DELETE /expense-categories/{id}` (own only); `GET/POST/PATCH /platform/expense-categories` (global).
- Dashboard summary adds `timings` (avg + p90 per stage) and `servers`.
- Full contract: `docs/api/openapi.json` (CI fails if it drifts).

## Order workflow (added 2026-11-19)
- `GET/PUT /settings`: adds `serve_mode` (SERVER|COUNTER) and `pickup_alert_minutes` (1-60 or null).
- `GET /tables`: each table adds `state` (FREE|OCCUPIED|BILL_PENDING), `unbilled_orders`, `unbilled_total_minor`.
- `GET /orders?scope=attention`: open orders plus served orders not yet billed (the Floor's "Needs attention").
- `GET /orders?scope=today|all&status=&table_id=`: the admin Orders screen.
- Events: `order.confirmed` (a QR order confirmed by staff: alerts chefs like a new order); `order.pickup_overdue`
  (no chef picked it up in time: admins and chefs on shift).

## Passwords and "My work" (added 2026-11-19)
- `POST /me/password {current_password, new_password}`: any signed-in user (platform admin too). Signs out every
  other device (refresh tokens revoked) and returns fresh tokens for this one. Audited.
- `POST /users/{id}/password {new_password}` (`staff.manage`): reset for staff who forgot theirs; signs them out.
  Only a hotel admin may reset a hotel admin; your own password goes through `/me/password`. Audited.
- `GET /dashboard/me?period=today|week`: the caller's own work, one section per thing their role does:
  `cooking` (kitchen.cook: orders/items cooked, avg prep, avg wait before start, queue), `serving` (order.serve or
  order.create: served, tables, avg ready-to-table, orders taken), `billing` (bill.manage: bills raised, collected
  by method, unpaid now). Each average comes with the team average; no other person's name or numbers.

## Lists by date, and billing a visit (added 2026-11-20)
- `GET /orders` and `GET /bills` take `from`/`to` (hotel-local `YYYY-MM-DD`, at most 31 days), `q` (orders: order
  number, with or without `#`, or table; bills: invoice number or table), `limit` (max 200) and `offset`. Newest
  first. Bills also take `status` (ISSUED|PAID|VOID). Indexed on `(hotel_id, created_at)` (migration 0005).
  Nothing is deleted: GST records are kept for 72 months; old days are simply not loaded until asked for.
- `POST /bills` returns `replaced_invoice_nos`: the unpaid bills it cancelled because the guest ordered more
  (docs/03 "Billing a table visit").

## Takeaway (added 2026-11-20)
- `POST /orders {takeaway: true, customer_name?, customer_phone?, items, note?}`: no table (exactly one of
  `table_id` or `takeaway`). Channel `TAKEAWAY`; order JSON carries `customer_name`, `customer_phone`.
- `POST /bills {order_id}`: bill one takeaway order (`table_order` for table orders, `already_billed`).
- `GET /orders?q=` also matches the takeaway customer's name or phone.

## Discounts (added 2026-11-20)
- `POST /bills {table_id | order_id, discount: {percent_bp | amount_minor, reason}}`: bill with a discount; with an
  unpaid bill already open it re-issues it. Errors: `discount_limit` (403, `details.max_bp`), `bad_discount`.
- Bill JSON adds `discount_minor`, `discount_reason`, `taxable_minor`, and per line `discount_minor`.
- `PUT /roles/{id}/discount-limit {max_discount_bp}` (`role.manage`); roles report `max_discount_bp`.

## Realtime
`WS /ws/outlets/{id}?token=` channels: `kitchen`, `floor`, `admin`; diner: `WS /ws/d/orders/{id}`.
Implemented: one outlet stream (`/ws/outlets/{id}?token=`) carrying all staff events, and `/ws/d?token=<diner session>` per diner session.

## Rooms (phase 8)
`/room-types`, `/rooms`, `/bookings`, `/bookings/{id}/check-in`, `/check-out`, `/folios/{id}`, `/housekeeping/tasks`
