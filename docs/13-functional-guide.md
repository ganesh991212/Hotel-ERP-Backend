# 13. Functional guide: how the whole app works

Plain-language description of every feature, screen, setting and rule, for owners, staff trainers, testers and
developers. **Kept current with every feature** (Definition of Done). Technical detail lives in docs/03 (data), 04
(kitchen), 06 (API), 10 and 12 (deployment); this guide says what people see and what happens.

Contents: 1 Who uses it · 2 Glossary · 3 Order life cycle · 4 Platform admin · 5 Hotel admin · 6 Floor and orders ·
7 Kitchen · 8 Menu · 9 Tables and QR · 10 Billing · 11 Team and roles · 12 Expenses · 13 Overview and "My work" ·
14 Settings · 15 Diner (QR) · 16 Alerts and notifications · 17 Printing · 18 Language, devices, sign-in ·
19 Plans and limits · 20 Rules the app always enforces · 21 Change log

---

## 1. Who uses it

| Person | Signs in? | Main screens |
|---|---|---|
| **Platform admin** (us, the software company) | Yes | Overview, Hotels, Plans, Categories |
| **Hotel admin** (owner) | Yes | Everything in their hotel |
| **Manager** | Yes | Like the admin, except roles and settings |
| **Cashier** | Yes | Floor, Orders, bills, own work |
| **Server / waiter** | Yes | Floor (take orders, confirm, serve), bills, own work |
| **Chef** | Yes | Kitchen screen, menu availability, own work |
| **Diner / guest** | No | Scans the table QR: menu, order, live status |

What each role may do is set per hotel (section 11). The bottom bar shows only the screens a person may open.

## 2. Glossary

| Word | Meaning |
|---|---|
| **KOT** | Kitchen Order Ticket: the slip that tells the kitchen what to cook (table, order number, time, items, notes, who took it). No prices. Printed for kitchens that work from paper, or reprinted when lost (marked "KOT REPRINT n"). |
| **Bill / invoice** | The guest's document with prices and tax. Its type follows the hotel's GST registration: **Tax invoice** (regular GST), **Bill of supply** (composition scheme), or **Bill** (not registered). Numbered `INV/26-27/00001` per financial year, never reused. |
| **Table visit** | From the first order at a table until its bill is paid. One visit gets one bill. |
| **Outlet** | A restaurant inside a hotel (restaurant, bar, room service). v1 shows one outlet per hotel. |
| **Paise** | All money is stored in paise (1 rupee = 100 paise) so totals never have rounding errors. |
| **Paper path** | An order handed to the kitchen as a printed KOT instead of the kitchen screen. |
| **Strict queue** | A chef cooks their orders one at a time, oldest first. |
| **Void / cancelled bill** | A bill withdrawn with a reason. It stays in the register; its number is never reused. |

## 3. Order life cycle

```
Placed --(confirm, if required)--> Confirmed --(chef picks up / auto-assigned)--> Queued/Cooking --> Ready --> Served --> (billed) --> Paid
                                         '--(KOT printed, paper path)--> Cooking on paper --> Ready
Any open order can be Cancelled with a reason (not after it is served).
```

| Status | Who moves it on | Screen shows "Next:" |
|---|---|---|
| **Placed** (waiting for confirmation) | Server or admin taps **Confirm** | confirm this order |
| **Confirmed** (waiting for a chef) | A chef taps **Accept** on the Kitchen screen; or auto-assign; or staff print the KOT | a chef picks it up / waiting for a free chef / print the KOT |
| **Queued** | The assigned chef starts it (only the first in their queue) | chef starts cooking |
| **Cooking** | The chef taps **Ready** (or staff, for paper orders) | chef is cooking |
| **Ready** | A server taps **Served** (counter mode: done automatically, the diner collects) | serve it to the table |
| **Served** | Billing the table | bill the table |

Every change is recorded with who, when, from and to (audit trail). If no chef picks up an order in time, an alert
goes out (section 16).

## 4. Platform admin

- **Overview**: hotels, subscriptions and platform totals.
- **Hotels**: register a hotel and its first admin (name, email, password, GST registration and GSTIN, state, plan);
  suspend or resume; change plan; per-hotel feature overrides.
- **Plans**: plans with features and limits (section 19). Prices are placeholders until market-validated.
- **Categories**: global expense categories every hotel sees (hotels can add their own, visible only to them).
- A hotel's data is never visible to another hotel; the platform admin works through separate, audited access.

## 5. Hotel admin at a glance

Bottom bar (scrolls sideways on phones; enlarges under the mouse on computers): **Overview, Floor, Orders, Kitchen,
Menu, Tables, Team, Expenses**. Top bar: hotel logo and name, language, **Settings**, alerts bell, profile menu.

## 6. Floor and Orders

### Floor
- **Needs attention** lists, most urgent first: orders nobody picked up in time, ready to serve, waiting for
  confirmation, cooking on paper, waiting for a chef, and **tables to bill** (one row per table). Each row says what
  happens next and shows the button for it (Confirm, Served, Mark ready, print KOT, Bill).
