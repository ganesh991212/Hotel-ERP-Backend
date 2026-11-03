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

## Realtime
`WS /ws/outlets/{id}?token=` channels: `kitchen`, `floor`, `admin`; diner: `WS /ws/d/orders/{id}`.

## Rooms (phase 8)
`/room-types`, `/rooms`, `/bookings`, `/bookings/{id}/check-in`, `/check-out`, `/folios/{id}`, `/housekeeping/tasks`
