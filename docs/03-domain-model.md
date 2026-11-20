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
  theirs as a part. Split **by items** gives each guest a GST invoice of their own (below).
- A part entered by mistake can be removed while the bill is unpaid (audited); a paid bill's takings are final.
- A bill with parts paid cannot be voided by hand (`has_payments`). When a bill is replaced (items added, discount),
  its payments move to the replacement; if they would exceed the new total, the replacement is refused.
- "My work" counts the money each person received (payments), not the bills they raised.

## Split by items (added 2026-11-20; prior art in docs/14)
- A table visit can be billed as 2 to 20 **guest bills**. Staff say who had what: whole units of a line per guest, or
  a line shared equally by some guests (its value split to the paisa; printed as "Naan (1/3 share)", qty 1 at the
  share's price, so qty x price = line total still holds).
- Each guest bill is a normal GST invoice with its **own number**; tax is calculated on its own lines (never a share
  of a total), service charge on its own subtotal. Guest totals can differ from one whole bill by a few paise of
  rounding; each invoice is right on its own.
- `bills.split_group_id` (migration 0009) = the first guest bill's id for every guest bill of the visit; `guest_no`
  of `guest_count`. The visit's orders point to the first guest bill (one bill per order still holds).
- An ordinary unpaid bill for the visit is cancelled as **replaced** (refused if part paid). Food ordered after a
  split gets a bill of its own; the guest bills are untouched.
- Each guest pays their own bill (in parts if they like). The table stays BILL_PENDING and the QR session open until
  the last guest bill is paid.
- Cancelling: a split is cancelled as a whole (all guest bills VOID, the food back to unbilled) and only while no
  guest has paid (`split_partly_paid`). Cancelling an order on an unpaid split does the same.
- Discounts: a discounted bill is not split (`bill_discounted`; cancel it first), because the discount would be
  lost. Guest bills cannot be discounted yet; a per-guest discount comes later.

## Variants and add-ons (added 2026-11-20; prior art in docs/14)
- `menu_item_variants(item_id, name, price_minor, is_available, sort)`: an item has none or two to ten sizes (Half /
  Full). With sizes, one must be chosen and its price replaces the item price; each size can be sold out alone.
- `addon_groups(name, min_select, max_select)` with `addons(name, price_minor, is_available)`, reusable across
  items via `menu_item_addon_groups(item_id, group_id, sort)`. `min..max` counts total picks in the group (0..3
  optional extras; 1..1 "choose one"). Picking the same add-on twice is `qty`, not two entries.
- Every channel goes through one check (`ordering.service._choose`): size required/valid/available, add-on offered
  with this item/available, group counts. Nothing is pre-selected.
- Price per unit = size (or item) price + add-ons x picks; **add-ons take the dish's GST rate** (restaurant food is
  one composite supply). Rates still come from effective-dated tax rules (rule 7).
- Order lines snapshot `variant_id`, `variant_name`, `addons` [{id, name, price_minor, qty}]; `name` reads
  "Biryani (Half)". Bill lines read "Biryani (Half) + Raita, 2 x Cheese", so different add-ons never merge.
- Lines are matched by position when an order is created: the same dish can be on one order twice. Each item keeps
  its `line_no` (migration 0012), and orders, KOTs and screens list items in the order they were entered.
- All four tables have `hotel_id` and RLS (migration 0010).

## Happy hour (added 2026-11-20; prior art in docs/14)
- `price_rules(name, percent_bp 1..9000, days bitmask Mon=1..Sun=64, start_time, end_time, category_ids[] | NULL,
  is_active)`, RLS (migration 0011). Times are the hotel's local time; end before start crosses midnight and belongs
  to the day it started (Sat 22:00-01:00 covers Sunday 00:30).
- Applied in `ordering.service.quote` for every channel at the moment the order is priced: the single best live rule
  for the dish's category takes its percent off the **dish/size price**; add-ons keep their price; rules never stack.
  A cart quoted at 6:59 and sent at 7:01 gets the 7:01 price.
