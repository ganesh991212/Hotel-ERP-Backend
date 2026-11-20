# 06. API surface (v1, grouped)

Base `/api/v1`. JSON. Errors: `{code, message, details}`. List endpoints are cursor-paginated. Mutating endpoints accept `Idempotency-Key`.

## Auth
`POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /me`, `GET /me/entitlements`

## Platform (super admin)
`/platform/plans` CRUD, `/platform/hotels` CRUD + `/suspend` `/resume`, `/platform/hotels/{id}/subscription`, `/platform/hotels/{id}/overrides`, `/platform/audit`

## Hotel admin
`/outlets`, `/users`, `/roles`, `/areas`, `/tables` (+ `/tables/{id}/qr`, `/tables/qr-sheet`), `/stations`, `/settings`

## Catalog
`/menus`, `/categories`, `/items` (+ `/items/{id}/availability`)

Variants and add-ons (built 2026-11-20; docs/03 "Variants and add-ons"):
- `GET /items` and the diner menu `GET /d/{qr}` add per item `variants[]` `{id, name, price_minor, is_available}` and
  `addon_groups[]` `{id, name, min_select, max_select, addons[]}`. Diners never see sold-out sizes/add-ons; an item
  whose sizes are all sold out is shown sold out.
- `PUT /items/{id}/variants {variants: [{id?, name, price_minor, is_available}]}` (none, or 2 to 10; `menu.manage`).
- `POST /variants/{id}/availability`, `POST /addons/{id}/availability` (`menu.availability`).
- `GET /addon-groups` (with `used_by` item names), `POST /addon-groups`, `PUT /addon-groups/{id}`
  `{name, min_select, max_select, addons: [{id?, name, price_minor, is_available}]}`, `DELETE /addon-groups/{id}`.
- `PUT /items/{id}/addon-groups {group_ids}` (order = display order).
- Order and quote lines accept `variant_id` and `addons: [{addon_id, qty}]` (ids only; priced on the server).
  Errors: `variant_required`, `bad_variant`, `bad_addon`, `addon_count` (`details.group/min/max`), `item_unavailable`.
  Order items return `variant_name` and `addons[]`.

Happy hour (built 2026-11-20; docs/03 "Happy hour"):
- `GET /price-rules`; `POST`/`PUT /price-rules/{id}` `{name, percent_bp 1..9000, days 1..127 (Mon=1 ... Sun=64),
  start_time "HH:MM", end_time, category_ids | null, is_active}` (`menu.manage`); `DELETE /price-rules/{id}`.
