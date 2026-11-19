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
- [x] Table QR printing: one table or all; A4 card sheet or roll tickets; native ESC/POS QR on thermal printers
- [x] Branded QR cards: hotel logo + name, one-line address, QR, table name, "Powered by" (POWERED_BY_* in .env); themed PDF cards (4 per A4, single large, roll) and ESC/POS raster logo + inverted table name
- [x] Hotel branding settings: logo upload (validated and re-encoded to PNG server-side) and address line
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
- [x] Firebase configured only in the server .env; apps fetch public config from `GET /push/config`; static web service worker
- [x] iOS alert sounds registered in the Runner target (pbxproj)
- [x] Browser: Enable alerts prompt (unlock sound + notification permission); loud sound while the tab is open
- [ ] Firebase project + keys in .env; verify on real Android/iOS devices
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
- [x] Telugu and Hindi UI (first-draft translations; native review pending), per-device language switch, on-demand Noto fonts, localised dates; prints stay English
- [x] Bottom navigation dock (all screen sizes; scrolls on phones)
- [ ] Offline waiter queue and local KDS cache (07)
- [ ] Widget and golden tests

## Expenses (added 2026-11-18)
- [x] Expense categories (defaults seeded per hotel), expenses with vendor, payment method, date
- [x] Monthly summary by category and day; net month on the dashboard (revenue minus expenses)
- [x] Global categories set by super admin (visible to every hotel, read-only to them) plus hotel-only categories; RLS read-own-or-global, write-own
- [ ] Receipts (image upload), recurring expenses, export

## Deployment (added 2026-11-18, docs/10)
- [x] Dockerfile, start/release scripts, CI builds the image
- [x] Startup guard: staging/production refuse to boot with missing or local config
- [x] Supabase roles + verification SQL, staging Blueprint (`render.yaml`), production Blueprint (not applied)
- [x] Env references `deploy/env.staging.example`, `deploy/env.production.example`; web build script (Frontend)
- [ ] Staging live on Render + Supabase, verification checklist passed
- [ ] Production: domain, Supabase Pro, Blueprint applied, restore drill, uptime alert
- [ ] Realtime fan-out via Redis or LISTEN/NOTIFY (needed before a second API instance)
- [x] Branches: `staging` (auto-deploys) -> `main` (production); superseded PR #2 kept in history at commit `a840958`
- [ ] Port the hardening items in docs/11 (login lockout and Argon2 off the event loop first)

## Order workflow (added 2026-11-19, docs/04 "Order workflow")
- [x] Serving mode per outlet (waiter serves / diner collects at counter); counter orders left out of serving average
- [x] No-pickup alert: configurable minutes (or off), admins + chefs on shift, once per order, hotel-scoped
- [x] Table state FREE / OCCUPIED / BILL_PENDING with amount owed; served-unbilled orders stay in "Needs attention"
- [x] Confirmed QR orders alert chefs (`order.confirmed`; was silent)
- [x] Orders list filters for the admin Orders screen
- [ ] Screens: "Next:" line per order, Bill pending tables, Orders screen, workflow settings
- [ ] Stations (bar / kitchen split): waiting on decision
- [ ] Automatic KOT printing: installed app + network printer only (browsers cannot print silently)

## Staff self-service (added 2026-11-19)
- [x] Change own password (signs out other devices); admin reset for staff, no escalation to the admin account
- [x] "My work" dashboard per role (cooking / serving / billing), today or 7 days, with team averages
- [ ] Forgot password by email (needs an email provider)

## Billing visits and long lists (added 2026-11-20)
- [x] One bill per table visit; items added after billing replace the unpaid bill; warn-and-allow while cooking
- [x] Orders and bills lists: date range, search, pages; indexes
- [x] Reprint any bill (DUPLICATE, logged)
- [ ] Bills register export (CSV) for the accountant

## Feature parity checklist (added 2026-11-20)

Compared with an 18-module restaurant app seen in a promo video. **That app is a front-end demo** (its own
Settings say "This build runs without a server. Data is saved in this browser"; Swiggy/Zomato are a "Demo feed";
payment gateway "Not connected"), so treat it as a list of ideas, not a proven competitor.

