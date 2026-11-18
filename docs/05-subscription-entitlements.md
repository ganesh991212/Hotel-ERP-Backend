# 05. Subscription and entitlements

Super admin owns plans and can override per hotel. Hotel admins can only choose within what their plan allows.

## Feature keys
Boolean flags: `QR_ORDERING`, `WAITER_ORDERING`, `AUTO_ROUTING`, `ADMIN_PRINT_MODE`, `CHEF_ACCEPT_MODE`, `EXPENSES`, `CUSTOM_ROLES`, `DEVICE_DISPLAYS`, `RATINGS`, `RATING_WEIGHTED_ROUTING`, `MULTI_STATION`, `SPLIT_BILLING`, `REPORTS_ADVANCED`, `API_WEBHOOKS`, `ROOMS`, `ROOM_SERVICE`.
Implemented so far (`app/platform/features.py`): the first seven plus `RATINGS`, `REPORTS_ADVANCED`, `API_WEBHOOKS`, `ROOMS`.
Numeric limits: `MAX_OUTLETS`, `MAX_TABLES`, `MAX_STAFF`, `MAX_CHEFS`, `MAX_MENU_ITEMS`, `MAX_ORDERS_PER_MONTH` (null = unlimited).

## Illustrative plans (prices not decided; set after market quotes)
| | Starter | Growth | Pro |
|---|---|---|---|
| Outlets | 1 | 3 | unlimited |
| Waiter ordering | yes | yes | yes |
| QR ordering | no | yes | yes |
| Kitchen modes | CHEF_ACCEPT, ADMIN_PRINT | + AUTO_ASSIGN | + stations |
| Expenses | yes | yes | yes |
| Custom roles | no (system roles editable) | yes | yes |
| Ratings | no | yes | + weighted routing |
| Rooms | no | no | yes |

## Placeholder prices (editable by super admin at any time)
Seeded in paise and flagged `is_placeholder` until real quotes exist (see 01). **These are not market-validated.**

| Plan | Monthly | Annual |
|---|---|---|
| Starter | ₹999 | ₹9,990 (10 months) |
| Growth | ₹2,499 | ₹24,990 |
| Pro | ₹4,999 | ₹49,990 |

Changing a plan price never changes existing subscribers until renewal (grandfathered by default; super admin can force). Limits and flags are edited in the same screen.

## Resolution
`effective(hotel, key) = override(hotel, key) ?? plan_feature(hotel.plan, key)`. A suspended or expired (past grace) hotel resolves every write-feature to false; reads of own data stay available for 30 days for export.

## Enforcement
One dependency path: `require_feature("QR_ORDERING")`, `require_limit("MAX_TABLES", current_count)`. Applied at the router level. Outlet settings that choose a gated option (for example `kitchen_mode=AUTO_ASSIGN`) are validated on write and re-checked at use.

## Downgrade
Existing data stays. Over-limit resources become read-only (for example tables beyond the new limit cannot take orders) and the admin sees a banner with what to remove or re-upgrade. Never delete on downgrade.

## Lifecycle
TRIAL (N days) → ACTIVE → PAST_DUE (grace, default 7 days, full access with banner) → SUSPENDED. Razorpay subscription webhooks drive transitions; handlers are idempotent on event id. Super admin can manually extend or comp a hotel with an audited reason.

## Client contract
`GET /me/entitlements` returns the resolved map for the UI; the UI hides and explains locked features (with an upgrade prompt) but the server is the authority.
