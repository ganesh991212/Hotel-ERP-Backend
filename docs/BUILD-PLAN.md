# BUILD-PLAN (single tracker)

Legend: [ ] todo, [x] done. Each task points at the doc section that defines it.

## Phase 0: Foundation (design)
- [x] Repo and folder structure
- [x] Project rules (`CLAUDE.md`)
- [x] Market analysis, architecture, domain model, routing, entitlements, API surface, realtime/printing, security, open questions
- [x] Repos created (Backend, Frontend); docs live only in Backend `docs/`
- [x] Docs revised for strict chef queue, accept mode, ratings, devices, placeholder prices
- [ ] Owner review of docs; answer remaining items in `09-open-questions.md`
- [x] CI skeleton (ruff, pytest on Postgres, OpenAPI drift check), docker-compose for Postgres + Redis (type check still todo)
- [x] FastAPI project skeleton with health endpoint and OpenAPI export (`docs/api/openapi.json`)

## Phase 1: Tenancy, auth, super admin (02, 05, 08)
- [x] Postgres schema: hotels, outlets, users, roles, permissions, refresh_tokens
- [x] RLS policies and tenant-context middleware; cross-tenant read/update/insert test over every tenant table
- [x] Login, refresh rotation with reuse detection, logout
- [x] RBAC with default roles and custom permission sets (hotel admin edits a role matrix; HOTEL_ADMIN locked)
- [x] Plans, plan_features, subscriptions, per-hotel overrides
- [x] `require_feature` / `require_limit` dependencies with tests
- [x] Super admin API: create hotel, assign plan, suspend, override entitlements
- [x] Hotel admin API: users, roles (one user = one role for now)
- [ ] Hotel admin API: multiple outlets (schema supports it; API and UI use the first outlet)
- [x] Audit log (all admin mutations; append-only enforcement still todo)
- [x] Hotel onboarding captures `hotel_tax_profile` (registration type, GSTIN with checksum, state, service charge) (03)
- [x] Feature registry in code (`app/platform/features.py`); per-hotel enablement via overrides (03, 05)
- [x] Seed plans with placeholder prices (`is_placeholder`), editable by super admin (05)
- [x] Plan billing: Razorpay subscription checkout, signed idempotent webhooks, invoices, TRIAL/ACTIVE/PAST_DUE/SUSPENDED (05); not yet run against a live Razorpay account
- [ ] Scheduled lifecycle job (lifecycle is computed on read today; no reminder emails)
- [ ] Webhook endpoints and API keys per hotel, signed deliveries with retry (03)
- [ ] Device registry and 6-digit pairing for TVs/tablets (03, 07)
- [ ] Staff attendance and shifts

## Phase 2: Catalog and tables (03, 06)
- [x] Categories and items (one menu per outlet)
- [ ] Multiple menus, variants, modifier groups
- [x] Prep time estimate per item
- [ ] Item to station mapping
- [x] Availability toggle (sold out), pushed live to staff screens
- [ ] Availability schedule (breakfast/dinner)
- [x] Tables with area (text); QR token generation and rotation; printable QR sheet (in-app)
- [ ] Image upload (object storage) and CDN-friendly sizes

## Phase 3: Ordering (03, 07, 08)
- [x] Table sessions (open, 3 h expiry) and diner token
- [ ] Table session join/close on bill payment
- [x] Diner read API: menu by QR token
- [ ] Diner menu caching (ETag/CDN)
- [x] Order create: QR channel and waiter/admin channel; server-side pricing; quote endpoints; idempotent `client_ref`
- [x] Optional confirmation step (waiter/admin approves QR orders)
- [x] Order and order-item state machine with audited events
- [x] WebSocket channels per outlet and per diner session, `seq` per channel, published after commit
- [ ] Redis fan-out for multiple API instances (hub is in-process today)
- [x] Abuse controls: session expiry, max open orders per session, item and quantity caps
- [ ] Abuse controls: rate limits, OTP option

