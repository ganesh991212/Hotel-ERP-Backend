# 14. Prior art: how other platforms do it, their mistakes, and our choice

Rule 12 (CLAUDE.md): before building any feature or change, research how existing restaurant platforms design it,
how it fits our app, what mistakes they made, and how we avoid them. One section per feature, written **before** the
code. Sources are vendor material and user forums unless stated; vendor claims are labelled as such.

---

## Reports (checklist item 7) - 2026-11-20

### How others do it
- **Petpooja** (India): a day-end report with sales by item and by payment mode, plus discounts, taxes and expenses
  for the day on one screen; cash received vs sales; item-wise tax collected; hourly sales; downloadable reports;
  multi-outlet dashboard. Its GSTR-1 / 3B month-end helper is a separate Excel template, not part of the POS.
  (Vendor material.)
- **Square / Toast** (US): sales summary, item sales, discounts, payment types, staff/labour; "live" vs settled
  reports.

### Their mistakes (user reports)
1. **Dashboard and reports disagree** (Square community: unresolved for months). Causes: reports counting open
   checks vs closed only, different time windows (midnight-to-midnight default), device filters.
2. **Discounts that do not reconcile** (Toast): a 100%-discount order is left out of sales but its discount is still
   counted, so discounts look larger than sales allow; discount reporting differs by order source.
