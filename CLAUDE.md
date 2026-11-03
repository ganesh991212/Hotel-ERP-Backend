# Hotel ERP Backend: project rules

Read `docs/BUILD-PLAN.md` at the start of every session. It is the only tracker. Do not copy it elsewhere; copies drift.

## Rules (break these and the build fails review)
1. **Money is integer paise.** Columns end in `_minor`. No floats, no arithmetic on amounts in Flutter; the server computes totals, tax and discounts and the client renders them.
2. **Tenant isolation.** Every tenant table has `hotel_id`; Postgres Row-Level Security is on; a request-scoped tenant context is set from the token, never from the request body. Every new table needs an RLS test (hotel A cannot read hotel B).
3. **Entitlements are checked on the server** through `require_feature` (dependency) and `enforce_limit` (under an advisory lock, so concurrent creates cannot overshoot). The client hides things for UX only.
4. **Prices come from the server.** The diner client sends item ids and quantities, never prices.
5. **Kitchen assignment runs under the per-outlet lock** and is a pure function over a snapshot (`docs/04-kitchen-routing.md`). No assignment logic in route handlers.
6. **Every order-item state change is an audited event** (who, when, from, to).
7. **Tax rates are effective-dated config**, never constants in code.
8. **Flutter:** no `Text('literal')` in features; strings live in ARB with descriptions; theme tokens only; status is icon + label, never colour alone; serialise concurrent token refreshes.
9. **Refresh tokens rotate; reuse revokes the family.**
10. **Strict chef queue.** Only the head order of a chef's queue can start; ratings may only break near-ties and never override queue rules (`docs/04-kitchen-routing.md`).
11. **While the repo is public:** no secrets, no real customer data, no real pricing in commits.

## Backend engineering rules (each one was a real defect somewhere)
12. **New hotel-scoped table → `TENANT_TABLES` in its migration** (ENABLE + FORCE + `hotel_isolation` policy with WITH CHECK). A table with `hotel_id` that is deliberately unscoped goes on the allowlist in `tests/test_db_safety.py` with its reasoning in the migration. The catalogue test fails otherwise.
13. **Queries still filter `hotel_id = :h` explicitly.** RLS is the safety net, not the filter: the explicit predicate keeps index use predictable.
14. **DB dependencies use `Depends(..., scope="function")`** (`HotelCtx`, `PlatformCtx`). With FastAPI's default scope the transaction commits *after* the response is sent.
15. **A write that must survive an error is committed before raising** (lockout counters, refresh-reuse revocation): return the error from inside the transaction, raise it after. Raising inside rolls the write back.
16. **Argon2 never runs on the event loop**: `hash_password_async` / `verify_password_async`, and never while holding a DB transaction.
17. **Handlers use the Core `AsyncConnection` (`ctx.db`), not an ORM session.** Measured +12% throughput.
18. **No privilege escalation**: grant only permissions you hold (`hotel.manage` holders excepted); never manage a member whose role exceeds yours; last-admin guard under `lock_admins`.
19. **Tests run as `hotel_app` against real Postgres.** Never point them at a superuser.

## Definition of done
Code, tests (including RLS and entitlement tests for new endpoints), OpenAPI updated, BUILD-PLAN ticked, migration reversible (CI runs downgrade + upgrade), `ruff` + `mypy --strict` clean.
