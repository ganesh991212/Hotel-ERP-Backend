# BUILD-PLAN (single tracker)

Legend: [ ] todo, [x] done. Each task points at the doc section that defines it.

## Phase 0: Foundation (design)
- [x] Repo and folder structure
- [x] Project rules (`CLAUDE.md`)
- [x] Market analysis, architecture, domain model, routing, entitlements, API surface, realtime/printing, security, open questions
- [x] Repos created (Backend, Frontend); docs live only in Backend `docs/`
- [x] Docs revised for strict chef queue, accept mode, ratings, devices, placeholder prices
- [ ] Owner review of docs; answer remaining items in `09-open-questions.md`
- [ ] CI skeleton (lint, type check, pytest), docker-compose for Postgres + Redis
- [ ] FastAPI project skeleton with health endpoint and OpenAPI export

## Phase 1: Tenancy, auth, super admin (02, 05, 08)
- [ ] Postgres schema: hotels, outlets, users, roles, permissions, refresh_tokens
- [ ] RLS policies and tenant-context middleware; cross-tenant read test
- [ ] Login, refresh rotation with reuse detection, logout
- [ ] RBAC with default roles and custom permission sets
- [ ] Plans, plan_features, subscriptions, per-hotel overrides
- [ ] `require_feature` / `require_limit` dependencies with tests
- [ ] Super admin API: create hotel, assign plan, suspend, override entitlements
- [ ] Hotel admin API: outlets, users, roles
- [ ] Audit log
- [ ] Hotel onboarding captures `hotel_tax_profile` (registration type, GSTIN with checksum, state, service charge) (03)
- [ ] Feature catalog/registry; per-hotel enablement by super admin (03, 05)
- [ ] Seed plans with placeholder prices (`is_placeholder`), editable by super admin (05)
- [ ] Webhook endpoints and API keys per hotel, signed deliveries with retry (03)
- [ ] Device registry and 6-digit pairing for TVs/tablets (03, 07)
- [ ] Staff attendance and shifts

## Phase 2: Catalog and tables (03, 06)
- [ ] Menus, categories, items, variants, modifier groups
- [ ] Item to station mapping, prep time estimate per item
- [ ] Availability toggle (sold out) and schedule (breakfast/dinner)
- [ ] Areas and tables; QR token generation and rotation; printable QR sheet
- [ ] Image upload (object storage) and CDN-friendly sizes

## Phase 3: Ordering (03, 07, 08)
- [ ] Table sessions (open, join, close) and diner token
- [ ] Diner read API: menu by QR token (cacheable)
- [ ] Order create: QR channel and waiter channel; server-side pricing
- [ ] Optional confirmation step (waiter/admin approves QR orders)
- [ ] Order and order-item state machine with audited events
- [ ] WebSocket channels per outlet; diner order status
- [ ] Abuse controls: rate limits, OTP option, session expiry

## Phase 4: Kitchen (04, 07)
- [ ] Chef profiles, stations, shifts, max_queue_orders
- [ ] Chef queue: strict order, only head startable, HOLD, admin reorder/reassign (04)
- [ ] Routing engine (pure function) with all 13 worked examples as tests (04)
- [ ] Per-outlet lock; unassigned queue; re-run on READY/shift/cancel
- [ ] Accept timeout and off-shift redistribution
- [ ] Modes: AUTO_ASSIGN (+ require_accept), CHEF_ACCEPT with atomic claim, ADMIN_PRINT
- [ ] Printers registry, print jobs, print station; Flutter printing package spike (07)
- [ ] KOT rendering (58 mm and 80 mm)
- [ ] KDS API

## Phase 5: Billing (03, 05)
- [ ] Bills from orders; split, merge, discounts (permissioned)
- [ ] Effective-dated tax rules; invoice type follows `hotel_tax_profile` (tax invoice / bill of supply / plain bill)
- [ ] Sequential invoice numbers per GSTIN per financial year; immutable; credit notes
- [ ] CA review of default rate table and invoice formats (once, before release)
- [ ] Payments: cash, card record, UPI via Razorpay; webhooks idempotent
- [ ] Day close and shift close reports

## Phase 6: Ratings and reports (04)
- [ ] Rating request after serve: food and service stars, one per verified session
- [ ] Attribution of food rating to cooking chef; 60-day decayed, smoothed score; no effect below n=10
- [ ] Low-rating (<=2) manager review queue
- [ ] Rating used only as a near-tie breaker within `rating_band_minutes`; per-hotel toggle, off by default
- [ ] Sales, item, chef performance, table turnover reports

## Phase 7: Flutter apps (UI from reference videos)
- [ ] Design tokens and component library
- [ ] Staff app (waiter, chef KDS, cashier)
- [ ] Admin web (menus, tables, staff, reports)
- [ ] Super admin web (hotels, plans)
- [ ] Diner web (lean build; spike size first)

## Phase 8: Rooms
- [ ] Room types, rooms, rate plans
- [ ] Bookings, check-in/out, folio
- [ ] Housekeeping tasks
- [ ] Room-service orders into the same kitchen pipeline
