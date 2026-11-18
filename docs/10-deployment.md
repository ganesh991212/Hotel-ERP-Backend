# 10. Deployment: staging and production

Hosting: **Render** (API + web) and **Supabase** (Postgres only). Two fully separate environments; nothing is shared
between them (database, secrets, Firebase project, Razorpay mode, JWT secret).

| | Staging | Production |
|---|---|---|
| Purpose | Your testing and demos. Fictional data only. | Paying hotels |
| Blueprint | `render.yaml` | `deploy/render.production.yaml` (separate Blueprint) |
| Deploys from | `main`, automatically on every push | `production` branch, manual deploy |
| Render API plan | Free (sleeps after 15 min idle; ~1 min cold start) | Starter or higher (always on) |
| Migrations | On boot (`RUN_RELEASE_ON_START=true`) | Pre-deploy command `scripts/release.sh` |
| Supabase | Free project (pauses after 7 days with no traffic) | Pro project (daily backups, no pausing) |
| Web URL | `*.onrender.com` | **Your own domain** (printed on QR cards) |
| Razorpay | Test keys (`rzp_test_`) | Live keys; API refuses to boot with test keys |
| Values | `deploy/env.staging.example` | `deploy/env.production.example` |

Region: Render **Singapore** and Supabase **Southeast Asia (Singapore)**. Render has no India region; keeping both in
the same region matters more than either being in India, because every request makes several database round trips.

## What runs

```
Browser / phone app
   |  https
   |-- hotel-erp-web-<env>   Render static site: Flutter web build (build: Frontend scripts/render-build.sh)
   |-- hotel-erp-api-<env>   Render web service: Docker image (Dockerfile), one uvicorn process
          |  Supavisor session pooler, TLS
          '-- Supabase Postgres: roles hotel_owner / hotel_app / hotel_platform (scripts/supabase-roles.sql)
```

The API refuses to start in `staging` or `production` when `JWT_SECRET`, `PUBLIC_DINER_URL`, `CORS_ORIGINS` or the
database URLs are missing or still local (`app/core/config.py: validate`). The deploy log then names every problem.

## First-time setup: staging

Order matters: database first, then API, then web, then the values that depend on the URLs Render assigns.

### 1. Supabase project
1. New project, name `hotel-erp-staging`, region **Southeast Asia (Singapore)**. Save the database password
   Supabase generates (that is the `postgres` admin; the app never uses it).
2. **Project Settings > Data API: disable it.** We do not use Supabase's auto-generated REST API; leaving it on
   exposes an extra door to the database.
3. Generate three passwords in a password manager (32+ letters and digits; no `@ : / ? # %`).
4. SQL Editor: paste `scripts/supabase-roles.sql`, replace the three `REPLACE_` placeholders, run. The last query
   must show three roles and only `hotel_platform` with `rolbypassrls = true`. Do not save the edited script.
   If `CREATE ROLE ... BYPASSRLS` is refused, stop and report it; do not give the app the `postgres` role instead.
5. **Connect > Session pooler**: copy the host (e.g. `aws-0-ap-southeast-1.pooler.supabase.com`) and the project ref.
   Build the three URLs as in `deploy/env.staging.example`:
   `postgresql+asyncpg://<role>.<ref>:<password>@<host>:5432/postgres?ssl=require`.
   - Session pooler, port 5432. Not "Direct connection" (IPv6 only, Render cannot reach it) and not the transaction
     pooler on 6543 (breaks asyncpg prepared statements).
   - The user is `<role>.<ref>` (e.g. `hotel_app.abcdefghijklmnopqrst`), not plain `hotel_app`.

### 2. Render Blueprint
1. Both services deploy from the branch named in `render.yaml` (temporarily `claude/magical-meitner-74zq87`; `main` once the backend main conflict is resolved).
2. Render > New > Blueprint > connect the backend repo; Render reads `render.yaml`. Give the Render GitHub app access
   to **both** repos.
3. Render asks for every `sync: false` value. Fill them from `deploy/env.staging.example`:
   - the three database URLs from step 1.5;
   - `PUBLIC_DINER_URL` = `https://hotel-erp-web-staging.onrender.com/#/t`, `CORS_ORIGINS` =
     `https://hotel-erp-web-staging.onrender.com`, `API_BASE` = `https://hotel-erp-api-staging.onrender.com`
     (expected names; corrected in step 3 if Render assigns different ones);
   - `PLATFORM_ADMIN_EMAIL` / `PLATFORM_ADMIN_PASSWORD`: the super-admin login (the new project mailbox);
   - Razorpay and Firebase values may stay empty for now (checkout and push are then off).
