# 12. Staging setup guide (step by step, as actually done)

This is the hands-on record of how staging was set up on **Supabase** (database) and **Render** (API + web), field
by field, including the mistakes we hit and how to avoid them. Use it again for production; differences for
production are marked **PROD**. Background and the reasons behind each choice: `docs/10-deployment.md`.

Placeholders like `<APP_PASSWORD>` mean "your real value from the password manager". Real secrets never go in this
file, in chat, or in git.

| Item | Staging value |
|---|---|
| Supabase project | `hotel-erp-staging`, ref `mhgmnmkbzmmbflvlnrrc`, region Southeast Asia (Singapore) `ap-southeast-1` |
| Supabase pooler host | `aws-1-ap-southeast-1.pooler.supabase.com` (Session pooler, port 5432) |
| API | `https://hotel-erp-api-staging.onrender.com` (Render web service, Docker, Singapore) |
| Web | `https://hotel-erp-web-staging.onrender.com` (Render static site) |
| Git branch deployed | `staging` (both repos) |

---

## Step 0. Before you start

1. **A project mailbox** used only for this product's accounts (Supabase, Render, the super-admin login).
2. **A password manager.** You will create: 3 database passwords, 1 super-admin password, 1 JWT secret.
   - Database passwords: **letters and digits only, 32+ characters.** Symbols break connection URLs (see Step 3.3).
3. Access to both GitHub repos: `Hotel-ERP-Backend`, `Hotel-ERP-Frontend`.

## Step 1. GitHub branches

Both repos use the same flow:

```
feature branch --PR--> staging --(tested on staging)--PR--> main --manual deploy--> production
```

1. `staging` and `main` are the only long-lived branches. `staging` deploys to staging automatically; `main` is
   production.
2. Delete finished feature branches: repo > **Branches** > trash icon.
3. Recommended: repo > **Settings > Branches** > protect `main` (require a pull request).

