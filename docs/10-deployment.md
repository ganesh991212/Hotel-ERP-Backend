# 10. Deployment: staging and production

Hosting: **Render** (API + web) and **Supabase** (Postgres only). Two fully separate environments; nothing is shared
between them (database, secrets, Firebase project, Razorpay mode, JWT secret).

| | Staging | Production |
|---|---|---|
| Purpose | Your testing and demos. Fictional data only. | Paying hotels |
| Service settings | created by hand (docs/12); reference `render.yaml` | `deploy/render.production.yaml` (Blueprint) |
| Deploys from | `staging` branch, automatically on every push | `main` branch, manual deploy |
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

## First-time setup
Step by step, field by field, with the mistakes to avoid: **`docs/12-staging-setup-guide.md`**. Production follows
the same guide (differences are marked PROD there) plus the checklist below.

## Routine deploys
Branch flow, the same in both repos:

```
feature branch --PR--> staging --(verified on staging)--PR--> main --manual deploy--> production
```

- `staging` and `main` are the only long-lived branches. Work happens on short-lived feature branches that are
  deleted after merging. Never commit to `main` directly; it only receives `staging`.
- Staging: merge a feature PR into `staging`. Render rebuilds both staging services. Migrations run on boot.
- Production (later): after the change has been verified on staging, merge a `staging` -> `main` PR
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
- Real values live only in Render's environment settings (and your password manager). Treat this repository as public:
  the `deploy/*.example` files hold placeholders only.
- Staging and production never share a secret. Rotating the JWT secret signs everyone out (acceptable).
- Database passwords: change with `ALTER ROLE ... PASSWORD` in Supabase, then update the URL in Render.