- **Table cards** by area: **Free**, the current order status, or **Bill pending · ₹amount** (served but unpaid).
- Tap a table: its open orders, **Bill table**, and (for staff allowed to take orders) the order composer: pick
  items by category or search, quantities, a note, live total from the server, **Send to kitchen**.

### Orders tab (admins and billing staff)
- **Orders | Bills** switch.
- **Today / Yesterday / Pick dates** (up to 31 days, the hotel's own calendar), **search** (order # or table; bill #
  or table), 50 rows at a time with **Load more**.
- Orders: filters All / Open / Served / Cancelled; each row shows status, items, channel (QR / Server / Admin), time,
  amount, the next step, and **Open bill** if billed. **Tables to bill** at the top: one Bill button per table.
- Bills (the register): filters All / Unpaid / Paid / Cancelled; tap a bill to view, **Print again**, settle or void.
  Cancelled bills show why ("Items added: replaced by INV/...").
- Old data is never deleted (GST law: 72 months); it simply loads when you pick older dates.

## 7. Kitchen

- **Modes** (Settings): **Chefs pick up** (shared board, first to accept gets it), **Auto assign** (the system
  gives it to the least busy chef on shift), **Printed tickets** (staff print the KOT, the kitchen cooks from paper).
- **Kitchen screen**: orders up for grabs, my queue, other chefs, on/off shift switch. Only the first order in a
  chef's queue can start; the next unlocks when it is marked ready.
- **Orders per chef at once** (1 to 5) caps each queue.
- Admins can reassign or reorder with a reason (audited).
- Built for phones, tablets and a kitchen TV.

## 8. Menu

- Categories and items: name, price, tax rate, veg / non-veg / egg mark, preparation time, availability.
- **Sold out** switch (chefs and servers may use it): the item disappears from QR ordering instantly and cannot be
  ordered.
- Prices always come from the server; a diner's phone or a waiter's tablet never sets a price.

## 9. Tables and QR codes

- Add tables with a label (like T1), area (Indoor, Patio...) and seats; deactivate when not in use.
- Every table has its own QR code. **Print QR** for one table or all: a branded card with the hotel logo, name,
  one-line address, the QR, "Table T1 · Scan to order", and "Powered by". Prints on A4 (2 x 2 per sheet), a single
  large card, or a thermal roll; always in English.

## 10. Billing

- **One bill per table visit.** Tap **Bill** for a table (Floor, Orders, or the table window).
  - If an order is still being cooked, the app warns ("still being prepared") and lets you **bill anyway**.
  - If the guest orders more after the bill was printed but before paying, the bill window shows
    **"1 new order since this bill" → Update bill**: the old bill is cancelled (it stays in the register) and one new
    bill covers everything. A GST invoice is never edited.
  - After payment, the table is free and the next order starts a new visit.
- **Bill contents**: hotel details and GSTIN, bill type, number, table, date, items, subtotal, CGST and SGST per rate,
  service charge (if set), total, payment method once paid.
- **Settle**: Cash, UPI or Card. Paying frees the table and ends the guests' QR session.
- **Print / Print again**: every copy after the first is marked **DUPLICATE** and logged with who printed it.
- **Void** (permission `bill.void`, reason required): withdraws a bill; its orders can be billed again.
- Cancelling an order that is on an unpaid bill cancels that bill; on a paid bill it is refused.

## 11. Team and roles

- **Team**: add staff (name, email, phone, role, password), change role, disable or enable, **Reset password** for
  someone who forgot theirs (they are signed out everywhere). Only a hotel admin can reset a hotel admin's password.
- **Roles**: Hotel admin (always everything), Manager, Cashier, Server, Chef, plus custom roles (plan feature). A
  permission matrix shows exactly what each role may do:

| Permission | What it allows |
|---|---|
| dashboard.view | See the hotel Overview |
| order.view | See orders |
| order.create | Take orders for a table |
| order.confirm | Confirm QR orders before the kitchen sees them |
| order.serve | Mark ready orders as served |
| order.cancel | Cancel orders (reason required) |
| kitchen.cook | Pick up and cook orders |
| kitchen.manage | Reassign or reorder chef queues; receives no-pickup alerts |
| table.manage | Add tables and print QR codes |
| menu.manage | Edit the menu and prices |
| menu.availability | Mark items sold out or available |
| staff.manage | Add and disable staff, reset their passwords |
| role.manage | Configure what each role can do |
| expense.view / expense.manage | See / record expenses |
| report.view | See sales reports |
| settings.manage | Change outlet settings and subscription |
| kot.print | Print kitchen tickets (and hand orders to the kitchen on paper) |
| bill.manage | Create, print and settle bills |
| bill.void | Void an issued bill (reason required) |

## 12. Expenses

- Record expenses with date, category, amount, payee and note. Categories: the platform's global list plus the
  hotel's own. Month view with totals; the Overview shows revenue minus expenses for the month.

