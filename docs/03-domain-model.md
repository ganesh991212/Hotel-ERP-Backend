# 03. Domain model

All tenant tables have `hotel_id` (RLS). Ids are UUIDs. Amounts are `_minor` integers.

## Platform
- `plans(id, code, name, price_minor, interval, is_active)`
- `plan_features(plan_id, feature_key, bool_value, int_limit)`
- `hotels(id, name, legal_name, status[ACTIVE|SUSPENDED|TRIAL], timezone)`
- `hotel_tax_profile(hotel_id, gst_registration[REGULAR|COMPOSITION|UNREGISTERED], gstin null, state_code, service_charge_bp, service_charge_applies, invoice_prefix, fy_start_month)` captured at onboarding; GSTIN format and checksum validated. Billing document type follows `gst_registration`: REGULAR = tax invoice (CGST+SGST or IGST by state), COMPOSITION = bill of supply with no tax collected, UNREGISTERED = plain bill.
- `invoices(id, hotel_id, number, fy, bill_id, issued_at, status)` numbers sequential per GSTIN per financial year, immutable once issued; cancellation = credit note.
- `subscriptions(hotel_id, plan_id, status, current_period_end, grace_until, razorpay_sub_id)`
- `hotel_entitlement_overrides(hotel_id, feature_key, bool_value, int_limit, reason, set_by)`
- `feature_catalog(key, scope[HOTEL|OUTLET|ROLE], default_value, plan_gated, description)` every capability is a row; new features ship disabled and are enabled per hotel by super admin.
- `webhook_endpoints(id, hotel_id, url, events, secret_hash, status)`, `api_keys(id, hotel_id, name, key_hash, scopes, last_used_at)`. Deliveries are signed and retried.
- `notification_templates(event, channel, locale, body)`, `notifications(id, user_id, event, payload, read_at)`.
- `devices(id, hotel_id, outlet_id, kind[PHONE|TABLET|KDS_DISPLAY|PRINT_STATION], name, token_hash, status, paired_at)` and `device_pairings(code, outlet_id, expires_at, approved_by)`: a TV or tablet shows a 6-digit code, an admin approves it, the device gets a revocable token scoped to outlet and role.
- `printers(id, outlet_id, name, connection[NETWORK|USB|BLUETOOTH], address, paper_mm, print_station_device_id null)`, `print_jobs(id, printer_id, kind[KOT|BILL], payload, status, attempts)`.
- `staff_attendance(id, user_id, outlet_id, clock_in, clock_out)`, `announcements(id, hotel_id, audience, body, created_by)`.

## Identity
- `users(id, hotel_id null for super admin, name, phone, email, password_hash, status)`
- `roles(id, hotel_id, name, is_system)`, `role_permissions(role_id, permission)`
- `user_outlet_roles(user_id, outlet_id, role_id)`
- `refresh_tokens(id, user_id, family_id, hash, used_at, revoked_at, expires_at)`

## Floor
- `outlets(id, hotel_id, name, kitchen_mode[AUTO_ASSIGN|CHEF_ACCEPT|ADMIN_PRINT], require_accept, max_queue_orders, rating_band_minutes, qr_confirm_required, serve_mode[SERVER|COUNTER], pickup_alert_minutes null, ratings_enabled, business_day_cutoff)` (workflow: docs/04 "Order workflow")
- `areas(id, outlet_id, name)`, `tables(id, area_id, label, seats, qr_token, status)`
- `table_sessions(id, table_id, opened_at, closed_at, diner_phone_verified, token_hash)`

## Catalog
- `menus(id, outlet_id, name, schedule)`, `categories(id, menu_id, name, sort)`
- `items(id, category_id, name, description, veg_flag, base_price_minor, tax_code, est_prep_minutes, station_id, is_available, image_key)`
- `variants(id, item_id, name, price_minor)`
- `modifier_groups(id, item_id, name, min, max)`, `modifiers(id, group_id, name, price_minor)`
- `stations(id, outlet_id, name)` (e.g. Tandoor, Grill, Beverages)