**Weight** = how much the module matters to an Indian restaurant deciding to pay (judgement, not measured: confirm
with 3 to 5 owners before building the big ones). Weights sum to 100. **Score** = weight x how complete ours is.
Pick from the top of "Next up"; tick sub-items as they ship and update the module's %.

| # | Module | Weight | Ours | Score | Status |
|---|---|---|---|---|---|
| 1 | Point of sale / billing | 10 | 40% | 4.0 | Partial |
| 2 | Orders (all channels) | 6 | 50% | 3.0 | Partial |
| 3 | Tables / floor plan | 6 | 55% | 3.3 | Partial |
| 4 | Kitchen display | 8 | 80% | 6.4 | Mostly done |
| 5 | Menu and pricing | 7 | 50% | 3.5 | Partial |
| 6 | Live dashboard | 6 | 70% | 4.2 | Mostly done |
| 7 | Reports | 7 | 15% | 1.1 | Missing most |
| 8 | Finance and GST | 7 | 40% | 2.8 | Partial |
| 9 | Inventory | 7 | 0% | 0 | Missing |
| 10 | Recipes and food cost | 5 | 0% | 0 | Missing |
| 11 | Purchases and suppliers | 4 | 0% | 0 | Missing |
| 12 | Customers (CRM) | 4 | 0% | 0 | Missing |
| 13 | Staff, shifts, attendance | 4 | 50% | 2.0 | Partial |
| 14 | Reservations and waitlist | 3 | 0% | 0 | Missing |
| 15 | Delivery (own riders) | 2 | 0% | 0 | Missing |
| 16 | Integrations | 6 | 25% | 1.5 | Partial |
| 17 | Branches (multi-outlet) | 3 | 10% | 0.3 | Missing most |
| 18 | AI insights | 1 | 0% | 0 | Missing |
| 19 | Settings, roles, permissions | 4 | 85% | 3.4 | Mostly done |
| | **Total** | **100** | | **35.4** | |

Ours but not in the demo (keep, they are selling points): QR table ordering with live diner tracking; SaaS plans,
subscriptions and feature gating; chef strict queue and auto-assign; no-pickup alerts; loud push alerts; Telugu and
Hindi; branded table QR cards; "My work" per role; one bill per table visit with GST-correct replacement.

### 1. Point of sale / billing (10)
- [x] Order per table by waiter/admin, prices from the server
- [x] GST bill (CGST/SGST, FY numbering), print (ESC/POS + PDF), settle Cash/UPI/Card, void, duplicate reprint
- [x] One bill per table visit; items added after billing replace the unpaid bill
- [ ] Takeaway / counter orders (no table)
- [ ] Discounts (amount or %, per role maximum, reason, audited)
- [ ] Split bill (by items or equally) and part payments (e.g. cash + UPI)
- [ ] Hold / resume an order; keyboard shortcuts for a counter PC
- [ ] Customer name/phone on the bill (feeds CRM)

### 2. Orders (6)
- [x] List with status filters, date range, search, pages; next-step line
- [x] Channels QR / Waiter / Admin
- [ ] Takeaway channel (with 1)
- [ ] Online channels (website ordering, Swiggy/Zomato via 16)

### 3. Tables / floor plan (6)
- [x] Areas, live status (free / occupied / bill pending), amount owed, QR per table
- [ ] Time seated and guest count (covers) per table
- [ ] Reserved and Cleaning states
- [ ] Waitlist (with 14)
- [ ] Move / merge tables

### 4. Kitchen display (8)
- [x] Live board, chef pick-up / auto-assign / paper KOT, strict queue, timers
- [x] Loud alerts, no-pickup alert, ready notifications
- [ ] Stations (bar, tandoor, Chinese...) with per-station routing
- [ ] Target time per dish with "Delayed" flag

