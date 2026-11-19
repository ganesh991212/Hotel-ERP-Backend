# 04. Kitchen routing

Unit of assignment: **the whole order goes to one chef**, who works a **strict queue**: first order fully prepared, then the second.

## Modes (per outlet, plan-gated)
- **AUTO_ASSIGN**: the system picks the chef and appends the order to that chef's queue. Optional `require_accept` (with timeout).
- **CHEF_ACCEPT**: the order appears on the shared board; the first chef to accept claims it and it joins their queue. The claim is atomic, so two chefs can never both take it.
- **ADMIN_PRINT**: admin or cashier prints the KOT and the chef cooks from paper. No assignment, no queue.

## The chef queue
- Each chef has an ordered queue of orders (`assignments.queue_position`).
- Only the **head** order can be started. The next one unlocks when the head is marked READY.
- Admin (permission `kitchen.manage`, reason required, audited) can reorder, reassign, or put a blocked head on HOLD (for example an ingredient ran out). HOLD raises an alert and lets the next order unlock only if the admin chooses to.
- Add-on items to an order already in a queue stay with the same chef and the same position.
- Cancelling a queued order closes up the queue.

## AUTO_ASSIGN algorithm
Runs under a per-outlet lock (Postgres advisory lock keyed by outlet id) over one snapshot of chef state.

1. **Eligible** = active, `on_shift`, station matches (if stations are enabled), and `queue_len < max_queue_orders` (default 3).
2. **est_wait** = sum of remaining prep minutes of every order already in the chef's queue, including the one in progress. An empty queue is 0, so an idle chef always wins.
3. Let `best` = lowest `est_wait`. **Candidates** = eligible chefs with `est_wait <= best + rating_band_minutes` (default 5; 0 disables rating).
4. Among candidates: if `RATING_WEIGHTED_ROUTING` is on, highest eligible rating wins (see Ratings); otherwise skip to 5.
5. Remaining tie: longest idle (`last_completed_at` oldest), then lowest chef id (deterministic).
6. **No eligible chef**: the order waits unassigned; admin is alerted after `unassigned_alert_seconds`; the queue is re-run on every READY, shift and cancel event.
7. **require_accept**: if the chef does not accept within `accept_timeout_seconds` (default 90), the order returns to routing, excluding that chef once. Applies to the head only; later queue entries are accepted when they reach the head.
8. **Off shift**: the chef's queue is redistributed to other chefs in the original order.

The function is pure: `assign(snapshot, order, config) -> chef_id | None`.

## Ratings (food and service are separate)
- Two ratings per served order: **food** and **service**. Only **food** is attributed to the chef who cooked it. Lateness and waiter service are not the chef's fault.
- Only from verified table sessions that were served; one rating per session; cancelled or refunded orders excluded; rate-limited.
- Chef score = rolling 60-day window with time decay, Bayesian-smoothed toward the hotel average: `(n*avg + m*hotel_avg)/(n+m)`, `m = 20`.
- **No effect until n >= 10**; new chefs start at the hotel average.
- **Rating only breaks near-ties** (step 3 to 4 above): it can never send an order to a chef who is more than `rating_band_minutes` busier than the best. By construction it cannot overload a top chef.
- Ratings of 2 or lower go to a manager review queue and count only after the manager confirms (disputable).
- Chefs see their own rating only if the hotel enables it. `RATING_WEIGHTED_ROUTING` is a per-hotel toggle, off by default.

## Worked examples (become the first unit tests)
Config: chefs Ganesh, Ravi, Sita; `max_queue_orders=3`; `rating_band_minutes=5`; ratings ignored unless stated.