## Ordering
- `orders(id, outlet_id, table_session_id, channel[QR|WAITER|ADMIN], placed_by_user_id null, status, note, created_at)`
- `order_items(id, order_id, item_id, variant_id, qty, unit_price_minor, status, station_id, est_prep_minutes)`
- `order_item_events(id, order_item_id, from_status, to_status, actor, at)`

Order-item states: `PLACED → CONFIRMED → ASSIGNED → PREPARING → READY → SERVED`; side states `CANCELLED`, `REASSIGNED`. QR orders enter `PLACED`; if `qr_confirm_required`, a waiter/admin moves them to `CONFIRMED`, otherwise they auto-confirm. Only `CONFIRMED` orders enter routing, and a whole order goes to one chef (see 04).

## Kitchen
- `chef_profiles(user_id, outlet_id, station_ids, on_shift, last_completed_at)`
- `assignments(id, order_id, chef_user_id, queue_position, status[QUEUED|ACCEPTED|PREPARING|READY|HOLD|REASSIGNED], assigned_at, accepted_at, completed_at, reason)`; one active assignment per order, strict order by `queue_position` per chef.
- `shifts(id, user_id, outlet_id, starts_at, ends_at)`

## Feedback
- `ratings(id, order_id, table_session_id, food_stars, service_stars, comment, cooked_by_user_id, review_status[NONE|PENDING|CONFIRMED|DISMISSED], created_at)`; one per session; only `food_stars` is attributed to the chef.

## Billing
- `bills(id, order_ids, subtotal_minor, discount_minor, tax_minor, total_minor, status)`, `bill_lines`
- `payments(id, bill_id, method, amount_minor, provider_ref, status)`
- `tax_rules(id, hotel_id, code, rate_bp, valid_from, valid_to)` (basis points, effective-dated)

## Rooms (phase 8)
`room_types, rooms, rate_plans, bookings, folios, folio_lines, housekeeping_tasks`. Room-service orders reuse `orders` with `channel=ROOM` and a `booking_id`.

## Expenses (added 2026-11-18)
- `expense_categories(id, hotel_id, name)` defaults seeded at onboarding (Groceries, Vegetables, Gas, Salaries, Rent, Utilities, Maintenance, Other).
- `expenses(id, hotel_id, outlet_id null, category_id, amount_minor, spent_on, vendor, note, payment_method[CASH|UPI|CARD|BANK], created_by)`.

## Plan billing
- `subscription_invoices(id, hotel_id, subscription_id, amount_minor, status, provider_ref, period_end)` one row per Razorpay `subscription.charged`.
- `webhook_events(id, provider, event)` idempotency ledger (platform only).

## Bills, printing, push (added 2026-11-18)
- `bills(id, hotel_id, outlet_id, table_id, fy, number, invoice_no, doc_type[TAX_INVOICE|BILL_OF_SUPPLY|BILL], status[ISSUED|PAID|VOID], subtotal_minor, tax_minor, cgst_minor, sgst_minor, service_charge_minor, total_minor, payment_method, paid_at, print_count, void_reason)`; unique `(hotel_id, fy, number)`. `bill_lines` snapshot names and prices. `orders.bill_id` links orders to their bill.
- `print_log(kind[KOT|BILL], order_id|bill_id, copy_no, printed_by, at)`.
- `orders`: `served_by_user_id`, `kot_print_count`, `on_paper` (handed to kitchen as a printed KOT).
- `device_tokens(hotel_id, user_id, token unique, platform[android|ios|web])`.
- `hotel_branding(hotel_id pk, address_line, logo_png, logo_version)`: logo is decoded and re-encoded as PNG on upload (never stored as uploaded).
- `expense_categories.hotel_id` is nullable: NULL = global (super admin). RLS: SELECT own or global; write own only. Plus `is_active`.

Order states gain one edge: `CONFIRMED -> PREPARING` directly when a KOT is printed and sent on paper (no chef assignment).