## Phase 4: Kitchen (04, 07)
- [x] Chef profiles, on-shift flag, max_queue_orders
- [ ] Stations and shift schedules
- [x] Chef queue: strict order, only head startable, admin reassign/return to board (04)
- [ ] HOLD and admin reorder (example 10)
- [x] Routing engine (pure function); examples 1-9, 11-13 covered by pure or API tests (04)
- [x] Per-outlet advisory lock; unassigned queue; re-run on READY/shift/cancel
- [x] Off-shift redistribution
- [ ] Accept timeout (`require_accept`)
- [x] Modes: AUTO_ASSIGN, CHEF_ACCEPT with atomic claim (accept at head starts cooking)
- [ ] ADMIN_PRINT mode end to end (selectable, but printing below is not built)
- [x] KOT printing: server builds layout blocks; Flutter renders PDF (any printer via OS dialog, 58/80 mm/A4) and the API also returns ESC/POS for network thermal printers (Android/iOS raw TCP 9100) (07)
- [x] Paper path: printing a KOT can hand a waiting order to the kitchen on paper (leaves the chef board; staff mark ready)
- [x] Print log for every KOT and bill print; reprints labelled REPRINT/DUPLICATE
- [ ] Printers registry and server-side print queue for a shared print station (today: per-device printer settings)
- [ ] USB/Bluetooth raw printing from the app (today via OS driver + print dialog)
- [x] KDS API (`GET /kitchen/board`)

## Phase 5: Billing (03, 05)
- [x] Bills from a table's orders; snapshot lines; CGST/SGST split; settle (cash/UPI/card); void with reason (`bill.void`)
- [ ] Split, merge, discounts (permissioned)
- [ ] Effective-dated tax rules; invoice type follows `hotel_tax_profile` (tax invoice / bill of supply / plain bill)
- [x] Sequential bill numbers per hotel per financial year (`INV/26-27/00001`) under a lock; issued bills immutable
- [ ] Credit notes instead of void for paid bills
- [ ] CA review of default rate table and invoice formats (once, before release)
- [ ] Payments: cash, card record, UPI via Razorpay; webhooks idempotent
- [ ] Day close and shift close reports

## Notifications (added 2026-11-18)
- [x] FCM HTTP v1 push ported from GigBridge (async, post-commit, stale-token pruning, shared-device re-binding)
- [x] Who gets what: new order -> chefs on shift (CHEF_ACCEPT) / KOT printers (ADMIN_PRINT) / assigned chef (AUTO); QR needing confirmation -> confirmers; ready -> servers
- [x] Loud original alert sounds; Android channels on the alarm stream; iOS time-sensitive; in-app loud playback on realtime events (web cannot customise push sounds)
- [ ] Firebase project + keys; verify on real Android/iOS devices; add the two .wav files to the Xcode Runner target
- [ ] Migrate to firebase_messaging register()/onRegistered once FID delivery is verified

## Phase 6: Ratings and reports (04)
- [ ] Rating request after serve: food and service stars, one per verified session
- [ ] Attribution of food rating to cooking chef; 60-day decayed, smoothed score; no effect below n=10
- [ ] Low-rating (<=2) manager review queue
- [ ] Rating used only as a near-tie breaker within `rating_band_minutes`; per-hotel toggle, off by default
- [x] Dashboard stage timings: wait for chef, preparation, serving, order-to-table (avg + p90); serving time per server
- [ ] Sales, item, chef performance, table turnover reports

## Phase 7: Flutter apps (UI from reference videos)
- [x] Design tokens (light + dark) and component library
- [x] Staff app: waiter floor, chef kitchen board (cashier waits on billing)
- [x] Admin web: dashboard, menus, tables/QR, team and role matrix, expenses, settings, plan checkout
- [x] Super admin web: overview, hotels, plans, overrides
- [x] Diner web in Flutter (menu, cart, live tracker)
- [x] Diner size spike: 3.2 MB gzipped first load (1.08 MB JS + 2.08 MB CanvasKit). Misses the 3 s on 4G target; see 09
- [ ] Telugu and Hindi translations (ARB keys exist, English only)
- [ ] Offline waiter queue and local KDS cache (07)
- [ ] Widget and golden tests

## Expenses (added 2026-11-18)
- [x] Expense categories (defaults seeded per hotel), expenses with vendor, payment method, date
- [x] Monthly summary by category and day; net month on the dashboard (revenue minus expenses)
- [x] Global categories set by super admin (visible to every hotel, read-only to them) plus hotel-only categories; RLS read-own-or-global, write-own
- [ ] Receipts (image upload), recurring expenses, export

## Phase 8: Rooms
- [ ] Room types, rooms, rate plans
- [ ] Bookings, check-in/out, folio
- [ ] Housekeeping tasks
- [ ] Room-service orders into the same kitchen pipeline
