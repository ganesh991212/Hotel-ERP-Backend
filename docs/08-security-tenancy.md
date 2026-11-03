# 08. Security and tenancy

## Tenant isolation
- `hotel_id` on every tenant table; RLS policy `hotel_id = current_setting('app.hotel_id')::uuid`.
- The application DB role is not a superuser and cannot bypass RLS. `app.hotel_id` is set per transaction (`SET LOCAL`) from the verified token.
- Test required for every new table: user of hotel A gets zero rows and cannot write rows for hotel B.
- Super admin endpoints use a distinct DB role limited to platform tables plus audited, explicit cross-tenant reads.

## Authentication
- Passwords: argon2id. Staff may also log in by phone OTP (config).
- Access JWT 15 min; refresh tokens rotate on every use and belong to a family. Reuse of a used token revokes the whole family. Clients must serialise refresh calls.
- Rate limits on login and OTP; lockout with backoff.

## RBAC
Permissions are strings (`order.create`, `order.cancel`, `bill.discount`, `menu.edit`, `staff.manage`). System roles: HOTEL_ADMIN, MANAGER, CASHIER, WAITER, CHEF. Custom roles combine permissions. Sensitive actions (cancel after preparing, discount over a limit, manual reassign) need permission and a reason, and are audited.

## Diner / QR abuse controls
- QR encodes an opaque `qr_token` for the table, not a table id. Tokens are rotatable (printed QR leaked or defaced).
- Scanning opens a **table session** with a short-lived token (default 3 h, ends at bill payment). The first scan can require phone OTP (config) and the outlet can require waiter confirmation before the kitchen sees an order.
- Per-session and per-IP rate limits; max items per order; max open orders per session.
- Prices are never accepted from the client.
- Optional geofence check (soft signal only, since GPS is unreliable indoors).

## Data protection
Diner phone numbers are personal data (India DPDP Act): collect only when needed, store hashed where possible, document retention. Staff Aadhaar or ID is out of scope. Encrypt at rest and in transit; secrets in a secret manager; audit log is append-only.

## Payments
Razorpay webhooks: verify signature, idempotent on event id. Never store card data. Amounts are verified server-side against the bill before capture.
