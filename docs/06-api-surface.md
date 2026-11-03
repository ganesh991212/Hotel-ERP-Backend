# 06. API surface (v1, grouped)

Base `/v1`. JSON. Errors: `{code, message, details}`, where clients branch on `code`. List endpoints are cursor-paginated (`?cursor=&limit=`, response `{items, next_cursor}`). Unknown request fields are rejected (422), not ignored. The live, authoritative contract is `/openapi.json`; this page is the map.

## Implemented in Phase 1

| Area | Endpoints | Who |
|---|---|---|
| Auth | `POST /auth/login` (email or phone; `hotel_id` when the user works at several hotels), `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/invites/{token}`, `POST /auth/invites/accept`, `GET /me` | anyone |
| Platform | `GET /platform/features`; `GET/POST /platform/plans`, `PATCH /platform/plans/{id}`, `PUT /platform/plans/{id}/features`; `GET/POST /platform/hotels`, `GET/PATCH /platform/hotels/{id}`, `POST .../suspend`, `POST .../resume`, `PUT .../subscription`, `GET .../entitlements`, `PUT/DELETE .../overrides/{feature}`; `GET /platform/audit` | super admin |
| Hotel | `GET/PATCH /hotel`, `GET/PUT /hotel/tax-profile`, `GET /hotel/entitlements` | members; writes need `hotel.manage` |
| Outlets | `GET/POST /outlets`, `GET/PATCH /outlets/{id}`, `POST .../archive`, `POST .../restore` | members; writes need `outlet.manage` |
| Roles | `GET /permissions`, `GET/POST /roles`, `PATCH/DELETE /roles/{id}` | members; writes need `role.manage` |
| Staff | `GET/POST /staff`, `GET/PATCH /staff/{id}`, `POST .../disable`, `POST .../enable`, `POST .../invite` | `staff.manage` |
| Audit | `GET /audit-log` | `audit.read` |

Error codes clients must handle: `INVALID_CREDENTIALS`, `ACCOUNT_LOCKED` (429), `HOTEL_SELECTION_REQUIRED` (409, `details` lists hotels), `REFRESH_TOKEN_SUPERSEDED` (retry with the newer token), `REFRESH_TOKEN_REUSED` (session ended, sign in), `PERMISSION_DENIED`, `FEATURE_NOT_IN_PLAN`, `PLAN_LIMIT_REACHED` (`details`: feature, limit, current), `PRIVILEGE_ESCALATION`, `LAST_ADMIN`, `CANNOT_MODIFY_SELF`, `HOTEL_SUSPENDED`, `INVITE_INVALID` (410), `VALIDATION_ERROR`, `NOT_FOUND`.

Invites: there is no SMS or email sending yet. The invite token is returned once, to the admin who created it, to share with the person (WhatsApp, in person). Accepting needs a new password for new people, or the **existing** password for someone who already works at another hotel.

## Planned (later phases)

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
