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
| **Takeaway** | An order with no table (counter, parcel). Its order number is the token the guest collects with. |
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
| **Ready** | A server taps **Served** (takeaway: **Handed over**; counter mode: done automatically) | serve it to the table / hand it over |
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
- **Takeaway** button (staff who take orders): the same composer without a table, plus the guest's name and phone
  (both optional). The order number is the guest's token. Takeaway orders appear in Needs attention as
  "Takeaway #12 · Ramesh" with **Handed over** when ready and **Bill** until billed.
- **Hold** (next to Send to kitchen, on a table or a takeaway): saves the cart **without sending it to the kitchen**
  (the guest went to fetch their wallet, or is still deciding). Nothing is cooked, numbered or billed. The floor shows
  "1 held" on the table, or "Takeaway · 1 held". Open the table / Takeaway and tap the held chip ("Held 7:42 pm ·
  Ravi · ₹520", today's price) to put the dishes back in the cart, change anything, then **Send to kitchen**; the hold
  disappears. If a dish was removed or sold out since, the chip says **needs changes**. Two people cannot send the
  same held order twice: the second is told it was already sent. The cross on a chip discards it (after asking).
  Close the day warns about held orders that were never sent.

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
- **Target time**: when an order is placed, each dish gets a **due time** = now + the chef pick-up time (Settings,
  "no pickup alert after", 5 minutes by default) + that dish's prep time (Menu). The order is due when its slowest dish
  is. Each card shows **"Due in 4 min"** (clock icon), **"Due now"** (alarm icon) or **"Delayed 3 min"** (warning
  icon, bold red) next to the waiting / cooking time. With a station picked, the card uses only that station's
  dishes, so the bar sees when its chai is due. Changing a prep time later does not move orders already placed.
- Built for phones, tablets and a kitchen TV.

## 8. Menu

- **Kitchen stations** (Menu > Stations): add stations such as Bar, Tandoor or Chinese. Give a category a station
  ("Drinks -> Bar") and, if needed, one dish a different one. Each dish shows where it goes; dishes without a station
  go to the main Kitchen. The kitchen ticket prints one slip per station (each with the order number and table), and
  each kitchen screen can show just its station. The order is still taken and marked ready by one chef.

- Categories and items: name, price, tax rate, veg / non-veg / egg mark, preparation time, availability.
- **Sold out** switch (chefs and servers may use it): the item disappears from QR ordering instantly and cannot be
  ordered.
- Prices always come from the server; a diner's phone or a waiter's tablet never sets a price.
- **Sizes** (Menu > item > Sizes): give a dish two or more sizes with their own price, e.g. Half ₹180 / Full ₹320.
  Then everyone ordering it (waiter, admin, QR diner) must pick a size. One size can be marked sold out on its own
  (half biryani finished, full still on); if every size is sold out the dish shows as sold out.
- **Add-ons** (Menu > Add-on groups): make a group once, e.g. "Extras" (Raita ₹30, Cheese ₹40; pick up to 3) or
  "Choose your bread" (Roti / Naan; pick exactly 1), then attach it to the dishes that offer it. Each group shows
  which dishes use it. The same add-on can be picked more than once (2 x Cheese) if the group allows that many.
- When ordering, the size and add-ons are chosen on the same sheet. The price shown is the server's: size plus
  add-ons. Add-ons are taxed at the dish's GST rate. The kitchen ticket prints the add-ons under the dish; the bill
  line reads "Biryani (Half) + Raita, 2 x Cheese".
- Changing a price later never changes an order already placed or its bill.
- **Combos** (Menu > item > ⋮ > Combo): turn a dish into a combo by listing what it contains, e.g. "Thali ₹300" =
  1 Biryani + 2 Naan. It keeps its own price and tax. For a choice ("choose your drink"), attach an add-on group
  with ₹0 options (and a surcharge for the pricier ones), set to "choose 1". The kitchen ticket lists every dish in
  the combo with its quantity. A combo can't be ordered while one of its dishes is sold out (diners see it sold out).
  A dish used in a combo can't be deleted until it is taken out of the combo.
- **Serving hours** (Menu > category > Edit): give a category its hours and days, e.g. Breakfast 7:00-11:00 every
  day (a window can run past midnight). Outside them, the QR menu shows the section with "Served 7:00-11:00" and its
  dishes can't be ordered. Staff get a warning ("Breakfast is over: order anyway?") and can still send the order for a
  guest who arrived late. No hours = served all day.
- **Happy hour** (Menu > Happy hour): a name, percent off, the days, a start and end time (it can run past
  midnight, e.g. Saturday 10 pm to 1 am) and which categories (or the whole menu). While it runs, the dishes show a
  "Happy hour −20%" badge for staff and on the QR menu, and the price is lowered automatically for every order,
  whoever takes it. It takes the percent off the dish or size price; add-ons keep their price. If two offers overlap,
  the guest gets the better one (they never add up). The bill line reads "Naan (Happy hour)", and the item report
  shows happy-hour sales as their own row. Switch an offer off without deleting it; deleting it never changes past
  orders.

### Table visits (Floor)
- **Seat** a table when guests sit down, with how many they are (you can change it later). The table shows
  **Seated** and a timer from that moment. If nobody seats it, the first order (waiter or QR) starts the timer.
- After ordering it shows Occupied, then Bill pending. When the last bill is paid the table turns **Cleaning**;
  tap **Clean** when it is ready and it becomes Free.
- **Reserved**: hold a free table with a short note ("8 pm, 4 people"). Seating guests or ordering clears it.
- Guests who leave without ordering: tap **Free** to end the visit.
- **Move**: the guests changed tables. Pick a free table: their orders (wherever they are in the kitchen), guest
  count and timer go with them, and QR diners keep ordering on the new table. The old table turns Cleaning.
- **Merge**: one party across several tables. Their orders and guests join this table, so they get one bill (split
  it by guest at the end if they like). Both are only possible before the bill is made.
- The number of guests is kept on the bill once per visit (also when the bill is split by guest), so Reports show
  **covers** and the **average spend per guest** correctly.

### Bookings and waitlist (Floor > Bookings)
- **Book**: name, phone, guests, date and time, and a table if you want to keep one, plus a note. If that table has
  another booking within an hour and a half, the app warns first. Tables show "Booked 8:00 pm · Sharma" from two
  hours before.
- **Today**: every booking for the day by time. When the party arrives, tap **Seat**: the table's visit starts with
  their guest count. Mark **No-show** or **Cancelled** otherwise. A new booking from a phone number that didn't turn
  up before shows "1 no-show before".
- **Waitlist** for walk-ins: name, phone, guests and the wait you told them. Each party shows how long it has
  waited; today's real average wait is shown so the next quote is honest. Tap **Seat** and pick a table when one is
  free.

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
- **Takeaway orders are billed one order at a time** (usually paid at the counter when ordering or at pickup), with
  the same warning if the food is not handed over yet. The bill says "Takeaway" and the guest's name.
- **Bill contents**: hotel details and GSTIN, bill type, number, table, date, items, subtotal, CGST and SGST per rate,
  service charge (if set), total, payment method once paid.
- **Company GST bill** (B2B; GST-registered hotels only, on an unpaid bill): type the company's name, GSTIN and
  address (optional). The app checks the GSTIN (a mistyped one is refused) and re-issues the bill with "Bill to:
  company, GSTIN" printed on it; the old number stays in the register as cancelled. More food later keeps the company
  on the new bill. A company bill cannot be split by items. GSTINs from **another state are refused for now** (those
  bills may need IGST; to be confirmed with your CA). In Reports > GST, company bills are listed one per line (for
  the B2B part of GSTR-1) and the rest is B2C.
- **Discount** (in the bill window, before or after printing): percent or amount, with a reason. Each role has a
  maximum (owner: any; Manager 20%, Cashier 10%, others none, until the owner changes them in Team > Roles). Tax is
  recalculated on the discounted amount, as GST requires. The bill shows Subtotal, Discount (and reason), Taxable
  value, CGST/SGST, Total. Discounting an already printed bill cancels it and issues a new one.
- **Pay in parts**: enter an amount and method (e.g. ₹500 Cash, then the rest by UPI). The bill shows Paid and Due;
  when nothing is due it is paid ("Mixed" if several methods). A part entered by mistake can be removed until the bill
  is paid. **Split equally** shows each guest's share (2 to 20 guests), and each pays theirs. A bill with money taken
  cannot be voided until those parts are removed; if the guest orders more, the money already paid moves to the new
  bill.
- **Split by items** (Floor or Orders > table > Bill > Split by items): choose how many guests, then for each dish
  tap who had it: give units to guests (2 Naan: one each) or mark it **Shared** by some guests (its price is shared
  equally, to the paisa). Every dish must be given out and every guest must get something. Each guest then gets a GST
  bill of their own with its own number and its own tax, printed "Guest 2 of 3". If the table already had one bill,
  it is cancelled and replaced (not if money was already taken on it, and not if it has a discount: cancel that bill
  first; guest bills cannot be discounted yet). Each guest pays their own bill; the table is
  free only when every guest has paid. Food ordered after splitting gets its own bill. To redo a split, cancel any
  guest bill: the whole split is cancelled (only while nobody has paid).
- **Settle**: Cash, UPI or Card for whatever is still due. Paying frees the table and ends the guests' QR session.
- **Print / Print again**: every copy after the first is marked **DUPLICATE** and logged with who printed it.
- **Void** (permission `bill.void`, reason required): withdraws a bill; its orders can be billed again.
- Cancelling an order that is on an unpaid bill cancels that bill; on a paid bill it is refused.

## 11. Team and roles

- **Team**: add staff (name, email, phone, role, password), change role, disable or enable, **Reset password** for
  someone who forgot theirs (they are signed out everywhere). Only a hotel admin can reset a hotel admin's password.
- **Roles**: Hotel admin (always everything), Manager, Cashier, Server, Chef, plus custom roles (plan feature). A
  permission matrix shows exactly what each role may do, plus each role's **maximum bill discount**:

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

- **Hotel Overview** (owners and managers): orders today, revenue today (issued bills, not cancelled: the same
  number the Reports show) with the change against **the same weekday last week up to the same time** (an up or
  down arrow and the amount and percentage; "No sales to compare" when there were none then), **money received today
  by cash / UPI / card** (the same figures as Reports and Close the day), average bill, food served but not billed yet (a warning), open orders by status, tables
  busy, orders by hour, last 7 days, channels, top items, month income before GST / expenses / net (the same rule as the Profit & loss report), average times (waiting for a
  chef, cooking, ready-to-table, total), each chef's completed orders and queue, each server's served orders and
  speed, live orders, and **On time today: 92% (23 of 25 orders ready by their due time)** under Service times.
- **My work** (everyone else's Overview), today or last 7 days, with the team's average under each average:
  - Chef: orders and items cooked, average cooking time, average wait before starting, queue now.
  - Server: orders served, tables served, average ready-to-table time, orders taken and their value, ready now.
  - Billing staff: bills raised, money they received by Cash/UPI/Card (part payments count for whoever took them),
    unpaid bills now.

### Close the day (cash drawer)

Who: owner, manager, cashier (permission "Count the cash drawer and close the day"). Open **Close day** from the
Overview at the end of the hotel day.
- The screen shows what the drawer should hold, worked out by the server from everything recorded on every phone
  and tablet: opening float + cash received (part payments included) - cash expenses paid from the drawer. UPI and
  card received are shown too, and a warning if bills are still unpaid.
- Count the cash and type the total, and how much stays in the drawer for tomorrow (the rest goes to the bank).
  The difference is shown at once as **Over**, **Short** or **Exact**. Any difference needs a short reason.
- Saving is final. Tomorrow's opening float is the cash you kept, so it can't be changed later. If cash is taken
  on that day after closing, the close shows it as "cash after close".
- **History** lists every close with the over/short and how many days were short in the period, so a pattern of
  small shortfalls stands out.

### Reports (owners and managers: permission "See sales reports")

Open **Reports** from the Overview. Pick a day or a date range (up to 92 days; days are the hotel's own calendar
days). Every report says at the top what it counts. Every report has **Download CSV** (opens in Excel, amounts in
rupees).

- **Sales by day**: one row per day and a total row. Bills, then the money ladder: Gross (menu value), minus
  Discounts, = Taxable, plus CGST + SGST and service charge, = Total. Then Paid and Unpaid (bills issued but not
  paid yet), and Cancelled bills with their value, never mixed into sales. A bill replaced because the table ordered
  more is counted once (the new bill), and shown only as a "replaced" count. **Money received** shows Cash, UPI and
  Card actually taken that day (part payments included), so the cash drawer can be checked against it.
  **Not billed yet**: food ordered in the period that is on no bill (including food whose bill was cancelled). It is a
  warning to act on, not sales.
- **Items**: each dish as printed on the bill: quantity sold, gross, discount, taxable. Biggest seller first.
  Above it, **By category** (Starters, Mains, Beverages...): dishes, quantity, gross, discount, taxable and share of
  sales; the categories add up to the item totals. A dish counts under the category it had **when it was billed**:
  moving it to another category later does not change past reports. Older bills that could not be matched show as
  "Not recorded". Download it as CSV like the other reports.
  Cancelled bills are left out.
- **PDF** (next to Download CSV, every report tab): an A4 PDF of what is on screen, with the hotel, report, dates,
  time zone, "what it counts" line and when it was made on top, and "Page 1 of 3" at the bottom. It opens the device's
  print / save / share sheet. Printed in English, like bills.
- **Profit & loss** (tab; Last 6 / Last 12 months): one card per month, newest first, and a total. **Sales before
  GST** (issued bills after discounts) + service charge − **expenses** (by category, from the Expenses screen, on the
  day spent) = **operating profit** (or loss) and its % of sales. **GST collected** is shown separately as owed to
  the government, never as income. The current month says **So far**. Food cost is only what was entered as expenses
  until stock is tracked. Download CSV gives one column per month.
- **GST summary**: taxable value, CGST and SGST per GST rate for the period (intra-state), SAC 996331, company (B2B) bills one per line, and
  the documents issued (first and last invoice number, how many, how many cancelled) as GSTR-1 asks. The tax per rate
  is exactly what was printed on each bill, so it always matches the bills.
- **Staff** (Pro plan): for each person: bills raised and their value, discounts given, money received by method,
  bills they cancelled by hand.
- **Bills register** (CSV only): every bill in the period, cancelled ones included, with date, time, table or
  takeaway, status, the full ladder, amount paid, method and the cancel or discount reason. This is the file to send
  to the accountant.

What happens: reports are read-only and computed on the server from the bills; nothing is stored or changed.
Servers and chefs cannot open them.

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
| KOT | Paper path, or reprint (says TAKEAWAY and the name for takeaway) | 58/80 mm thermal or A4 |
| Bill | Print / Print again (DUPLICATE) | 58/80 mm thermal or A4 |
| Table QR cards | Tables screen | A4 sheet, single card, or roll |

Printer setup (profile menu): paper size and how to print: the device's print dialog, or straight to a network
thermal printer (installed app). Printed documents are always in English.

## 18. Language, devices, sign-in

- **Keyboard shortcuts** (counter PC with a keyboard; press **?** anywhere on the Floor, in an order or a bill for
  the list):
  | Key | Does |
  |---|---|
  | `/` | Search the menu |
  | Enter (in the search) | Add the top dish shown; the search clears for the next one |
  | Ctrl + Enter | Send to kitchen |
  | Alt + H | Hold the order |
  | T | New takeaway (Floor) |
  | Alt + P | Print the bill (bill window) |
  | Alt + C / Alt + U / Alt + K | Pay what is due by cash / UPI / card (bill window) |
  Single keys (`/`, T, ?) do nothing while you are typing in a box; Alt and Ctrl keys always work. No F-keys and no
  Ctrl + P, so the browser never reloads or opens its own print dialog mid-bill.

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
| 2026-11-20 | Reports as PDF (section 13) |
| 2026-11-20 | Keyboard shortcuts for a counter PC (section 18) |
| 2026-11-20 | Profit & loss by month; Overview month net now excludes GST (sections 13) |
| 2026-11-20 | Company GST (B2B) bills; B2B list in the GST report (sections 10, 13) |
| 2026-11-20 | Target time per dish, "Delayed" on the kitchen screen, on time % (sections 7, 13) |
| 2026-11-20 | Hold / resume an order (section 6, 13) |
| 2026-11-20 | Reports: sales by category (section 13) |
| 2026-11-20 | Overview: vs same day last week and payment mix (section 13) |
| 2026-11-20 | Kitchen stations: a ticket and a screen per station (sections 7, 8) |
| 2026-11-20 | Bookings and walk-in waitlist (section 6) |
| 2026-11-20 | Move and merge tables (section 6) |
| 2026-11-20 | Table visits: seat with guests, timer, Cleaning / Reserved; covers in Reports (section 6) |
| 2026-11-20 | Close the day: cash drawer count with over/short and history (section 13) |
| 2026-11-20 | Serving hours for categories (breakfast / lunch / dinner) (section 8) |
| 2026-11-20 | Combos (section 8) |
| 2026-11-20 | Happy hour (section 8) |
| 2026-11-20 | Menu sizes (half/full) and add-ons (section 8) |
| 2026-11-20 | Split by items: one GST bill per guest (section 10) |
| 2026-11-20 | Reports: sales by day, items, GST summary, staff, bills register, CSV; Overview revenue now counts bills (section 13) |
| 2026-11-20 | Part payments and equal split (sections 10, 13) |
| 2026-11-20 | Discounts with a per-role maximum (sections 10, 11) |
| 2026-11-20 | Takeaway / counter orders (section 6, 10, 17) |
| 2026-11-20 | Guide created, covering everything up to: one bill per visit, Orders/Bills with dates and search, reprint, change/reset password, My work, workflow settings, no-pickup alerts, bill pending |
