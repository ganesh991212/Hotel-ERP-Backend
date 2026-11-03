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

## Still open
- Offline guarantees per device type (default in 07).
- Product name.

## Inputs pending from you
- UI reference videos (Phase 7).
- GitHub repo for this project, then I attach it with `add_repo` to push.