- Order lines snapshot `list_unit_price_minor` (before the rule) and `price_rule` (its name). Bill lines read
  "Naan (Happy hour)", so the item report shows happy-hour sales as their own row with real amounts.
- `quote` now uses the hotel's local date for the GST rate lookup too (it used the server's UTC date, which was a day
  behind between 00:00 and 05:30 IST).

## Combos (added 2026-11-20; prior art in docs/14)
- A combo is an ordinary `menu_items` row with `menu_item_components(combo_item_id, component_item_id, qty, sort)`
  (RLS, migration 0013): its own price, category, GST code, sold-out switch, sizes, add-on groups and happy hour.
  Choice slots ("choose your drink") are add-on groups (min 1, max 1; ₹0 or a surcharge).
- At least two dishes (or one dish twice); no combo inside a combo; a dish in a combo can't be deleted
  (`item_in_combo`). Saving components sets the combo's preparation time to its slowest dish.
- Ordering: refused while any component is sold out (`item_unavailable`); diners see such a combo as sold out.
  Priced and taxed as one supply at the combo's price and GST code.
- Order lines snapshot `components` [{item_id, name, qty per combo}]; the KOT prints each dish x quantity under the
  combo. Bills and reports show the combo as one line; per-component stock comes with inventory (item 9).

## Serving hours (added 2026-11-20; prior art in docs/14)
- `menu_categories.days` (Mon=1 ... Sun=64), `available_from`, `available_to` (both or neither; migration 0014).
  No times = served all day on those days. Same window logic as happy hour (`pricing.in_window`): hotel-local time,
  a window crossing midnight belongs to the day it started.
- `quote` refuses dishes from a category outside its hours: `outside_hours` (409, details: items, category, hours).
  QR diners can never override; staff orders may send `allow_outside_hours: true` after the app warned. The staff
  composer's running quote always prices (only placing the order warns).
- The diner menu marks such a section `open_now: false` with its `hours` (dishes keep their real sold-out state, so
  the menu says "Served 07:00-11:00", not "Sold out").

## Day close (added 2026-11-20; prior art in docs/14)
- `day_closes` (RLS, migration 0015): one per hotel day, final once saved. A snapshot of opening float, cash sales
  (`bill_payments` CASH on the hotel day, every device, part payments included), cash paid out (expenses paid in
  CASH that day), expected = float + cash sales - paid out, counted, difference = counted - expected (+ over,
  - short), cash kept in the drawer and the deposit (counted - kept), UPI and card received.
- The opening float is the previous close's cash kept (typed only for the very first close), so it can't be edited
  to hide a gap. Any difference needs a note. Future days can't be closed; a day can be closed once.
- Cash taken on that day after the close is reported as `cash_after_close_minor`, never folded in.
- Permission `dayclose.manage` (admin, manager, cashier; migration grants it to existing hotels' system roles).

## Table visits (added 2026-11-20; prior art in docs/14)
- `dining_tables.seated_at`, `guests`, `hold_state` (RESERVED | CLEANING), `hold_note` (migration 0016). State shown:
  OCCUPIED (open orders) > BILL_PENDING (owes) > SEATED (seated, nothing ordered) > hold (CLEANING / RESERVED) > FREE.
- Seating (`POST /tables/{id}/seat`) or the first order (any channel) starts the visit and clears a hold. Paying the
  table's last open bill ends the visit and sets CLEANING; "Clean" (hold FREE) frees it. Reserved is manual, with a
  short note; it can't be set while guests are seated. Guests who leave without ordering: hold FREE ends the visit.
- Covers: `bills.guests` = the visit's guests, recorded on its bill (only the first guest bill when split; never on a
  later extra bill). Reports: `covers` and `avg_per_cover_minor` (total of the bills that recorded guests / covers).

## Audit
`audit_log(id, hotel_id, actor, action, entity, entity_id, before, after, at)`.