## Implementation notes (v1)
- `users.role_id` (one role per user) instead of `user_outlet_roles`; tables carry `area` as text instead of an `areas` table; one menu per outlet (`menu_categories`, `menu_items`). Each is a planned extension, not a change of direction.

## Order workflow (added 2026-11-19)
- `orders.pickup_alerted_at`: set when the no-pickup alert fired (once per order).
- Table state is derived: `OCCUPIED` / `BILL_PENDING` / `FREE` (docs/04). Tables list returns `state`, `unbilled_orders`, `unbilled_total_minor`.

## Billing a table visit (added 2026-11-20)
- One bill per **table visit**: from the first order until the bill is paid. Paying ends the visit (QR sessions
  close); the next order at that table starts a new visit and a new bill.
- Guest orders more after the bill was issued (unpaid): billing the table again **cancels the issued bill**
  (`void_reason` "Items added: replaced by <new invoice>") and issues **one** bill with every order of the visit.
  A GST invoice is never edited; the cancelled number stays in the register, numbering stays gapless.
- Orders still being prepared: billing returns `orders_not_served` with their numbers; the client warns and may
  retry with `include_unserved` (decided 2026-11-20: warn and allow).
- Cancelling an order that is on an issued bill cancels that bill (bill the table again); on a paid bill it is
  refused (`on_paid_bill`; refunds are a separate flow).
- Any bill, paid included, can be printed again; copies after the first say DUPLICATE and are logged.

## Takeaway orders (added 2026-11-20)
- An order with no table: `channel = TAKEAWAY`, optional `customer_name` / `customer_phone` (migration 0006). Its
  order number is the pickup token. Same kitchen flow as table orders; "served" means handed over.
- Billed **per order** (`POST /bills {order_id}`), not per table visit; warn-and-allow while still cooking; an
  order is billed once (`already_billed`). Never affects table state. KOT and bill say TAKEAWAY and the name.

## Discounts (added 2026-11-20)
- A bill-level discount, by percent (`percent_bp`, 1000 = 10%) or amount, with a required reason. Stored on the bill
  (`discount_minor`, `discount_reason`, `discount_by`) and shared across bill lines (`bill_lines.discount_minor`,
  largest-remainder so the shares add up to the paisa). Migration 0007.
- **GST on the discounted value**: each line is taxed on (line total - its share); service charge applies to the
  discounted subtotal. Pure maths: `pricing.apply_discount`.
- **Per-role maximum** `roles.max_discount_bp` (0 = none). Defaults: Hotel admin 100% (locked), Manager 20%,
  Cashier 10%, others 0; the admin changes them. A discount's share of the subtotal is rounded **up** before the check,
  so rounding can never beat the limit. Enforced on the server (`discount_limit`, with `max_bp`).
- Discounting an issued, unpaid bill re-issues it (old bill cancelled "Discount applied: replaced by ...").
- The Overview's revenue is order value before discounts; Reports (checklist 7) will show net sales.

## Payments (added 2026-11-20)
- `bill_payments(hotel_id, bill_id, method[CASH|UPI|CARD], amount_minor > 0, reference, received_by, created_at)`,
  RLS like every hotel table (migration 0008; earlier paid bills back-filled with one payment each).
- A bill is paid in one go (`settle`) or in parts; when the parts reach the total it becomes PAID with
  `payment_method` = the single method, or `MIXED`. Paying more than is due is refused (`overpayment`); change is
  given outside the app.
- Equal split: shares of what is due that add up to the paisa (first shares take the leftover paise); each guest pays
  theirs as a part. Split **by items** (a separate GST invoice per guest) is not built yet.
- A part entered by mistake can be removed while the bill is unpaid (audited); a paid bill's takings are final.
- A bill with parts paid cannot be voided by hand (`has_payments`). When a bill is replaced (items added, discount),
  its payments move to the replacement; if they would exceed the new total, the replacement is refused.
- "My work" counts the money each person received (payments), not the bills they raised.

## Audit
`audit_log(id, hotel_id, actor, action, entity, entity_id, before, after, at)`.
