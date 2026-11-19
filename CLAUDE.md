# Hotel ERP Backend: project rules

Read `docs/BUILD-PLAN.md` at the start of every session. It is the only tracker. Do not copy it elsewhere; copies drift.

## Rules (break these and the build fails review)
1. **Money is integer paise.** Columns end in `_minor`. No floats, no arithmetic on amounts in Flutter; the server computes totals, tax and discounts and the client renders them.
2. **Tenant isolation.** Every tenant table has `hotel_id`; Postgres Row-Level Security is on; a request-scoped tenant context is set from the token, never from the request body. Every new table needs an RLS test (hotel A cannot read hotel B).
3. **Entitlements are checked on the server** through the single `require_feature` / `require_limit` dependency. The client hides things for UX only.
4. **Prices come from the server.** The diner client sends item ids and quantities, never prices.
5. **Kitchen assignment runs under the per-outlet lock** and is a pure function over a snapshot (`docs/04-kitchen-routing.md`). No assignment logic in route handlers.
6. **Every order-item state change is an audited event** (who, when, from, to).
7. **Tax rates are effective-dated config**, never constants in code.
8. **Flutter:** no `Text('literal')` in features; strings live in ARB with descriptions; theme tokens only; status is icon + label, never colour alone; serialise concurrent token refreshes.
9. **Refresh tokens rotate; reuse revokes the family.**
10. **Strict chef queue.** Only the head order of a chef's queue can start; ratings may only break near-ties and never override queue rules (`docs/04-kitchen-routing.md`).
11. **While the repo is public:** no secrets, no real customer data, no real pricing in commits.
12. **Prior art before every feature or change (mandatory).** Before building, research how existing restaurant
    platforms (e.g. Petpooja, Restroworks/POSist, UrbanPiper, Square, Toast, Lightspeed) design it; how it fits our
    app; what mistakes they made (user complaints, gaps) and how we avoid them. Record it in
    `docs/14-prior-art.md` (one section per feature) **before** writing code, then build our own way.

## Definition of done
Code, tests (including RLS and entitlement tests for new endpoints), OpenAPI updated, BUILD-PLAN ticked, migration reversible, `docs/13-functional-guide.md` updated (what users see and what happens), prior-art section in `docs/14-prior-art.md`.