## 13. Overview and "My work"

- **Hotel Overview** (owners and managers): orders and revenue today, average ticket, open orders by status, tables
  busy, orders by hour, last 7 days, channels, top items, month revenue / expenses / net, average times (waiting for a
  chef, cooking, ready-to-table, total), each chef's completed orders and queue, each server's served orders and
  speed, live orders.
- **My work** (everyone else's Overview), today or last 7 days, with the team's average under each average:
  - Chef: orders and items cooked, average cooking time, average wait before starting, queue now.
  - Server: orders served, tables served, average ready-to-table time, orders taken and their value, ready now.
  - Billing staff: bills raised, collected by Cash/UPI/Card, unpaid bills now.

## 14. Settings (hotel admin)

| Tab | Setting | Effect |
|---|---|---|
| Kitchen | Restaurant name | Shown on screens, bills, QR cards |
| | How orders reach chefs | Chefs pick up / Auto assign / Printed tickets (plan-dependent) |
| | Orders per chef at once | 1 to 5 |
| | Alert if no chef picks up within | Off, 3, 5, 10, 15 or 30 minutes |
| | How food reaches the table | Waiter serves / Diner collects at counter |
| | Confirm QR orders first | A server approves table orders before the kitchen sees them |
| Branding | Logo, one-line address | Shown in the app, on bills and QR cards |
| Plan & billing | Current plan, limits, upgrade | Subscription |

Saving keeps the window open and confirms "Settings saved" (or shows the error) above it.

## 15. Diner (QR ordering)

1. Scan the table QR: the hotel's menu opens in the phone's browser (no app, no sign-up).
2. Browse by category, add items and notes, see the total, **Place order**.
3. A live tracker shows Placed → Confirmed → Cooking (with the chef's name) → Ready ("on its way", or in counter mode
   "please collect it from the counter").
4. More rounds can be ordered from the same phone; all go on the table's one bill.
5. After payment the QR session ends; the next guests start fresh.
Limits protect the kitchen: at most 5 open orders per table session, sold-out items refused, prices from the server.

## 16. Alerts and notifications

| Event | Who is alerted |
|---|---|
| New QR order waiting for confirmation | Staff who confirm orders |
| New or confirmed order on the chefs' board | Chefs on shift (Printed-tickets mode: staff who print KOTs) |
| Order assigned to a chef | That chef |
| Order ready | Servers |
| Order not picked up in time | Admins/managers (`kitchen.manage`) and chefs on shift, once per order |

- In the app: a loud sound plus an on-screen message. The first tap anywhere on a page allows sound (browser rule).
- On phones: a push notification with a loud sound, even with the app closed (once Firebase is configured).
- "Enable alerts" asks once per browser; the answer is remembered for 7 days. Profile menu: sound on/off, test sound.

## 17. Printing

| Document | When | Paper |
|---|---|---|
| KOT | Paper path, or reprint | 58/80 mm thermal or A4 |
| Bill | Print / Print again (DUPLICATE) | 58/80 mm thermal or A4 |
| Table QR cards | Tables screen | A4 sheet, single card, or roll |

Printer setup (profile menu): paper size and how to print: the device's print dialog, or straight to a network
thermal printer (installed app). Printed documents are always in English.

## 18. Language, devices, sign-in

- Screens in **English, Telugu, Hindi** (globe button); prints in English.
- Works on phones, tablets, computers and kitchen TVs (web today; Android/iOS builds from the same code).
- Sign-in with email and password. **Change password** in the profile menu (signs out your other devices).
  Sessions renew themselves; a stolen, reused session token signs that login out everywhere.

## 19. Plans and limits

Features by plan (gated on the server; the app hides what the plan does not include):

| Feature | Meaning |
|---|---|
| QR_ORDERING | Diners order by scanning the table QR |
| WAITER_ORDERING | Servers and admins take orders |
| CHEF_ACCEPT_MODE / AUTO_ROUTING / ADMIN_PRINT_MODE | The three kitchen modes |
| EXPENSES | Expenses module |
| CUSTOM_ROLES | Edit roles and permissions |
| REPORTS_ADVANCED, RATINGS, API_WEBHOOKS, ROOMS | Reserved for upcoming modules |

Limits per plan: outlets, tables, staff, chefs, menu items, orders per month. Reaching a limit shows a clear message;
nothing already created is removed.

## 20. Rules the app always enforces

- One hotel never sees another hotel's data (enforced in the database itself).
- Money is calculated only on the server, in paise; screens only display it.
- Prices come from the server; client-sent prices are ignored.
- Every order-item status change, bill, void, password change and settings change is audited.
- Bill numbers are sequential per financial year with no gaps or duplicates; issued bills are never edited.
- Permissions and plan limits are checked on the server, whatever the screen shows.

## 21. Change log

| Date | Change |
|---|---|
| 2026-11-20 | Guide created, covering everything up to: one bill per visit, Orders/Bills with dates and search, reprint, change/reset password, My work, workflow settings, no-pickup alerts, bill pending |