**Local copy on the Mac** (both repos side by side, the frontend reads the backend's docs):
```bash
cd ~/repos/"Hotel Management"
git clone -b staging https://github.com/ganesh991212/Hotel-ERP-Backend.git
git clone -b staging https://github.com/ganesh991212/Hotel-ERP-Frontend.git
```

## Step 2. Supabase project

1. supabase.com > **New project**
   | Field | Value |
   |---|---|
   | Organization | Hotel ERP |
   | Name | `hotel-erp-staging` (**PROD:** `hotel-erp-production`) |
   | Database password | generate; save it as "Supabase postgres admin". The app never uses it. |
   | Region | **Southeast Asia (Singapore)** |
   | Plan | Free (**PROD:** Pro, for daily backups and no pausing) |
2. **Project Settings > Data API > turn it off.** The app does not use Supabase's REST API; leaving it on is an
   extra way into the database.
3. Only one project per environment. A first project was created in Tokyo by mistake and deleted: Render runs in
   Singapore, and a database in another region adds roughly 0.3 to 0.5 s to most screens.

## Step 3. Database roles and passwords

The app uses three database logins, never the `postgres` admin:

| Role | Used for | Sees other hotels' data? |
|---|---|---|
| `hotel_owner` | creating tables (migrations) at deploy time | owner of the tables |
| `hotel_app` | all normal hotel requests | **no** (Row-Level Security) |
| `hotel_platform` | super admin and login lookups | yes (`BYPASSRLS`) |

### 3.1 Create the roles
SQL Editor > New query > paste `scripts/supabase-roles.sql` > replace the three `REPLACE_..._PASSWORD`
placeholders > **Run**. The last result must show three roles, with `rolbypassrls = true` only for `hotel_platform`.

(Staging: the roles were created first without passwords, and the passwords were set in 3.2. Either way works.)

### 3.2 Set or change the passwords
SQL Editor, with your three passwords:
```sql
ALTER ROLE hotel_owner    LOGIN PASSWORD '<OWNER_PASSWORD>';
ALTER ROLE hotel_app      LOGIN PASSWORD '<APP_PASSWORD>';
ALTER ROLE hotel_platform LOGIN PASSWORD '<PLATFORM_PASSWORD>';
```
Do not save this query with real passwords in it.

### 3.3 Build the three connection URLs
Get the host: project > **Connect** (top bar) > **Session pooler** > copy only the host.
Use the **Session pooler** (port 5432). Not "Direct connection" (Render cannot reach it) and not "Transaction pooler"
(port 6543, breaks the app's database driver).

```
DATABASE_URL           postgresql+asyncpg://hotel_app.mhgmnmkbzmmbflvlnrrc:<APP_PASSWORD>@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres?ssl=require
PLATFORM_DATABASE_URL  postgresql+asyncpg://hotel_platform.mhgmnmkbzmmbflvlnrrc:<PLATFORM_PASSWORD>@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres?ssl=require
MIGRATION_DATABASE_URL postgresql+asyncpg://hotel_owner.mhgmnmkbzmmbflvlnrrc:<OWNER_PASSWORD>@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres?ssl=require
```

Check each URL before saving it:
- starts with `postgresql+asyncpg://` (not `postgresql://`);
- user is `<role>.mhgmnmkbzmmbflvlnrrc` (the `.ref` suffix is required by the pooler);
- **no placeholder words left**: no `HOST`, `APP_PASSWORD`, `<`, `>`;
- ends with `/postgres?ssl=require`.

If a password contains a symbol, write it encoded **inside the URL only** (Supabase keeps the plain password):

| Symbol | In the URL | | Symbol | In the URL |
|---|---|---|---|---|
| `%` | `%25` | | `#` | `%23` |
| `@` | `%40` | | `&` | `%26` |
| `:` | `%3A` | | `+` | `%2B` |
| `/` | `%2F` | | space | `%20` |
| `?` | `%3F` | | | |

Example: password `Ab12%xyz` is written `Ab12%25xyz`. Simpler: use letters-and-digits-only passwords.

## Step 4. Render: the API (web service)

Render dashboard > project > environment **Staging** (create it if missing; keep staging and production services in
separate environments) > **New > Web Service** > repo **Hotel-ERP-Backend**.

| Field | Value |
|---|---|
| Name | `hotel-erp-api-staging` |
| Project / Environment | My project / **Staging** |
| Language (runtime) | **Docker** (not Python; Render cannot switch it later, the service must be recreated) |
| Branch | `staging` |
| Region | **Singapore** |
| Root Directory | empty |
| Dockerfile Path | `./Dockerfile` |
| Docker Build Context Directory | `.` |
| Docker Command | empty (the image starts itself) |
| Instance Type | Free (**PROD:** Starter or higher) |
| Health Check Path | `/health` |
| Auto-Deploy | On commit (**PROD:** off, deploy by hand) |

### 4.1 API environment variables
Every row below must exist. Names are case-sensitive; no spaces around values.

| Key | Value | Notes |
|---|---|---|
| `APP_ENV` | `staging` | turns on the startup check; a wrong setting then fails the deploy with a clear message |
| `RUN_RELEASE_ON_START` | `true` | creates/updates tables and the super admin on every start (**PROD:** `false`, see 4.3) |
| `JWT_SECRET` | 64 random characters | see 4.2. **Not** the word `Generate`. |
| `DATABASE_URL` | from Step 3.3 | secret |
| `PLATFORM_DATABASE_URL` | from Step 3.3 | secret |
| `MIGRATION_DATABASE_URL` | from Step 3.3 | secret |
| `DB_POOL_SIZE` | `3` | (**PROD:** `5`) |
| `DB_MAX_OVERFLOW` | `2` | (**PROD:** `5`) |
| `PUBLIC_DINER_URL` | `https://hotel-erp-web-staging.onrender.com/#/t` | printed inside every table QR code |
| `CORS_ORIGINS` | `https://hotel-erp-web-staging.onrender.com` | no trailing `/`, never `*` |
| `PLATFORM_ADMIN_EMAIL` | the project mailbox | first super-admin login |
| `PLATFORM_ADMIN_PASSWORD` | strong password | secret; clear it after the first login (Step 6) |
| `POWERED_BY_NAME` | `Vethra` | footer on printed QR cards |
| `POWERED_BY_CONTACT` | website or phone, or empty | public text |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | empty for now | test keys later (Step 7) |
| `FCM_*`, `FIREBASE_*` | empty for now | push later (Step 7); in-app alerts work without them |

The full annotated list is `deploy/env.staging.example` (**PROD:** `deploy/env.production.example`).

### 4.2 Generating `JWT_SECRET`
Either:
- Mac Terminal: `openssl rand -hex 32` > copy the 64-character output > paste as the value; or
- delete the row, **+ Add Environment Variable**, key `JWT_SECRET`, press the **Generate** button beside the
  value box (only shown when adding a new row).

Never reuse it between staging and production. Changing it signs everyone out once; nothing else breaks.

### 4.3 PROD only: migrations before traffic
Settings > **Pre-Deploy Command**: `sh scripts/release.sh`, and `RUN_RELEASE_ON_START=false`. (The free plan has no
pre-deploy command, which is why staging migrates on start.)

### 4.4 Deploy
**Save, rebuild, and deploy**. A good log shows, in order:
```
Running upgrade  -> 0001 ...
Running upgrade 0001 -> 0002 ...
Running upgrade 0002 -> 0003 ...
platform admin created: <email>
Uvicorn running on http://0.0.0.0:...
```

## Step 5. Render: the web app (static site)

Same project and environment > **New > Static Site** > repo **Hotel-ERP-Frontend**.

| Field | Value |
|---|---|
| Name | `hotel-erp-web-staging` |
| Project / Environment | My project / **Staging** |
| Branch | `staging` |
| Root Directory | empty |
| Build Command | `bash scripts/render-build.sh` |
| Publish Directory | `build/web` |

Environment variables:

| Key | Value |
|---|---|
| `API_BASE` | `https://hotel-erp-api-staging.onrender.com` (no trailing `/`; the build stops if there is one) |
| `FLUTTER_VERSION` | `3.50.0` |

After creating it: **Settings > Headers > Add header**: path `/*`, name `Cache-Control`, value `no-cache`.
Without it, phones can keep an old version of the app for a few minutes after a deploy.

The first build takes 8 to 10 minutes (it downloads Flutter). A good log ends with
`Built build/web for API https://hotel-erp-api-staging.onrender.com`.

`API_BASE` is compiled into the web app: whenever it changes, redeploy the static site (Manual Deploy > Clear build
cache & deploy).

## Step 6. Verify

| # | Check | How | Expected |
|---|---|---|---|
| 1 | API up | open `https://hotel-erp-api-staging.onrender.com/health` | `{"status":"ok"}` |
| 2 | Tables exist | Supabase > Table Editor | `hotels`, `users`, `plans`, ... (about 30 tables) |
| 3 | Isolation | SQL Editor: run `scripts/supabase-verify.sql` | every query returns **zero rows** |
| 4 | Web loads | open `https://hotel-erp-web-staging.onrender.com` | login screen |
| 5 | Super admin | log in with `PLATFORM_ADMIN_EMAIL` | platform dashboard |
| 6 | Clean up | change the password in the app, then delete `PLATFORM_ADMIN_PASSWORD` in Render | |
| 7 | End to end | create a test hotel, table, menu item; print the table QR; scan it on a phone **using mobile data**; order; see it on the chef screen; mark ready; diner page updates | |
| 8 | Logs | Render > API > Logs | no passwords or tokens visible |

Free-plan behaviour: the API sleeps after 15 minutes idle; the first request after that takes about a minute.
The free Supabase project pauses after 7 days without traffic (Supabase > project > Restore).

## Step 7. Later (optional on staging)

- **Razorpay test mode:** Razorpay dashboard in Test mode > API keys (`rzp_test_...`) into `RAZORPAY_KEY_ID` /
  `RAZORPAY_KEY_SECRET`; Webhooks > URL `https://hotel-erp-api-staging.onrender.com/api/v1/webhooks/razorpay`,
  secret into `RAZORPAY_WEBHOOK_SECRET`.
- **Push notifications:** a separate Firebase project for staging; fill `FCM_PROJECT_ID`, `FCM_SERVICE_ACCOUNT_JSON`
  (the whole JSON on one line) and the `FIREBASE_*` values; add the web domain to Firebase > Authentication >
  Authorized domains.
- **Phone apps against staging:** `flutter build apk --dart-define=API_BASE=https://hotel-erp-api-staging.onrender.com`
  (same flag for iOS).

---

## Mistakes we hit (avoid these)

| What happened | Symptom | Fix |
|---|---|---|
| Supabase project created in Tokyo | slow screens (not yet noticed, caught early) | recreate in Singapore while empty |
| Template URLs pasted as-is (`...:APP_PASSWORD@HOST:5432...`) | `/health` OK but login gives "Internal Server Error"; Supabase shows no connection attempts; no tables | replace `HOST` and the password words with real values (Step 3.3) |
| `JWT_SECRET` set to the word `Generate` | anyone could forge logins | real random value (Step 4.2) |
| Services created one by one instead of from the Blueprint | `APP_ENV`, `RUN_RELEASE_ON_START`, `JWT_SECRET` missing, tables never created, no error shown | add every row in Step 4.1 |
| Password with `%` in a URL | "password authentication failed" or a URL parse error | encode it (`%` is `%25`) or use letters and digits only |
| Render API key saved with `Bearer` in it | Render API returns 401 | paste only the key (starts with `rnd_`) |
| Staging services placed in a Render environment named "Production" | confusion once real production exists | separate **Staging** environment |
| Backend `main` had a different, older Phase 1 codebase (PR #2) | PR conflicts | `main` now equals `staging`; PR #2 kept in history at commit `a840958`; ideas to port in `docs/11` |

## Where things live

| What | Where |
|---|---|
| Secrets | Render > service > Environment, and your password manager. Nowhere else. |
| Every variable, explained | `deploy/env.staging.example`, `deploy/env.production.example` |
| Service settings as code | `render.yaml` (staging), `deploy/render.production.yaml` (production). Staging was created by hand in the dashboard; these files are the reference for its settings and can create production via **New > Blueprint**. |
| Database role setup | `scripts/supabase-roles.sql`; checks: `scripts/supabase-verify.sql` |
| Why it is built this way | `docs/10-deployment.md` |

## Status log

| Date | State |
|---|---|
| 2026-11-18 | Supabase staging project, roles and passwords done. Web app live and pointing at the API. API live, but database URLs still held template placeholders, so no tables yet. Next: Step 4.1 values, redeploy, Step 6. |