- `GET /items` and the diner menu add `offer`: `{name, percent_bp, until}` while a rule is live for the dish (a badge;
  the price is the quote's). Order items return `price_rule` and `list_unit_price_minor`.

Combos (built 2026-11-20; docs/03 "Combos"):
- `PUT /items/{id}/components {components: [{item_id, qty}]}` (`menu.manage`; empty = ordinary item). Errors
  `bad_combo`. `GET /items` and the diner menu return `components[]` `{item_id, name, qty, is_available}`; order items
  return `components[]`. `DELETE /items/{id}` refuses a dish used in a combo (`item_in_combo`, `details.combos`).

Serving hours (built 2026-11-20; docs/03 "Serving hours"):
- `POST`/`PUT /categories` accept `days` (1..127), `available_from`, `available_to` ("HH:MM", both or neither);
  categories return them with `open_now`. The diner menu adds `open_now` and `hours` per section.
- `POST /orders` accepts `allow_outside_hours` (staff); otherwise dishes outside their hours give `outside_hours`.
  Diner quote and order always refuse them.

## Diner (token = table QR, then table-session token)
`GET /d/{qr_token}` (outlet + menu, cacheable), `POST /d/{qr_token}/session`, `POST /d/orders`, `GET /d/orders/{id}`, `POST /d/orders/{id}/rating`, `POST /d/call-waiter`, `POST /d/bill-request`

## Staff ordering
`POST /orders`, `GET /orders?status=&table=`, `POST /orders/{id}/items`, `POST /orders/{id}/confirm`, `POST /order-items/{id}/cancel` (permissioned, reason required)

## Kitchen
`GET /kitchen/queue`, `POST /orders/{id}/accept` (atomic claim in CHEF_ACCEPT mode), `/start`, `/ready`, `/hold`, `/serve`, `POST /kitchen/chefs/{id}/queue/reorder`, `POST /kitchen/tickets/{id}/print`, `GET/PUT /kitchen/chefs/{id}` (shift, on-shift flag), `POST /kitchen/assign` (manual override, audited)

## Billing
`POST /bills`, `GET /bills/{id}`, `POST /bills/{id}/split`, `/merge`, `/discount`, `POST /bills/{id}/payments`, `POST /webhooks/razorpay`, `POST /day-close`

## Reports
Built 2026-11-20 (`report.view`; `?from=&to=` hotel-local dates, default today, at most 92 days; basis in every
response; design in docs/14 "Reports"):
- `GET /reports/sales`: per day and in total: bills, gross, discount, taxable, CGST, SGST, other tax, service charge,
  round off, total; paid / unpaid; cancelled (by hand) and replaced bills kept apart; money received by CASH/UPI/CARD
  by the day it was taken; `unbilled` (orders on no live bill: a warning, never sales).
- Rota (`attendance.manage`): `GET /rota?from=&to=` (default 7 days from today, max 31), `POST /rota {user_id, day,
  start "HH:MM", end "HH:MM", note?}` (`shift_overlap`, `bad_times`), `DELETE /rota/{id}`, `POST /rota/copy-week
  {week_start}` → `{copied, skipped}`. `GET /rota/me` (anyone): my shifts for 7 days. Each shift has `state`,
  `late_minutes`, `clocked_in_at`, `working`, `overnight`.
- Attendance: `GET /attendance/me`, `POST /attendance/clock-in` (`already_clocked_in`), `POST /attendance/clock-out`
  (`not_clocked_in`) for anyone signed in to a hotel; `GET /attendance?from=&to=` (`attendance.manage`, hotel-local
  days, default today, "from" alone runs to today, max 62 days): `shifts[]` (minutes, open, missing_clock_out,
  corrected) and `people[]` (days, shifts, minutes, missing_clock_outs); `POST /attendance/{id}/fix {clock_in_at?,
  clock_out_at?, reason}` (`future_time`, `bad_times`).
- `GET /reports/{sales|items|categories|gst|pnl|staff}.print?from=&to=` (`report.view`; staff also needs
  `REPORTS_ADVANCED`): `{title, blocks}` in the bill block format (English, rupees formatted on the server), the same
  figures as the JSON report; the app renders them as an A4 PDF.
- `GET /reports/pnl?from=&to=` (`report.view`): profit and loss by **whole months** (default the last 6, at most 12):
  per month and in total `bills`, `sales_minor` (before GST, after discounts), `service_charge_minor`,
  `income_minor`, `expenses[]` by category, `expenses_minor`, `operating_profit_minor`, `margin_pct`,
  `gst_collected_minor` (not income), `so_far` for the current month. `GET /reports/pnl.csv` (one column per month).
  `/dashboard/summary` adds `income_month_minor`; `net_month_minor` = income before GST - expenses (it used to
  subtract expenses from bill totals that included GST).
- `GET /reports/items`: per item name as billed: category, qty, gross, discount, taxable; plus `categories[]`
  (items, qty, gross, discount, taxable, share %) from the same lines. `GET /reports/categories.csv` exports them.
- `GET /reports/gst`: by GST rate: taxable, CGST, SGST (per bill as printed, then summed); SAC 996331; documents
  issued per type and FY (first/last number, total, cancelled, net) for GSTR-1 table 13.
- `GET /reports/staff` (also plan feature `REPORTS_ADVANCED`): per person: bills, billed value, discounts given,
  money received by method, cancellations made by hand.
- `GET /reports/{sales|items|gst|staff|register}.csv`: the same numbers as CSV in rupees (UTF-8 with BOM for Excel);
  `register` lists every bill in the period, cancelled ones included, with the reason.
- `GET /dashboard/summary` revenue (today, 7 days, month) now uses the same definition (issued, not-cancelled bills);
  adds `bills_today` and `unbilled_today_minor`; `avg_ticket_minor` is per bill.
- Later: `/reports/chefs`, `/reports/tables`, category-wise, PDF.

## Implemented additions (2026-11-18)
- `POST /orders/quote`, `POST /d/{qr}/quote`: server-priced totals for carts (clients never compute money).
- Orders and their lines carry `due_at` (target ready time, fixed when placed). `GET /dashboard/summary` adds
  `on_time {ready, on_time, pct}` for today (`pct` null when nothing was made ready).
- Hold / resume (`order.create`; list `order.view`): `POST /held-orders {items, table_id?, customer_name?, note?}`
  (refused if the cart cannot be priced); `GET /held-orders?table_id=|takeaway=true` (oldest first; each with `items`,
  today's `quote` or a `problem`); `DELETE /held-orders/{id}`. `POST /orders {..., held_order_id}` consumes the hold;
  `409 hold_gone` if it was already sent or discarded.
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
- `POST /bills {table_id | order_id, buyer: {name, gstin, address?}}`: a B2B tax invoice; with an unpaid bill open
  it is re-issued with the buyer. Errors `not_tax_invoice`, `bad_gstin`, `interstate_b2b`. Bills return `buyer`
  (or null) and `order_ids`. `GET /reports/gst` adds `b2b[]` and `b2c_by_rate[]` (also in `gst.csv`).
- `POST /bills {order_id}`: bill one takeaway order (`table_order` for table orders, `already_billed`).
- `GET /orders?q=` also matches the takeaway customer's name or phone.

## Discounts (added 2026-11-20)
- `POST /bills {table_id | order_id, discount: {percent_bp | amount_minor, reason}}`: bill with a discount; with an
  unpaid bill already open it re-issues it. Errors: `discount_limit` (403, `details.max_bp`), `bad_discount`.
- Bill JSON adds `discount_minor`, `discount_reason`, `taxable_minor`, and per line `discount_minor`.
- `PUT /roles/{id}/discount-limit {max_discount_bp}` (`role.manage`); roles report `max_discount_bp`.

## Payments (added 2026-11-20)
- `POST /bills/{id}/payments {method, amount_minor, reference?}`: one part; errors `overpayment` (`details.due_minor`).
- `DELETE /bills/{id}/payments/{payment_id}`: undo a part while unpaid.
- `GET /bills/{id}/split?parts=2..20`: `{due_minor, shares_minor[]}`.
- `POST /bills/{id}/settle {method}` pays whatever is still due.
- Bill JSON adds `payments[]`, `paid_minor`, `due_minor`; `payment_method` may be `MIXED`.

## Split by items (added 2026-11-20)
- `GET /bills/split-preview?table_id=`: the visit's grouped lines `{key, name, qty, unit_price_minor, tax_rate_bp,
  line_total_minor}`, `unserved[]`, `open_bill` (the ordinary bill a split would replace) and its `open_bill_paid_minor`.
- `POST /bills/split {table_id, guests 2..20, lines: [{key, qty: [per guest]} | {key, shared: [guest numbers]}],
  include_unserved}` -> 201 `{bills[], replaced_invoice_nos[]}`. Errors: `bad_split`, `guest_empty`,
  `lines_changed` (open the split again), `orders_not_served`, `has_payments`, `bill_discounted`, `nothing_to_bill`. Audited `bill.split`.
- Bill JSON adds `split`: null, or `{group_id, guest_no, guest_count, bills: [{id, invoice_no, guest_no, status,
  total_minor, due_minor}]}`. Voiding a guest bill voids the whole split (`split_partly_paid` once anyone paid).

## Day close (added 2026-11-20)
- `GET /day-close?date=` (`dayclose.manage`; default today): `cash_sales_minor`, `upi_minor`, `card_minor`,
  `cash_paid_out_minor`, `carried_float_minor` (null before the first close), `opening_float_minor`,
  `expected_minor`, `unpaid_bills {count, total_minor}` (a warning), `closed` (the saved close or null). With
  `counted_minor` (+ `float_kept_minor`, `opening_float_minor`) it adds `check {difference_minor, result,
  deposit_minor}` so the app shows Over / Short live without money arithmetic.
- `POST /day-close {date?, counted_minor, float_kept_minor, opening_float_minor? (first close only), note?}` -> 201
  the close: `difference_minor`, `result` OVER | SHORT | EXACT, `deposit_minor`, `closed_by`,
  `cash_after_close_minor`. Errors `note_required` (any difference), `already_closed`, `future_day`, `bad_float`.
- `GET /day-closes?from=&to=` (default last 30 days): `closes[]`, `net_difference_minor`, `short_days`, `over_days`.

## Table visits (added 2026-11-20)
- `POST /tables/{id}/seat {guests?}` and `POST /tables/{id}/hold {state: FREE|RESERVED|CLEANING, note?}`
  (`order.create`); error `table_in_use`. Tables return `state` (FREE | SEATED | OCCUPIED | BILL_PENDING | CLEANING |
  RESERVED), `seated_at`, `guests`, `hold_note`. Bills return `guests`. Sales report rows add `covers`,
  `avg_per_cover_minor`.
- `POST /tables/{id}/move {to_table_id}` and `POST /tables/{id}/merge {from_table_ids}` (`order.create`); errors
  `table_has_bill`, `table_in_use`, `nothing_to_move`, `same_table`. Return the target table.

## Bookings (added 2026-11-20)
- `GET /bookings?date=` (`order.view`; hotel-local day, default today): `bookings[]` (that day, by time),
  `waitlist[]` (waiting now), `avg_wait_minutes_today`.
- `POST /bookings {kind: BOOKING|WAITLIST, name, phone?, guests, booked_for? (with zone), quoted_minutes?, table_id?,
  note?, allow_overlap?}` (`order.create`) -> 201; errors `table_booked` (details name, booked_for), `past_time`.
- `POST /bookings/{id}/seat {table_id?}`; `POST /bookings/{id}/status {status: CANCELLED|NO_SHOW}`; errors
  `table_in_use`, `table_has_bill`, `table_required`, `bad_booking_state`.
- A booking: `id, kind, status, name, phone, guests, booked_for, quoted_minutes, waited_minutes, table, note,
  no_shows_before`. Tables add `next_booking {at, name, guests}`.

## Kitchen stations (added 2026-11-20)
- `GET /stations`; `POST /stations {name}`, `PUT /stations/{id}`, `DELETE /stations/{id}` (`menu.manage`; error
  `station_exists`, also for "Kitchen"). Categories and items accept `station_id` (item `null` = follow the category).
  `GET /items` returns `station_id` (own) and `station {id, name}` (effective; null = main kitchen).
- Order items return `station`. `GET`/`POST /orders/{id}/kot` add `tickets [{station, blocks}]` when the order spans
  several stations.

## Realtime
`WS /ws/outlets/{id}?token=` channels: `kitchen`, `floor`, `admin`; diner: `WS /ws/d/orders/{id}`.
Implemented: one outlet stream (`/ws/outlets/{id}?token=`) carrying all staff events, and `/ws/d?token=<diner session>` per diner session.

## Rooms (phase 8)
`/room-types`, `/rooms`, `/bookings`, `/bookings/{id}/check-in`, `/check-out`, `/folios/{id}`, `/housekeeping/tasks`
