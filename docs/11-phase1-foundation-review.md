# 11. What to port from the superseded Phase 1 foundation (PR #2)

PR #2 (`claude/phase-1-foundation`) was a second, independent Phase 1 backend. It was merged into `main` while
this codebase was being built, then superseded because the frontend targets this codebase. Its code is kept at
tag **`archive/phase-1-foundation`** (`git show archive/phase-1-foundation:<path>`).

Checked against this codebase on 2026-11-18. Each item: port, or decide not to, then tick.

## Gaps here (port these)
- [ ] **Login lockout.** PR #2 counts failed logins and locks the account for a while, committing the counter
      *before* raising the error (raising inside the transaction rolls the counter back). We have no lockout.
- [ ] **Argon2 off the event loop.** PR #2 hashes and verifies in a thread (`hash_password_async`). Ours runs on the
      event loop, so a burst of logins stalls every request on that instance.
- [ ] **`FORCE ROW LEVEL SECURITY`.** Ours uses ENABLE only, so the table owner (`hotel_owner`, used by migrations and
      the seed) bypasses RLS. Low risk because the API never connects as the owner, but FORCE makes that structural.
- [ ] **Catalogue test.** PR #2 fails CI when a table with `hotel_id` has no RLS policy (allowlist for deliberate
      exceptions). We test isolation per table, not the catalogue.
- [ ] **Immediate revocation.** PR #2 checks on each request (one indexed query) that the session is still valid,
      so a disabled user is out at once. Ours waits for the 15-minute access token to expire.
- [ ] **Limit checks under an advisory lock** (`enforce_limit`), so two concurrent creates cannot both pass a plan limit.
- [ ] **Login by phone** as well as email; **hotel audit log** endpoint; **per-hotel plan overrides**.

## Conventions worth adopting
- Queries filter `hotel_id = :h` explicitly even with RLS (index use stays predictable).
- No privilege escalation: you may grant only permissions you hold; last-admin guard.
- CI runs migrations downgrade + upgrade; `mypy --strict`.