3. **Settlement lag** makes the same day show different totals at different times (Square).
4. GST filing needs rate-wise B2C totals and invoice-wise B2B; generic sales reports are "only partly usable"
   (Petpooja's own note), so owners re-key numbers into a spreadsheet.

### Our choice
- **One source of truth: issued bills** (and their lines and payments). The same definitions feed the Overview and
  every report, so they cannot disagree. Each report states its basis on screen and in the CSV.
- **Clear money ladder** on every report: Gross (menu value) - Discounts = Taxable value; + CGST + SGST + service
  charge = Bill total. A fully discounted bill stays in Gross with its discount, so the ladder always reconciles.
- **Status kept apart**: Paid, Unpaid (issued, not yet paid) and Cancelled (void) are separate columns, never mixed
  into one "sales" number. Unbilled food (served, no bill yet) is shown separately as a warning, not as sales.
- **Hotel's own calendar day** (its timezone, from the server), stated on the report. A configurable business-day
  cutoff (for places open past midnight) is a later setting.
- **Payments by the money actually received** (part payments by method), so cash in hand can be checked against the
  report; no settlement lag because we record payments at the counter.
- **GST summary built in**: rate-wise taxable value, CGST and SGST for the period (what GSTR-1 B2C needs), with SAC
  996331 (restaurant service). B2B invoices (customer GSTIN) come later with customer details.
- **CSV for every report**, same numbers as the screen, for the accountant.
- Reports: day-by-day sales, item-wise sales, GST summary, staff, payments. Category-wise needs the menu category on
  bill lines (not stored yet; added later).

## Split by items (checklist item 1, open) - 2026-11-20

### How others do it
- **Square**: select items on the check and move them to new checks; for a shared item choose a number of portions
  and it works out each person's cost.
- **Lightspeed**: seat-based; each item is put on the check for its seat automatically.
- **Aldelo**: assign items to a guest's bill, even after the order went to the kitchen.
- **Petpooja** (India): each sub-bill gets its own GST; tax is apportioned so the filing total does not change.
  (Vendor material.) Indian rule they cite: a tax invoice is optional below Rs 200 for an unregistered customer who
  does not ask for one, but must be issued on request.

### Their mistakes
1. Some systems **divide the original tax line** instead of recalculating tax per check, so per-check GST is wrong.
2. **Discounts do not follow the item** after a split.
3. Splitting after printing leaves the original bill alive, so the same food is billed twice.

### Our choice (built 2026-11-20; details in docs/03 "Split by items")
- Before (or instead of) the table bill, staff open **Split by items**: drag or tap items into Guest 1, Guest 2, ...
  A shared item can be split into N equal portions (the line value is shared to the paisa).
- Each guest's part becomes its **own GST bill with its own number**, tax recalculated per line on that bill
  (never divided), discounts applied per bill. The visit is paid when all its bills are paid.
- If a single table bill was already issued, splitting cancels it (it stays in the register) and issues the guest
  bills: the same food is never on two live bills.
- Chosen over seat-based ordering (Lightspeed): our waiters and QR diners do not record seats, so assigning items at
  the bill is the only split that works with the orders we have. A shared dish is split equally, not by custom
  percentages (rarely needed; Square's portion count is the same idea).
- Not yet: a discount per guest bill (Toast's "discount does not follow the item" mistake is avoided by not carrying
  a whole-bill discount into the split at all; a guest discount comes later).

## Menu variants (half/full) and add-ons (checklist item 5) - 2026-11-20

### How others do it
- **Petpooja** (India): "item-wise variations, and add-ons with separate pricing for each customisation"; menu setup
  is categories, variations, add-ons, recipes and tax mapping, kept the same across dine-in and delivery. (Vendor
  material; screens not public.)
- **Toast**: portions (e.g. 1st half / 2nd half) and modifier groups attached to items; groups can be required or
  optional, with min/max choices.
- **Square**: modifier sets attached to items, with required/optional and pre-selected options.
- Common pattern: a variation group (Half / Full, Small / Large) with its own price per option, plus reusable
  add-on groups ("Extras", "Choose your bread") with rules for how many may be picked.

### Their mistakes (user reports)
1. **Modifier rules that behave differently by device** (Square: optional modifiers forced as required on iOS) and
   pre-selected modifier bugs that silently add items.
2. **Can't see where a modifier is used** (Toast: modifier items "don't show which groups they're used in"), so
   owners can't safely change or delete them; large menus become unmanageable.
3. **Hidden from the till** when a visibility flag is off (Toast "Visible to POS"): staff can't sell what's on the menu.
4. **Online customers see a huge raw list of modifiers** (Square), because one list serves every channel.
5. **The same option can't be picked twice** in a group (Square), e.g. "extra cheese x2".
6. **Required-before-optional prompting order** forces extra taps on every order (Toast).

### GST (India)
- Food and drink served by a restaurant is one **composite supply**, taxed at the rate of the principal supply
  (restaurant service): add-ons served with the dish take the **dish's GST rate**, not a rate of their own. Things sold
  as separate goods (e.g. packaged goods, cigarettes) are outside this; we do not model them as add-ons.
  (Commentary sources; rates must still come from effective-dated tax config, rule 7.)

### Our choice
- **Variants** belong to one item (Half ₹180 / Full ₹320). If an item has variants, one must be chosen and its price
  replaces the item price. Each variant can be marked sold out on its own (half biryani out, full still on).
- **Add-on groups** are reusable across items ("Extras": raita ₹30, extra gravy ₹40) with `min`/`max` picks
  (0..n optional, 1..1 "choose one"). The menu screen shows **which items use each group** (avoids mistake 2).
  An add-on can be picked more than once only if the group allows it (`max` counts total picks; mistake 5).
- **One rule set everywhere**: the server validates variant and add-on choices for waiters, admins and QR diners
  alike (no per-device behaviour; mistake 1). Nothing is pre-selected.
- **Prices from the server only** (rule 4): clients send `variant_id` and add-on ids with counts; the server prices
  `unit = variant (or item) + add-ons`, and the add-ons take the item's GST rate (composite supply).
- **Snapshot on the order**: the order line keeps the variant name, each add-on's name and price at that moment, so
  later menu edits never change an order, a KOT or a bill. The bill line reads "Chicken Biryani (Half) + Raita" with
  the combined unit price; the KOT shows the variant in bold and add-ons underneath for the chef.
- **One list for staff and diners**, but sold-out variants/add-ons are hidden from diners and shown greyed for
  staff (no separate "visible to POS" flag to forget; mistake 3).
- Composer: pick the variant first (one tap, required), add-ons on the same sheet (mistake 6 avoided by showing
  both at once, not as successive prompts).

## Happy hour (checklist item 5) - 2026-11-20

### How others do it
- **Petpooja**: time-based pricing and time-based menus (set a window once; items appear/disappear on schedule).
  (Vendor material; steps not public.)
- **Toast**: either "time-specific pricing" on items/groups, or a separate happy-hour menu with menu-specific prices.
- **Heartland / Lavu**: pricing rules with a start and end time, targeting items by tag; the POS applies the discount
  in the window and reverts after.

### Their mistakes
1. **Online and third-party channels don't get the happy-hour price** (Toast's own support: partners "do not read
   time-based prices"; ChowNow always shows the base price; Toast Order & Pay users add the discount by hand).
2. **Reports hide it**: Toast's product mix averages the prices (100 beers at $10 + 50 at $5 shows $8.33), so the
   owner can't see what happy hour sold or cost.
3. **Staff must remember** to ring the happy-hour version of an item (Toast's own guidance), so errors are common.
4. **Copies leak**: a shallow-copied happy-hour menu group changes the original item's pricing (Toast).
5. **Sizes block it**: size pricing can't be combined with happy-hour pricing (Toast forum).
6. **Timed menu bugs**: ordering from a timed menu fails outside its window (Toast forum, "may not be a priority").

### Our choice
- A **price rule**, not a copy of the menu (avoids 4): name, percent off, days of the week, start and end time (may
  cross midnight, e.g. 22:00-01:00), and the categories it covers (none = whole menu). Owners switch it on/off.
- Applied **on the server, automatically, for every channel** (waiter, admin, QR diner) at the hotel's local time
  (avoids 1 and 3). If two rules overlap, the guest gets the single best one; rules never stack.
- Applies to the dish or size price (works with sizes; avoids 5); add-ons keep their price.
- **Recorded on the order line** (list price, rule name), and the bill line reads "Fresh Lime Soda (Happy hour)", so the
  item report shows happy-hour sales as their own row with real amounts, never an average (avoids 2).
- The price is decided when the order is placed: a cart quoted at 6:59 and sent at 7:01 gets the 7:01 price; the
  order shows exactly what was charged.
- A bill discount still works on top (it is a separate, audited decision with the role's limit).

## Combos (checklist item 5) - 2026-11-20

### How others do it
- **Petpooja**: a combo bills at one set price while each component stays linked to its own recipe, so stock comes
  off per component (vendor blog).
- **Square** (combos): the ticket and KDS show the combo's parts, each routed to its station, marked "part of Burger
  Combo"; the item-sales report lists the individual items sold in combos.
- **Lightspeed**: a base price sets the combo's minimum; chosen items can add a charge.
- **GoFrugal ServQuick**: combos cannot track inventory at all (disabled by default).

### Their mistakes
1. A bundle that reaches the kitchen as **one line** ("3-course prix fixe" shows as one ticket item; Square forum), so
   the cooks don't see what to make.
2. **Combos missing from accounting exports** (Square: not supported by QuickBooks/Xero/Zoho integrations).
3. **No stock tracking for combos** (GoFrugal), or a combo still sold when one of its parts has run out.
4. Choice slots ("pick your drink") built as a separate, special mechanism with its own rules to learn.

### Our choice
- A combo is an **ordinary menu item** (own price, category, GST code, sold-out switch) with a list of component
  dishes and quantities. It reuses everything items already have: sizes, add-on groups, happy hour.
- **Choices reuse add-on groups**: "Choose your drink" is a group with Coke ₹0 / Lime soda ₹0 / Lassi +₹20, min 1,
  max 1 (avoids 4).
- The **KOT prints every component** under the combo (avoids 1); the order line keeps a snapshot of the components.
- A combo **can't be ordered while any of its fixed components is sold out** (server check; avoids the second half of
  3); its preparation time is the slowest component's.
- Priced and taxed as one supply at the combo's own price and GST code (restaurant food, composite supply). The bill
  and reports show the combo as one line at its real price, so CSV exports carry it like any item (avoids 2).
  Per-component stock comes with inventory (checklist item 9), which will read the same snapshot.
- No combos inside combos (keeps the ticket and stock readable).

## Time-based menus (checklist item 5) - 2026-11-20

### How others do it
- **Petpooja**: time-based menus; set the window once and items appear/disappear on schedule (vendor material).
- **Toast**: menus with availability times. **Square**: menu availability for the POS, plus separate "timed
  categories" for online ordering.

### Their mistakes (forum reports)
1. **Works at the till, not online** (Square: menu availability "only works on our POS, not in our online stores";
   you must also set up timed categories). Two settings for one idea.
2. **Staff locked out with no override**: a Toast lunch menu (12-3 pm) can't be used for an order taken at 5 pm for
   tomorrow; support's fix was to change the menu's hours, place the order, then change them back.
3. **Delivery platforms ignore the times** ("most ordering platforms don't support time-specific menus"); sellers ask
   how to hide breakfast after 11 am on DoorDash.

### Our choice
- **Serving hours on the category** (Breakfast 07:00-11:00, chosen days; a window may cross midnight), one setting
  for every channel (avoids 1). No hours = served all day.
- **QR diners**: outside the hours the section is shown with "Served 07:00-11:00" and its dishes can't be ordered; the
  server refuses them (`outside_hours`).
- **Staff**: warn-and-allow, as with billing: the server answers `outside_hours` with the dishes, the app asks
  "Breakfast is over: order anyway?", and staff can send it (avoids 2). Diners can never override.
- Uses the same time logic as happy hour (hotel's local time, days bitmask, crossing midnight), so the two never
  disagree. Online platforms (checklist item 16) will receive the same hours when they are built (3).

## Day close / cash drawer (checklist item 8) - 2026-11-20

### How others do it
- **Petpooja**: the night audit / day-end report has a cash position: expected vs counted drawer cash, over or short;
  payments split by card, UPI, cash, online. Advice: count and sign over each drawer at handover, because "an
  unexplained shortfall discovered later has no clear owner" (vendor blog).
- **Toast**: after the count, the result is labelled cash over / short / no difference; above a threshold a manager
  must approve, and staff add a short comment (45 characters). A Z report alone does not close the day.
- Common formula: expected = opening float + cash sales - cash paid out; difference = counted - expected.

### Their mistakes (Square seller forums, mostly)
1. **Cash rung on another device is missing** from the drawer report (drawer tracked per device).
2. **Day boundary vs shifts**: a 24/7 shop's cash for a shift didn't match its sales until the close-of-day time was
   changed.
3. **The difference was hidden** from the close screen by an update (only visible later in history).
4. Cash refunds, reopened sales and tips handled differently in drawer vs sales reports, so owners stop trusting the
   drawer report.
5. **Small nightly shortfalls go unnoticed** when reconciling only monthly.

### Our choice
- **One drawer per hotel day, computed on the server** from what was actually recorded on every device: cash
  payments (`bill_payments`, part payments included) and cash expenses that day (avoids 1 and 4: the same numbers as
  Reports).
- **Hotel's calendar day**, stated on screen; cash taken after the day was closed is shown on that close as "cash
  after close" instead of silently changing it (helps 2).
- Close screen shows expected, counted and the **difference with "over" / "short" labels**, before and after saving
  (avoids 3). Any difference needs a note (Toast-style); the close is final and audited.
- **Opening float carries over**: tomorrow's opening float is the cash kept in the drawer at today's close, so it
  can't be edited to hide a gap. The rest is the bank deposit.
- **History with a running over/short total and a count of short days**, so a pattern of small shortfalls is visible
  (avoids 5).
- New permission **"Count cash and close the day"** (admin, manager, cashier by default; servers don't have it).
- Warns (doesn't block) when bills are still unpaid for the day.

## Tables: guests (covers), time seated, Reserved / Cleaning (checklist item 3) - 2026-11-20

### How others do it
- **Toast Tables**: statuses include Seated (set when a party is seated) and Dirty (set by "Mark as dirty" or
  automatically when the party leaves); the table tile shows the guest count and a timer. The timer counts how long
  the **check** has been open, not how long the guests have been seated.
- Table-management guides describe multi-state floors (available, reserved, seated, ordered, paying, dirty...).
- Petpooja / Square: table management is listed as a feature; no public detail on statuses (searches came back
  generic).

### Their mistakes
1. **Split checks copy the guest count to every check** (Toast forum; staff reply: no fix, adjust by hand), so covers
   and "average per cover" are inflated whenever a table splits.
2. **Timer measures the check, not the seating**: a party that sat 20 minutes before ordering looks quicker than it
   was.
3. Too many manual statuses for staff to keep up with on a busy floor (generic guides list up to 11).

### Our choice
- **Seat a table** with the number of guests (optional); the first order seats it automatically (QR diners too), so
  the timer starts when guests sit down or first order, whichever is first (avoids 2).
- **Guests are counted once per visit**: the bill keeps the visit's guest count; when a visit is split by items, only
  the first guest bill carries it (avoids 1). Reports show covers per day and the average spend per cover, computed
  on the server.
- **States stay few and mostly automatic** (avoids 3): Free -> Seated -> Ordering -> Bill pending -> **Cleaning**
  (set automatically when the last bill is paid) -> Free (one tap "Clean"). **Reserved** is the only manual state, with
  a short note ("8 pm, 4 people"); seating or ordering on it clears it.

## Move / merge tables (checklist item 3) - 2026-11-20

### How others do it
- **Lightspeed**: tables with ordered items can be transferred to another table or merged.
- **Papaya**: merging combines two tables into one bill; the merged table's unpaid items go to the destination
  bill; **blocked if a part payment exists** (move items instead).
- **Eats365 / eZee**: transfer the whole table, or individual KOTs / items, to another table; complimentary items
  can't be transferred (Eats365).
- **Dish / Odoo**: "move order" moves the table's open order to another table.
(Vendor help pages; no Petpooja or Toast specifics found.)

### Their mistakes / gaps
1. Merge silently fails or is blocked late when part of a bill is already paid; staff learn the rule at the till.
2. Three overlapping actions (move order, move KOT, move items) for what the waiter thinks of as "they changed
   tables".
3. Not covered in any of them: a **QR diner** whose phone still points at the old table keeps ordering to it after
   the move.

### Our choice
- Two actions only: **Move** (the whole visit goes to a free table) and **Merge** (other tables' visits join this
  one: one party, one bill). Moving single items between guests is already "split by items" at billing time.
- Everything moves together: open orders (wherever they are in the kitchen; screens and tickets show the new table),
  the guest count (summed on merge), the timer (earliest start kept) and the **QR diner sessions**, so diners keep
  ordering and tracking on the new table (avoids 3). The old table turns Cleaning.
- **Before billing only**: if a table already has an issued bill, the action is refused up front with a clear reason
  ("bill it again after moving" isn't needed: cancel or settle that bill first), so an invoice never changes table
  after it was issued and nothing paid is merged (avoids 1).
- Audited (who, from, to).

## Reservations and waitlist (checklist items 14 and 3) - 2026-11-20

### How others do it
- **Restroworks**: reservation management to "book more covers, cut no-shows" (vendor page).
- **Eat App / Tablein / NowBookIt / TouchBistro** (vendor blogs): bookings from several channels into one book;
  waitlist apps text the guest when the table is ready; confirmation prompts to reduce no-shows; some ask a deposit
  after repeated no-shows (e.g. three in six months).
- Indian platforms (Dineout, EazyDiner) and Toast Tables: no usable public detail found.

### Their mistakes
1. **Double bookings from channels that sync slowly**: two guests claim one table through different sources in the
   minutes before the book updates (Eat App).
2. **No-shows** cost the table and lengthen walk-in waits; repeat no-shows aren't visible to the host.
3. **Quoted waits that don't match reality** frustrate walk-ins (common complaint; no hard data found).
4. Reservations kept apart from the floor, so the host can't see that a table is booked in 30 minutes.

### Our choice
- One book inside the app (staff take phone and walk-in bookings; online channels later, writing into the same book),
  so there is nothing to sync (avoids 1). A booking on a table that overlaps another booking of that table (within
  the hotel's 90-minute slot) warns and needs a second tap.
- **Today's arrivals** list with time, name, phone, guests, table and note; the floor shows "Booked 8:00 pm" on a
  table with a booking in the next two hours (avoids 4). **Seat** turns the booking into a table visit with its guest
  count.
- **No-show** and **Cancelled** are recorded; when a new booking uses a phone number with earlier no-shows, the app
  shows "2 no-shows before" (avoids 2). Deposits come with online payments (item 16).
- **Waitlist** for walk-ins: name, phone, guests, quoted minutes; shows how long each party has waited, and today's
  **actual average wait** next to the quote field, so quotes follow reality (helps 3). Seat from the waitlist the
  same way. Text-when-ready comes with WhatsApp (item 16).
- Guest names and phones are hotel data: only staff with order access see them; they never appear in logs.

## Kitchen stations (checklist item 4) - 2026-11-20

Owner's decision: **split tickets and screens**; one chef still owns the order and marks it ready, so the strict
queue (rule 10) and assignment (rule 5) don't change. Full per-station item tracking stays deferred.

### How others do it
- **Toast**: each item is assigned prep stations with routing rules; a "preview menu item routing" screen shows where
  an item goes. **UrbanPiper, Bento, TableTrack**: a KOT per kitchen station / printer. **Odoo (Cerevantix)**: product
  categories mapped to stations; each station prints only its own items. **LS Central**: routing per item; an item
  with a route on any station becomes a "kitchen item".

### Their mistakes (Toast's own troubleshooting pages)
1. Items routed to the **wrong station** (routing rules hard to see).
2. The **same order prints twice** when an item is assigned to more than one prep station.
3. **Drinks still print in the kitchen**: printer lists per station drift from the item mapping.
4. Orders show on the screen but **don't print** when a station has no printer set.

### Our choice
- **One station per dish**: the category sets it ("Beverages -> Bar"), a dish may override it; never two (avoids 2).
  Dishes with no station go to the main **Kitchen**, so nothing is ever lost (avoids 4).
- The **menu shows each dish's station** next to it, so routing is visible where it is set (avoids 1 and 3).
- The order line keeps the station it was sent to (snapshot), so changing the menu later doesn't move tickets.
- **KOT**: one ticket per station, each listing only that station's dishes, with the table and order number on
  every ticket; the full ticket is still available.
- **Kitchen screen**: a station picker ("All", "Kitchen", "Bar"...) shows each device only its station's dishes;
  remembered on that device. The order is still accepted and marked ready by its chef.

## Live dashboard: vs same day last week, payment mix (checklist item 6) - 2026-11-20

### How others do it
- **Square** (Restaurants daily sales): "week over week" is the change against the **same day last week**; when that
  day has no data it shows **no change at all** rather than a number.
  (https://squareup.com/help/us/en/article/8579-review-daily-sales-for-your-restaurant)
- **Toast**: hour-by-hour sales against the same day last week, for daypart staffing.
  (https://pos.toasttab.com/th-us/blog/on-the-line/restaurant-pos-reports)
- **Petpooja**: a daily revenue tracker records how guests paid (cash / UPI / card) beside covers; it warns that one
  day is a weak baseline (weather, a local holiday) and that the comparison that means most is your own pattern.
  (https://blog.petpooja.com/operations-workflows/important-restaurant-analytics-you-must-know/,
  https://blog.petpooja.com/templates-checklist/restaurant-daily-revenue-tracker-template/)
- **Atlas** (sales trends): compares windows that don't line up (a whole earlier period against a partial current one).
  (https://help.atlas.kitchen/reports/sales/sales-trends-beta)

### Their mistakes
1. **Partial day against a whole day**: at 2 pm "today" is compared with all of last Tuesday, so every morning looks
   like a collapse.
2. **"-100%" or "+inf%"** when last week had no sales (a new outlet, a holiday closure).
3. Payment mix computed from **bill totals** instead of money received, so part-paid and unpaid bills distort it, and
   the dashboard disagrees with the cash drawer.
4. Hour-level comparison of a single day is noise presented as signal.

### Our choice
- Compare **today so far** with the **same weekday last week up to the same hotel-local time** (avoids 1). Revenue uses
  the same bill definition as Reports and the Overview revenue tile.
- No sales last week up to that time -> the server sends `null` and the app says "No sales to compare", never a
  percentage (avoids 2). The server computes the change; the app only shows it, with an up/down icon and words, not
  colour alone.
- Payment mix = **money received today by method** (`bill_payments`), the same definition as Reports and Day close,
  so the Overview, the reports and the drawer agree (avoids 3).
- No hour-by-hour comparison; the 7-day chart already shows the pattern (avoids 4).

## Category-wise sales (checklist item 7) - 2026-11-20

### How others do it
- **Petpooja**: category-wise sales built from the same billing data as its other sales reports
  (https://blog.petpooja.com/glossary/item-category/, https://www.petpooja.com/reporting).
- **Toast**: "sales categories" are set in the menu builder, separate from menu groups; a change applies **only to future
  sales**, and support can back-fill at most two weeks
  (https://support.toasttab.com/en/article/Sales-Categories-1492812477858?lang=en_US).
- **Square**: past sales keep the category they had; owners who fix categories still see the old ones in earlier
  reports, and deleted categories linger there
  (https://www.sellercommunity.com/t5/Questions-How-To/Updating-Categorie-Do-Reports-update-with-the-changes-made/m-p/5447).
- **Lightspeed (ShopKeep)**: a sales-by-category report with the same totals as the item report
  (https://shopkeep-support.lightspeedhq.com/support/reporting/sales-by-category-report).

### Their mistakes
1. Owners are **surprised** that a category fix does not change old reports, and nothing on screen says why.
2. Category totals that **don't add up** to the item report or the sales report (different bases: ordered vs billed,
   before vs after discount).
3. Back-filling history from today's menu silently **rewrites** what was sold under which heading.

### Our choice
- The category is **snapshotted** on the order line when ordered and copied to the bill line, like the name and the
  price: a bill never changes later. The report says so in its basis line (avoids 1).
- Category rows are computed from the **same bill lines** as the item report (issued, not cancelled; gross, discount,
  taxable), so categories sum exactly to the item totals (avoids 2).
- Bills issued before this change have no category; they show as **"Not recorded"** rather than being guessed from
  today's menu, except where the order line's own dish could be matched exactly (avoids 3).
- One table under the item report, plus a CSV; no separate screen.

## Hold / resume an order (checklist item 1) - 2026-11-20

### How others do it
- **PlumPOS** (glossary): "hold" saves an order **without sending it to the kitchen or charging it**, found again by
  table, name or ticket number, then edited and sent (https://plumpos.com/glossary/hold.html).
- **DealPOS** (restaurant): "park" an order aside while other guests are served, then retrieve it; parked items can be
  merged into one invoice (https://support.dealpos.com/en/articles/4773801-park-and-retrieve-for-restaurant).
- **Odoo POS** add-on "hold and resume": held carts listed on the till, resumed with one tap
  (https://apps.odoo.com/apps/modules/17.0/sp_pos_hold_and_resume).
- **LOGIC ERP / retail POS**: "park tickets" stored **locally on the terminal** for speed
  (https://www.logicerp.com/retail-pos-software/park-tickets-hold-sales-in-pos).
- **RetailOps**: holds for an order waiting on the customer (https://help.retailops.com/hc/en-us/articles/212149843-Put-Orders-on-Hold).
- Toast's "hold" is a different thing (course firing: send the starter now, hold the mains); not this item.

### Their mistakes
1. Held tickets kept **on one terminal**: lost when the device resets, invisible from the other till or a phone.
2. A held cart resumed hours later at **yesterday's price** (or with a dish that has since sold out).
3. **Two people resume the same hold** and the kitchen gets the order twice.
4. Holds **forgotten** at closing: the guest never got the food, nobody notices.

### Our choice
- A hold is a **cart saved on the server** (dish ids, sizes, add-ons, quantities, note, optional table or the
  takeaway guest's name). It is not an order: no kitchen ticket, no number, no bill (avoids 1).
- Prices are **never stored**: the list shows today's price, and resuming re-quotes; a dish that is gone or sold out is
  flagged on the hold so staff see it before sending (avoids 2).
- Sending the resumed order **consumes the hold in the same transaction**; if another device already sent it the
  order is refused with "already sent" (avoids 3).
- Close the day shows **how many holds are still waiting**, and the floor shows a "Held" count per table / takeaway
  (avoids 4). Discarding a hold is audited.
- Scope: one hold per "Hold" tap; tables and takeaway alike. Course firing stays out.

## Target time per dish and "Delayed" (checklist item 4) - 2026-11-20

### How others do it
- **Toast**: time thresholds per kitchen; a ticket changes colour the longer it is unfulfilled
  (https://support.toasttab.com/en/article/Basic-Kitchen-Configuration?lang=en_US).
- **MenuTiger / NextOrder-style KDS**: each product has a prep time; with several items the ticket counts down from
  the **slowest** item (https://menutiger.helpscoutdocs.com/article/383-how-do-i-change-the-appearance-and-preparation-times-for-my-kitchen-display-system-kds,
  https://nextorder.com/en-nz/products/kitchen-display-screen).
- **Sapaad**: flags a delay per item when it runs past its preparation time
  (https://kb.sapaad.com/help/how-do-i-know-when-there-is-a-delay-in-the-preparation-of-an-item).
- **GoTab, Epson TrueOrder, Bank of America KDS**: two thresholds (caution then late), often per order type, shown as
  blue/yellow/red (https://success.gotab.io/knowledge/kds-ticket-timer,
  https://files.support.epson.com/pdf/pos/kds/111-56-qum-013_r3.36_trueorder_kds_order_timers-quick_user_manual.pdf,
  https://merchanthelp.bankofamerica.com/Kitchen-Display-Order-Management).
- A KDS vendor blog: set thresholds to the kitchen's **real** targets, or every ticket looks late
  (https://crumb-pos.vercel.app/blog/kds-best-practices).

### Their mistakes
1. **Colour alone** (yellow/red) says "late": unreadable for colour-blind cooks and on washed-out kitchen TVs.
2. **One threshold for everything**: chai is held to the biryani's 20 minutes, or the biryani to the chai's 4.
3. The clock starts when the **cook starts**, so time spent waiting for a chef never counts, while the guest has been
   waiting since they ordered.
4. Lateness is shown on the screen but **never measured**, so nobody knows whether the kitchen is improving.
5. Changing a dish's prep time later moves the goalposts for orders already cooking.

### Our choice
- Each dish already has its prep time. When an order is placed, the server fixes a **due time** for every line:
  placed + the outlet's chef pick-up time + that dish's prep time; the order is due when its slowest line is (avoids
  2, 3). It is a snapshot, so later menu edits don't change it (avoids 5).
- The kitchen card says **"Due in 4 min"**, **"Due now"** or **"Delayed 3 min"** with a clock / warning icon as well
  as colour (avoids 1). With a station selected, the card uses that station's dishes only (the bar's chai is due long
  before the kitchen's biryani).
- The Overview reports **on time today**: orders ready by their due time, out of all orders made ready (avoids 4).
- No per-order-type thresholds yet: one rule, visible and explained in the guide.

## B2B invoices: the buyer's GSTIN on the bill (checklist item 8) - 2026-11-20

### How others do it
- **Petpooja**: a bill issued to a customer's GSTIN is B2B; dine-in and takeaway bills are B2C. A guest asking for a
  GSTIN is flagged on the bill, not billed as a normal table
  (https://blog.petpooja.com/finance-compliance/gst-billing-for-restaurants/,
  https://blog.petpooja.com/glossary/gstr-1/). Petpooja also notes most restaurant bills do not need e-invoicing
  (https://blog.petpooja.com/finance-compliance/e-invoice-for-restaurants-most-bills-do-not-need/).
- **GSTR-1**: each B2B invoice is reported on its own line: number, date, buyer GSTIN, taxable value, tax
  (https://docs.frappe.io/erpnext/gstr-1-missing-or-mismatch, https://www.patronaccounting.com/blog/restaurant-gst-return-documents-required-checklist).
- Other sources: https://restro-logic.pages.dev/blog/restaurant-gst-reporting-india.html,
  https://xiotch.web.app/blog/gst-billing-guide-restaurants-india/, https://cdn.gofrugal.com/blog/billing-vs-invoice/
  (vendor marketing; claims unverified).

### Their mistakes
1. **Invalid GSTINs** typed by hand go onto invoices; the buyer cannot claim credit and the hotel must amend next
   month (https://www.gimbooks.com/blog/10-common-gst-invoice-errors/amp/). Since July 2025 GSTR-3B is filled from
   GSTR-1 and locked, so the error is locked in too.
2. **B2B mixed into B2C** totals: GSTR-1 mismatches and notices (https://vasyerp.com/the-retail-guru/?p=62134).
3. A **wrong place of supply**: an other-state company billed CGST + SGST where IGST may apply.
4. The **recipient edited on an issued invoice**, or lost when the bill is re-issued for extra items.

### Our choice
- The buyer (company name, GSTIN, address) is added when billing, or to an **unpaid** bill by re-issuing it (the old
  number is cancelled and kept, like a discount); an issued invoice is never edited (avoids 4). Re-billing for more
  items **keeps** the buyer; a split refuses rather than drop it (avoids 4).
- The GSTIN is checked on the server (format and check digit); a mistyped one is refused (avoids 1).
- Only a **GST-registered** hotel (tax invoice) can issue B2B bills.
- **Other-state GSTINs are refused for now** ("inter-state B2B needs IGST"), so we never print CGST + SGST where IGST may
  be due (avoids 3). [Likely] Under section 12(2) of the IGST Act the place of supply for a registered buyer is the
  buyer's location, which would make those bills IGST. To be confirmed with the owner's CA before we support it.
- The GST report lists **B2B invoices one per line** (invoice, date, buyer GSTIN, name, taxable, rate, CGST, SGST)
  and the B2C totals separately; the CSV has both (avoids 2).
- No e-invoicing (IRN) yet: it applies only above the turnover threshold.

## Profit and loss by month (checklist item 8) - 2026-11-20

### How others do it
- **Petpooja**: the POS fills in net sales, discounts, taxes and food cost; the owner adds fixed costs (rent,
  salaries, utilities); prime cost = food + labour as one figure
  (https://blog.petpooja.com/finance-compliance/read-restaurant-profit-and-loss-from-pos/).
- **Patron Accounting (India)**: the P&L starts from **net sales excluding GST**, then COGS, labour, rent, utilities and
  aggregator commission, each on its own line (https://www.patronaccounting.com/blog/restaurant-profit-and-loss-statement).
- **Benchmarks**: COGS 28-35%, labour 25-35%, prime cost 55-65%
  (https://www.katalystos.com/blog/how-to-read-a-restaurant-p-and-l-statement,
  https://equimise-marketing.pages.dev/blog/reading-your-pl).
- **DoorDash / 7shifts / Supy / OysterLink guides**: same structure; review more often than monthly
  (https://merchants.doordash.com/en-au/blog/restaurant-profit-and-loss-statement,
  https://www.7shifts.com/blog/restaurant-profit-and-loss-statement/, https://supy.io/blog/how-to-read-a-restaurant-p-and-l,
  https://oysterlink.com/spotlight/pl-for-restaurant-example).

### Their mistakes (and one of ours)
1. **GST counted as revenue**: the tax collected for the government inflates sales and profit by the GST rate.
   *Our own Overview did this*: "net this month" was bill totals (with GST) minus expenses.
2. A **part month** compared with full months as if it were complete.
3. Profit shown with **no food cost** and no warning, so it looks far better than it is.
4. Numbers that do not match the sales report because the P&L uses another definition of a sale.

### Our choice
- Sales = **taxable value** of issued, not-cancelled bills (gross minus discounts, **before GST**), the same bills as
  Reports; service charge is income on its own line; GST collected is shown below as "collected for the government",
  never as income (avoids 1, 4). The Overview's month net now uses the same rule.
- Expenses by category from the Expenses screen, on the date they were spent. Operating profit = sales + service
  charge - expenses; margin % from the server.
- One column per month (up to 12); the current month is marked **"so far"** (avoids 2).
- Until inventory exists, the P&L says **food cost is only what was entered as expenses** (avoids 3); prime cost and
  COGS % come with inventory (parked).
- CSV download like the other reports.

## Keyboard shortcuts for a counter PC (checklist item 1) - 2026-11-20

### How others do it
- **Petpooja**: a keyboard billing view; item **short codes** typed at the counter instead of tapping
  (https://www.petpooja.com/billing, https://blog.petpooja.com/glossary/quick-billing-shortcut/,
  https://amd.petpooja.com/restaurant-billing-software). Its published pages do not list default keys.
- **Odoo POS add-ons**: F1 product screen, F2 payment screen, keys for quantity and discount
  (https://apps.odoo.com/apps/modules/17.0/sh_pos_keyboard_shortcut, https://apps.odoo.com/apps/modules/17.0/nf_pos_keyboard_shortcuts,
  https://webkul.com/blog/odoo-pos-keyboard-shortcuts/).
- **Paloma365, NamaSoft, WCPOS**: F-key maps, often user-configurable
  (https://dashboard.palomapos.com/docs/en/pos-classic/hotkeys, https://docs.namasoft.com/en/platform/shortcuts.html,
  https://docs.wcpos.com/settings/store/hotkeys.md).

### Their mistakes
1. **F-keys and Ctrl keys that the browser owns** (F1 help, F5 reload, F11 full screen, Ctrl+P print dialog, Ctrl+H
   history) in a web POS: pressing one reloads the page mid-bill or opens the wrong dialog.
2. **Single-letter keys that fire while typing** a guest's name or a note.
3. Shortcuts nobody knows exist: a printed sheet, never shown in the app.
4. Every vendor's map is different, so staff moving between tills relearn everything.

### Our choice
- Keys the browser leaves alone: `/` search, **Enter** (in search) adds the top dish, **Ctrl+Enter** send to
  kitchen, **Alt+H** hold, **T** new takeaway on the Floor, in the bill window **Alt+P** print and **Alt+C / Alt+U /
  Alt+K** pay the whole amount due by cash / UPI / card. No F-keys, no Ctrl+P (avoids 1).
- Plain keys (`/`, `T`, `?`) do nothing while a text field has focus; modifier keys always work (avoids 2).
- **?** opens the list; buttons show their key in the tooltip (avoids 3).
- No per-item short codes yet: search-and-Enter covers most of it without a new menu field to maintain.
