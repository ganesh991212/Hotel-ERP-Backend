# 09. Open questions

Answer when convenient; each has a default so work is not blocked.

| # | Question | Default if unanswered |
|---|---|---|
| 1 | Product name | `vethra_hotel` working name |
| 2 | Target customer: single restaurants, hotels with several outlets, or chains? | Single hotel, 1 to 5 outlets |
| 3 | Should waiters and chefs use their own phones, or hotel-provided tablets? | Both supported |
| 4 | Kitchen printers: which model/connection? | Network ESC/POS 80 mm |
| 5 | Is offline operation a v1 requirement? | Waiter offline queue + stale KDS banner |
| 6 | Split billing and merge: needed in v1? | Yes, plan-gated |
| 7 | Zomato/Swiggy order integration? | Out of scope for v1 |
| 8 | Inventory and recipe costing depth? | Out of scope for v1; stock-out toggle only |
| 9 | Can chefs see their own ratings? | Hotel setting, default off |
| 10 | Should ratings be able to lower a chef's share of orders? | Off by default, hotel opt-in |
| 11 | Languages for staff UI | English + Telugu + Hindi |
| 12 | Hosting region and budget | India region, managed Postgres/Redis |
| 13 | Plan prices | After 5 to 10 owner interviews and competitor quotes |
| 14 | GST treatment | Captured per hotel at onboarding (registration type, GSTIN, state, service charge); CA reviews the default rate table and invoice formats once before Phase 5 |
| 15 | Diner OTP on first scan | Hotel setting, default off |

## Decisions recorded
- Repos: `Hotel-ERP-Backend` (this, with all docs) and `Hotel-ERP-Frontend`. Both currently public; owner will make them private later. Until then: no secrets, no customer data.
- Platform model: MyGate-style multi-tenant platform with feature registry, roles, webhooks/APIs (01).
- Chef model: whole order to one chef, strict queue; AUTO_ASSIGN or CHEF_ACCEPT (04).
- Ratings: handled as in 04 (food/service split, near-tie only, off by default).
- Prices: placeholders, editable by super admin (05).
- Printers: network, USB or Bluetooth all supported (07).
- Devices: phones, tablets, monitors and TVs, via pairing (07).
- GST: captured at hotel onboarding; billing follows it (03).
- Scope: dining first, rooms phased later; India market.

- Owner request 2026-11-18: **CHEF_ACCEPT (chef picks up) is the core flow**, so it is in every plan, not Pro only (05 updated). Super admin can still change it per plan or hotel.
- Owner request 2026-11-18: **expenses** module added (03, 06, BUILD-PLAN).
- Owner request 2026-11-18: hotel admins configure **what each role can do** (permission matrix). Editing system roles is in every plan; creating new roles needs `CUSTOM_ROLES`.
- UI reference: a commercial mint-green dashboard template the owner shared. Its licence forbids redistributing its source, and the repos are public, so the Flutter UI reproduces the **visual language only**; no code or assets were copied.
- Plan billing goes through **Razorpay subscriptions** (hosted payment page, webhook activation). Needs a Razorpay account and keys in the secret manager before go-live.

- 2026-11-18: reuse from the owner's other apps: push (GigBridge api+app, ported with an async fix), PDF printing (CMS app invoice generator, adapted), sound player (GigBridge). Vethra (school fees) had nothing directly reusable.
- 2026-11-18: printing supports every printer through two routes (OS print dialog PDF; raw ESC/POS to network thermal printers from the mobile app). Bills numbered per hotel per FY.
- 2026-11-18: expense categories are global (super admin) or hotel-only.

## Still open
- **Firebase project** for push (FCM keys on the server; google-services.json / GoogleService-Info.plist / web config in the apps).
- **iOS sound files** must be added to the Runner target in Xcode before the custom sound plays on iPhone.
- **Paid-bill corrections**: void is allowed with reason today; a CA should confirm whether credit notes are required.
- **Diner page technology.** Measured Flutter web first load is 3.2 MB gzipped. Options: (a) keep Flutter web and accept ~4-8 s first load on 4G, (b) build a light HTML diner page (~100-200 KB) and keep Flutter for staff. Recommendation: (b) before launch; the API does not change.
- **Payments provider account.** Razorpay keys, webhook URL and plan IDs.
- Offline guarantees per device type (default in 07).
- Product name.

## Inputs pending from you
- Razorpay account (test keys first) to verify checkout and webhooks end to end.
- Whether to buy a licence for the reference template (only relevant if you want closer fidelity; even then its source cannot go in a public repo).