1. **Strict queue.** Ravi has orders A (head) then B. B cannot start until A is READY; attempting `start` on B is rejected.
2. **Idle chef wins (the Ganesh case).** Ganesh finished his order (wait 0), Ravi wait 15, Sita wait 20. New order goes to Ganesh.
3. **Full queue skipped.** Ganesh queue length 3 (cap). Ravi 15 and Sita 20 compete; Ravi wins even though Ganesh's wait is shorter.
4. **Tie.** Ravi and Sita both wait 20; Ravi last completed 12:01, Sita 12:04. Ravi wins.
5. **Rating near-tie only.** Waits: Ravi 20 (rating 3.9), Sita 24 (rating 4.8). Within 5 minutes, so Sita wins. If Sita's wait were 30, Ravi wins despite the lower rating.
6. **Rating ignored below sample size.** Sita has a single 5-star (n=1): rating is not used; tie-break falls back to idle time.
7. **All full.** All three at cap. Order waits unassigned, admin alerted; the first READY triggers a re-run and the freed chef gets it.
8. **Accept race (CHEF_ACCEPT).** Two chefs accept the same order in the same millisecond. Exactly one claim succeeds; the other gets a "taken" response.
9. **Accept timeout (AUTO_ASSIGN + require_accept).** Ravi does not accept in 90 s. Order goes to the next best chef, never back to Ravi first.
10. **Blocked head.** Ravi's head order is put on HOLD (ingredient out). Alert raised; admin chooses to unlock B or reassign A.
11. **Off shift with a queue.** Sita goes off shift holding A, B. Both are reassigned in order A then B among on-shift chefs.
12. **Cancel while queued.** B is cancelled; C moves up to position 2.
13. **Concurrency.** Two orders arrive in the same millisecond with one idle chef. Under the lock the second sees the first's assignment and goes to the next-best chef.

## Paper path (added 2026-11-18)
When a chef cannot check a device, a server or manager prints the KOT with "send to kitchen". The order moves
CONFIRMED -> PREPARING with `on_paper = true` and no assignment, so it leaves the shared board and AUTO_ASSIGN
skips it. Staff with `order.serve` or `kitchen.manage` mark it READY (chef-assigned orders stay with their chef).
In ADMIN_PRINT mode this is the normal flow.

## KOT content
Table, area, time, order number, items with modifiers and notes, station, assigned chef (AUTO mode), channel (QR/Waiter).

## Order workflow (added 2026-11-19)
Fixed stages, switched per outlet. No free-form workflow builder (every combination would need testing and staff
would get a different app per hotel).

```
Order in -> [Confirm] -> Kitchen (mode) -> Cooking -> Ready -> [Served] -> Bill & pay -> Table free
```

| Setting (outlet) | Values | Default |
|---|---|---|
| `qr_confirm_required` | QR orders wait for a waiter/admin to confirm | off |
| `kitchen_mode` | CHEF_ACCEPT / AUTO_ASSIGN / ADMIN_PRINT (above) | CHEF_ACCEPT |
| `serve_mode` | `SERVER`: a waiter carries food and taps Served. `COUNTER`: the diner collects; marking READY also marks it SERVED and tells the diner to collect | SERVER |
| `pickup_alert_minutes` | 1 to 60, or null = off. A CONFIRMED order still on the chefs' board after this long alerts admins (`kitchen.manage`) and chefs on shift, once per order | 5 |

Payment is after eating only (decided 2026-11-19; pay-before-cooking is out of scope).

Every order and table shows **what happens next and who does it** ("Next: chef to accept", "Next: serve",
"Next: bill"), with the button for that step when the viewer may do it. Admins can always step in (assign, print
KOT, mark ready).

**Table state** (derived, not stored): `OCCUPIED` while any order is open; `BILL_PENDING` while served orders are
unbilled or the table's bill is ISSUED (unpaid); `FREE` otherwise. Settling the bill frees the table.

**Counter mode and timings:** counter orders have `served_at = ready_at`; they are left out of the "serving"
average so they do not pull it to zero.

Deferred: **stations** (bar/kitchen split per item: needs a station per menu item and item-level tracking; waiting
on whether hotels have a separate bar); **automatic KOT printing** (browsers cannot print without a click; possible
later in the installed Android/desktop app with a network printer).