### 5. Menu and pricing (7)
- [x] Items, categories, availability, veg/non-veg, prep time, tax rate
- [ ] Variants (half/full) and add-ons
- [ ] Combos
- [ ] Happy hour (time + days + categories + %)
- [ ] Time-based menus (breakfast / lunch / dinner)

### 6. Live dashboard (6)
- [x] Orders, revenue, average ticket, by hour, 7 days, channels, top items, month net, timings, chefs, servers
- [ ] Compare with the same day last week
- [ ] Payment mix (cash / UPI / card)
- [ ] Gross profit and food cost % (needs 10)

### 7. Reports (7)
- [ ] Day-by-day sales table (orders, gross, discounts, tax, net, payment modes)
- [ ] Item-wise and category-wise sales
- [ ] Staff-wise sales
- [ ] Export CSV / PDF (also the bills register for the accountant)

### 8. Finance and GST (7)
- [x] GST invoices; expenses with global and hotel categories; month revenue minus expenses
- [ ] GST summary for filing (GSTR-1 style: B2C totals by rate, HSN/SAC)
- [ ] Day close / cash drawer reconciliation (expected vs counted)
- [ ] Profit and loss by month

### 9. Inventory (7)
- [ ] Ingredients, units, stock levels, reorder level, low-stock alerts
- [ ] Auto-deduct stock from sales (needs 10)
- [ ] Wastage entries; stock counts

### 10. Recipes and food cost (5)
- [ ] Recipe per dish (ingredients x quantity), cost per plate, food cost %, margin

### 11. Purchases and suppliers (4)
- [ ] Suppliers, purchase orders, goods received (updates stock and average cost), supplier balance, returns

### 12. Customers / CRM (4)
- [ ] Customer list from bills and QR orders (phone, visits, lifetime spend), consent for messages
- [ ] Segments and simple campaigns (via 16 WhatsApp)

### 13. Staff, shifts, attendance (4)
- [x] Staff, roles, chef on/off shift, password change and reset, "My work"
- [ ] Attendance (clock in/out) and shift schedule
- [ ] Cash handover per shift

### 14. Reservations and waitlist (3)
- [ ] Bookings (name, phone, guests, time, table, notes) and today's arrivals
- [ ] Walk-in waitlist with quoted wait and "Seat" action

### 15. Delivery with own riders (2)
- [ ] Phone/website delivery orders, riders, assign, picked up / delivered, COD cash with riders

### 16. Integrations (6)
- [x] Printers (ESC/POS network, system print), FCM push, Razorpay for plan subscriptions
- [ ] Payment gateway for diner bills (UPI/cards online)
- [ ] WhatsApp (bill/receipt, order ready) - needs Meta business verification
- [ ] Swiggy / Zomato - needs partner API access or an aggregator middleware (e.g. UrbanPiper)

### 17. Branches (3)
- [x] Schema supports several outlets per hotel
- [ ] Switch outlet in the app; compare outlets; central menu

### 18. AI insights (1)
- [ ] Forecasts and tips from the hotel's own data (sales forecast, low-stock, upsell pairs). Rules first; no
      third-party AI on customer data without a decision

### 19. Settings, roles, permissions (4)
- [x] Roles, custom roles, permission matrix, plan-gated features, kitchen and workflow settings, branding
- [ ] Per-role maximum discount (with 1)

### Next up (weight x gap / effort)
1. POS: takeaway + discounts + split/part payment (+6 points, medium)
2. Reports: day-by-day + item-wise + CSV export, incl. bills register (+5, small)
3. Menu: variants/add-ons, happy hour, combos (+3.5, medium)
4. Finance: GST summary + day close (+3, medium)
5. Tables: covers, time seated, reserved/cleaning + reservations/waitlist (+5, medium)
6. Inventory + recipes + purchases (+16, large; confirm demand first)
7. Integrations: online bill payment, WhatsApp (+3, needs accounts and approvals)

## Phase 8: Rooms
- [ ] Room types, rooms, rate plans
- [ ] Bookings, check-in/out, folio
- [ ] Housekeeping tasks
- [ ] Room-service orders into the same kitchen pipeline