4. Apply. The API boots, runs migrations and the seed, and logs `platform admin created: <email>`.

### 3. Fix up URLs
Render adds a suffix when a service name is taken. Open both services and read their real URLs. If they differ
from step 2.3, update `PUBLIC_DINER_URL` and `CORS_ORIGINS` on the API and `API_BASE` on the web, then redeploy
**both** (`API_BASE` is compiled into the web build, so the web needs a rebuild, not just a restart).

### 4. Verify (10 minutes)
1. `https://<api>/health` returns `{"status":"ok"}`.
2. Supabase SQL Editor: run `scripts/supabase-verify.sql`; every query returns zero rows.
3. Web: log in as the super admin, change the password, then clear `PLATFORM_ADMIN_PASSWORD` in Render.
4. Create a test hotel, a table, a menu item; print the table QR; scan it with a phone (mobile data, not Wi-Fi) and
   place an order; see it arrive on the chef screen; mark it ready; see the diner page update.
5. Check the API logs contain no passwords or tokens.

### 5. Optional on staging
- **Razorpay test mode**: keys from the Razorpay dashboard in Test mode; webhook URL
  `https://<api>/api/v1/webhooks/razorpay`, secret into `RAZORPAY_WEBHOOK_SECRET`.
- **Push**: a separate Firebase project for staging; fill the `FCM_` and `FIREBASE_` values; add the web domain to
  Firebase authorised domains.
- **Mobile builds against staging**: `flutter build apk --dart-define=API_BASE=https://<api>` (same for iOS).

## Routine deploys
- Staging: merge to `main`. Render rebuilds both services. Migrations run on boot.
- Production (later): after the change has been verified on staging, fast-forward `production` to the same commit
  and press Deploy on both production services (API first, then web).

**Migrations must be backward compatible** (add columns, tables, nullable fields; drop or rename only in a later
release). In production the new schema is applied while the previous version is still serving traffic, and a
rollback in Render redeploys old code against the new schema. Never rely on `alembic downgrade` in a deployed
environment: it can drop data.

## Production: before the first paying hotel
Do these in order; most cannot be undone cheaply once hotels have printed QR cards.
1. **Buy the domain** and decide the web host (e.g. `app.<domain>`) and API host (e.g. `api.<domain>`). The web host
   is printed in every table QR: changing it later means reprinting every card in every hotel.
2. New Supabase project `hotel-erp-production` (Pro plan, Singapore), Data API off, roles script with **new**
   passwords, point-in-time recovery if the budget allows.
3. Render > New > Blueprint, path `deploy/render.production.yaml`. Fill from `deploy/env.production.example`.
   Attach the custom domains to both services; set `PUBLIC_DINER_URL`, `CORS_ORIGINS`, `API_BASE` to them.
4. Razorpay live keys and live webhook; separate Firebase project; release builds of the Android/iOS apps with
   `--dart-define=API_BASE=https://api.<domain>`.
5. Run the verification above, plus a restore drill: restore yesterday's Supabase backup into a scratch project
   and log in against it. A backup never restored is not a backup.
6. Uptime check on `https://api.<domain>/health` (any external pinger) alerting your phone.

## Scaling limit (read before adding instances)
The realtime hub (`app/realtime/hub.py`) and push fan-out are in-process. With two API processes, a chef on
instance A does not see an order placed through instance B. Therefore:
- one instance, one uvicorn worker (`scripts/start.sh`, `numInstances: 1`);
- scale up the instance size, not the count;
- before running more than one instance, move realtime fan-out to Redis or Postgres `LISTEN/NOTIFY`
  (docs/02 describes the Redis design; it is not built yet).

During a Render deploy the old and new instance overlap for a few seconds; connected screens reconnect and
re-fetch, so nothing is lost, but an order placed in that window may show on the chef screen after the reconnect.

## Secrets
- Real values live only in Render's environment settings (and your password manager). This repository is public:
  the `deploy/*.example` files hold placeholders only.
- Staging and production never share a secret. Rotating the JWT secret signs everyone out (acceptable).
- Database passwords: change with `ALTER ROLE ... PASSWORD` in Supabase, then update the URL in Render.
