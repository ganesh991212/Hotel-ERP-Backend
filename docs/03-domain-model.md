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
- `outlets(id, hotel_id, name, kitchen_mode[AUTO_ASSIGN|CHEF_ACCEPT|ADMIN_PRINT], require_accept, max_queue_orders, rating_band_minutes, qr_confirm_required, ratings_enabled, business_day_cutoff)`
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

## Audit
`audit_log(id, hotel_id, actor, action, entity, entity_id, before, after, at)`.
