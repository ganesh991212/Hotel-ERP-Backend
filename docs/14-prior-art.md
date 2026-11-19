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
