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
