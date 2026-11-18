"""Phase 1 foundation: platform, identity, tenancy, RBAC, entitlements, audit.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-11-03

Two kinds of table live here, and the split is a decision, not an accident.

**Hotel-scoped (RLS ENABLE + FORCE, policy on ``hotel_id = app_hotel_id()``):**
hotel_tax_profile, outlet, role, membership, audit_log. These hold a hotel's own data.
FORCE matters: without it the table owner (which runs migrations and scripts) bypasses
the policy. WITH CHECK matters: without it a hotel could *write* rows into another.

**Platform (no RLS, listed in UNSCOPED_TABLES):** hotel, app_user, plan, feature_catalog,
plan_feature, subscription, hotel_entitlement_override, auth_session, refresh_token,
invite_token, platform_audit_log. They are the tenancy root, global catalogues, or
token-keyed auth state that must be found *before* the hotel is known (login, refresh,
invite). None holds guest or order data. Hotel-facing endpoints read them filtered by the
hotel id from the token. ``tests/test_rls_coverage.py`` fails the build if any table with
a ``hotel_id`` column is neither forced-RLS nor on this list.

Entitlements cache invalidation: every change to a plan's features, the feature
catalogue, a hotel's subscription or its overrides bumps ``hotel.entitlements_version``
via triggers. The per-request access check reads that version, so cached entitlements are
keyed by it and can never be stale, on any number of API processes, with no extra query.
"""

from __future__ import annotations

import uuid

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ["hotel_tax_profile", "outlet", "role", "membership", "audit_log"]
UNSCOPED_TABLES = [
    "hotel",
    "app_user",
    "plan",
    "feature_catalog",
    "plan_feature",
    "subscription",
    "hotel_entitlement_override",
    "auth_session",
    "refresh_token",
    "invite_token",
    "platform_audit_log",
]


def _rid(code: str) -> str:
    # Must match app.core.permissions._rid; test_system_roles_match_code asserts it.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"hotel-erp/system-role/{code}"))


_ALL_PERMS = [
    "hotel.manage",
    "outlet.manage",
    "staff.manage",
    "role.manage",
    "audit.read",
    "menu.manage",
    "table.manage",
    "order.create",
    "order.view",
    "order.cancel",
    "kitchen.work",
    "kitchen.manage",
    "bill.create",
    "bill.discount",
    "payment.collect",
    "report.view",
]
SYSTEM_ROLES = [
    # Not kitchen.work: it means "receives kitchen orders"; admins never join the queue.
    ("hotel_admin", "Hotel admin", [p for p in _ALL_PERMS if p != "kitchen.work"]),
    (
        "manager",
        "Manager",
        [
            "menu.manage",
            "table.manage",
            "order.create",
            "order.view",
            "order.cancel",
            "kitchen.manage",
            "bill.create",
            "bill.discount",
            "payment.collect",
            "report.view",
            "audit.read",
        ],
    ),
    ("cashier", "Cashier", ["order.view", "bill.create", "payment.collect"]),
    ("waiter", "Waiter", ["order.create", "order.view"]),
    ("chef", "Chef", ["order.view", "kitchen.work"]),
]

# (key, kind, description, default)
FEATURES = [
    ("WAITER_ORDERING", "flag", "Waiters take orders on the staff app", False),
    ("QR_ORDERING", "flag", "Guests order by scanning the table QR", False),
    ("ADMIN_PRINT_MODE", "flag", "Kitchen mode: admin prints the KOT", False),
    ("AUTO_ROUTING", "flag", "Kitchen mode: automatic chef assignment", False),
    ("CHEF_ACCEPT_MODE", "flag", "Kitchen mode: chefs accept orders from a shared board", False),
    ("MULTI_STATION", "flag", "Kitchen stations (tandoor, grill, beverages)", False),
    ("RATINGS", "flag", "Guest food and service ratings", False),
    ("RATING_WEIGHTED_ROUTING", "flag", "Ratings break near-ties in chef assignment", False),
    ("SPLIT_BILLING", "flag", "Split and merge bills", False),
    ("REPORTS_ADVANCED", "flag", "Advanced reports", False),
    ("API_WEBHOOKS", "flag", "Hotel API keys and webhooks", False),
    ("DEVICE_DISPLAYS", "flag", "Paired TVs and tablets as kitchen displays", False),
    ("ROOMS", "flag", "Rooms, bookings and housekeeping", False),
    ("ROOM_SERVICE", "flag", "Room-service orders into the kitchen", False),
    ("MAX_OUTLETS", "limit", "Maximum active outlets", 0),
    ("MAX_TABLES", "limit", "Maximum tables across outlets", 0),
    ("MAX_STAFF", "limit", "Maximum staff members (active and invited)", 0),
    ("MAX_CHEFS", "limit", "Maximum chefs", 0),
    ("MAX_MENU_ITEMS", "limit", "Maximum menu items", 0),
    ("MAX_ORDERS_PER_MONTH", "limit", "Maximum orders per calendar month", 0),
]

# Placeholder prices in paise, editable by super admin (docs/05). Annual = 10 months.
PLANS = [
    ("starter", "Starter", 99_900, 999_000, 10),
    ("growth", "Growth", 249_900, 2_499_000, 20),
    ("pro", "Pro", 499_900, 4_999_000, 30),
]
_U = None  # unlimited
PLAN_FEATURES: dict[str, dict[str, bool | int | None]] = {
    "starter": {
        "WAITER_ORDERING": True,
        "ADMIN_PRINT_MODE": True,
        "MAX_OUTLETS": 1,
        "MAX_TABLES": 15,
        "MAX_STAFF": 10,
        "MAX_CHEFS": 3,
        "MAX_MENU_ITEMS": 150,
        "MAX_ORDERS_PER_MONTH": 3000,
    },
    "growth": {
        "WAITER_ORDERING": True,
        "QR_ORDERING": True,
        "ADMIN_PRINT_MODE": True,
        "AUTO_ROUTING": True,
        "RATINGS": True,
        "SPLIT_BILLING": True,
        "DEVICE_DISPLAYS": True,
        "MAX_OUTLETS": 3,
        "MAX_TABLES": 60,
        "MAX_STAFF": 40,
        "MAX_CHEFS": 10,
        "MAX_MENU_ITEMS": 600,
        "MAX_ORDERS_PER_MONTH": _U,
    },
    "pro": {
        **{key: True for key, kind, _d, _v in FEATURES if kind == "flag"},
        **{key: _U for key, kind, _d, _v in FEATURES if kind == "limit"},
    },
}


def _sql_literal(value: bool | int | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(int(value))


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # ---- helpers --------------------------------------------------------------------
    # NULLIF: on a pooled connection an expired transaction-local setting reads as '' (not
    # NULL), and ''::uuid raises. Lesson from vethra_api migration 0003.
    op.execute(
        """
        CREATE FUNCTION app_hotel_id() RETURNS uuid LANGUAGE sql STABLE AS
          $$ SELECT NULLIF(current_setting('app.hotel_id', true), '')::uuid $$;
        CREATE FUNCTION app_user_id() RETURNS uuid LANGUAGE sql STABLE AS
          $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;
        CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS
          $$ BEGIN NEW.updated_at := now(); RETURN NEW; END $$;
        """
    )

    # ---- platform tables (unscoped) -------------------------------------------------
    op.execute(
        """
        CREATE TABLE hotel (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name varchar(200) NOT NULL,
          slug varchar(64) NOT NULL,
          legal_name varchar(200),
          timezone varchar(64) NOT NULL DEFAULT 'Asia/Kolkata',
          currency char(3) NOT NULL DEFAULT 'INR',
          status varchar(16) NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'suspended', 'cancelled')),
          status_reason text,
          entitlements_version bigint NOT NULL DEFAULT 1,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_hotel_slug UNIQUE (slug),
          CONSTRAINT ck_hotel_slug CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$')
        );
        CREATE INDEX ix_hotel_created ON hotel (created_at DESC, id DESC);
        CREATE INDEX ix_hotel_status_created ON hotel (status, created_at DESC, id DESC);

        CREATE TABLE app_user (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          full_name varchar(200) NOT NULL,
          email varchar(320),
          phone varchar(20),
          password_hash varchar(255),
          password_updated_at timestamptz,
          status varchar(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
          is_platform_admin boolean NOT NULL DEFAULT false,
          failed_login_count integer NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
          locked_until timestamptz,
          last_login_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_app_user_email UNIQUE (email),
          CONSTRAINT uq_app_user_phone UNIQUE (phone),
          CONSTRAINT ck_app_user_contact CHECK (email IS NOT NULL OR phone IS NOT NULL)
        );

        CREATE TABLE plan (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          code varchar(32) NOT NULL,
          name varchar(80) NOT NULL,
          description text,
          price_monthly_minor bigint NOT NULL CHECK (price_monthly_minor >= 0),
          price_annual_minor bigint NOT NULL CHECK (price_annual_minor >= 0),
          currency char(3) NOT NULL DEFAULT 'INR',
          is_placeholder boolean NOT NULL DEFAULT true,
          is_active boolean NOT NULL DEFAULT true,
          sort_order integer NOT NULL DEFAULT 0,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_plan_code UNIQUE (code),
          CONSTRAINT ck_plan_code CHECK (code ~ '^[a-z][a-z0-9_]{1,31}$')
        );

        CREATE TABLE feature_catalog (
          key varchar(48) PRIMARY KEY CHECK (key ~ '^[A-Z][A-Z0-9_]{1,47}$'),
          kind varchar(8) NOT NULL CHECK (kind IN ('flag', 'limit')),
          description text NOT NULL,
          default_flag boolean,
          default_limit integer CHECK (default_limit >= 0),
          sort_order integer NOT NULL DEFAULT 0,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_feature_default CHECK (
            (kind = 'flag' AND default_flag IS NOT NULL AND default_limit IS NULL)
            OR (kind = 'limit' AND default_flag IS NULL)
          )
        );

        -- A row's presence means "the plan sets this feature". For a limit, NULL means
        -- unlimited; for a flag, flag_value is required (enforced by trigger below).
        CREATE TABLE plan_feature (
          plan_id uuid NOT NULL REFERENCES plan (id) ON DELETE CASCADE,
          feature_key varchar(48) NOT NULL REFERENCES feature_catalog (key) ON DELETE CASCADE,
          flag_value boolean,
          limit_value integer CHECK (limit_value >= 0),
          PRIMARY KEY (plan_id, feature_key)
        );

        CREATE TABLE subscription (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          hotel_id uuid NOT NULL REFERENCES hotel (id) ON DELETE RESTRICT,
          plan_id uuid NOT NULL REFERENCES plan (id) ON DELETE RESTRICT,
          status varchar(16) NOT NULL
            CHECK (status IN ('trial', 'active', 'past_due', 'cancelled')),
          billing_interval varchar(8) NOT NULL CHECK (billing_interval IN ('monthly', 'annual')),
          -- Price snapshot at subscription time: changing a plan's price later does not
          -- change what an existing subscriber pays until they renew (grandfathering).
          price_minor bigint NOT NULL CHECK (price_minor >= 0),
          currency char(3) NOT NULL DEFAULT 'INR',
          started_at timestamptz NOT NULL DEFAULT now(),
          current_period_end timestamptz NOT NULL,
          ended_at timestamptz,
          is_current boolean NOT NULL DEFAULT true,
          created_by_user_id uuid REFERENCES app_user (id) ON DELETE SET NULL,
          note text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_subscription_period CHECK (current_period_end > started_at),
          CONSTRAINT ck_subscription_current_open CHECK (NOT is_current OR ended_at IS NULL)
        );
        CREATE UNIQUE INDEX uq_subscription_current ON subscription (hotel_id) WHERE is_current;
        CREATE INDEX ix_subscription_hotel ON subscription (hotel_id, started_at DESC);

        CREATE TABLE hotel_entitlement_override (
          hotel_id uuid NOT NULL REFERENCES hotel (id) ON DELETE CASCADE,
          feature_key varchar(48) NOT NULL REFERENCES feature_catalog (key) ON DELETE CASCADE,
          flag_value boolean,
          limit_value integer CHECK (limit_value >= 0),
          reason text NOT NULL CHECK (char_length(reason) >= 3),
          set_by_user_id uuid REFERENCES app_user (id) ON DELETE SET NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (hotel_id, feature_key)
        );

        -- One row per signed-in device. Refresh tokens hang off it as a chain so a
        -- replayed old token can be recognised (vethra_api overwrote the hash and could not).
        CREATE TABLE auth_session (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
          hotel_id uuid REFERENCES hotel (id) ON DELETE CASCADE,
          kind varchar(8) NOT NULL CHECK (kind IN ('staff', 'platform')),
          device_label varchar(128),
          platform varchar(16) NOT NULL,
          app_version varchar(32),
          ip varchar(64),
          created_at timestamptz NOT NULL DEFAULT now(),
          last_seen_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          revoke_reason varchar(64),
          CONSTRAINT ck_auth_session_kind CHECK ((kind = 'platform') = (hotel_id IS NULL))
        );
        CREATE INDEX ix_auth_session_user_live ON auth_session (user_id) WHERE revoked_at IS NULL;
        CREATE INDEX ix_auth_session_hotel_user_live ON auth_session (hotel_id, user_id)
          WHERE revoked_at IS NULL;

        CREATE TABLE refresh_token (
          token_hash char(64) PRIMARY KEY,
          session_id uuid NOT NULL REFERENCES auth_session (id) ON DELETE CASCADE,
          issued_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          used_at timestamptz
        );
        CREATE INDEX ix_refresh_token_session ON refresh_token (session_id);

        CREATE TABLE invite_token (
          token_hash char(64) PRIMARY KEY,
          hotel_id uuid NOT NULL REFERENCES hotel (id) ON DELETE CASCADE,
          user_id uuid NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
          created_by_user_id uuid REFERENCES app_user (id) ON DELETE SET NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          used_at timestamptz,
          revoked_at timestamptz
        );
        CREATE INDEX ix_invite_open ON invite_token (hotel_id, user_id)
          WHERE used_at IS NULL AND revoked_at IS NULL;

        CREATE TABLE platform_audit_log (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          actor_user_id uuid REFERENCES app_user (id) ON DELETE SET NULL,
          action varchar(64) NOT NULL,
          entity_type varchar(48) NOT NULL,
          entity_id uuid,
          hotel_id uuid REFERENCES hotel (id) ON DELETE RESTRICT,
          before_json jsonb,
          after_json jsonb,
          ip varchar(64),
          note text,
          occurred_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_platform_audit_occurred ON platform_audit_log (occurred_at DESC, id DESC);
        CREATE INDEX ix_platform_audit_hotel ON platform_audit_log (hotel_id, occurred_at DESC);
        """
    )

    # ---- hotel-scoped tables (RLS) --------------------------------------------------
    op.execute(
        """
        CREATE TABLE hotel_tax_profile (
          hotel_id uuid PRIMARY KEY REFERENCES hotel (id) ON DELETE RESTRICT,
          gst_registration varchar(16) NOT NULL
            CHECK (gst_registration IN ('regular', 'composition', 'unregistered')),
          gstin char(15),
          legal_name varchar(200) NOT NULL,
          state_code char(2) NOT NULL CHECK (state_code ~ '^[0-9]{2}$'),
          service_charge_applies boolean NOT NULL DEFAULT false,
          service_charge_bp integer NOT NULL DEFAULT 0
            CHECK (service_charge_bp BETWEEN 0 AND 2000),
          invoice_prefix varchar(16) NOT NULL DEFAULT 'INV'
            CHECK (invoice_prefix ~ '^[A-Z0-9/-]{1,16}$'),
          fy_start_month smallint NOT NULL DEFAULT 4 CHECK (fy_start_month BETWEEN 1 AND 12),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          -- Regular and composition taxpayers have a GSTIN; unregistered ones do not.
          CONSTRAINT ck_tax_gstin_presence CHECK ((gst_registration = 'unregistered') = (gstin IS NULL)),
          -- The first two GSTIN digits are the state code.
          CONSTRAINT ck_tax_gstin_state CHECK (gstin IS NULL OR substr(gstin, 1, 2) = state_code),
          CONSTRAINT ck_tax_service_charge CHECK (service_charge_applies OR service_charge_bp = 0)
        );

        CREATE TABLE outlet (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          hotel_id uuid NOT NULL REFERENCES hotel (id) ON DELETE RESTRICT,
          name varchar(120) NOT NULL,
          code varchar(24) NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{0,23}$'),
          kind varchar(16) NOT NULL DEFAULT 'restaurant'
            CHECK (kind IN ('restaurant', 'bar', 'cafe', 'room_service', 'banquet')),
          status varchar(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
          kitchen_mode varchar(16) NOT NULL DEFAULT 'admin_print'
            CHECK (kitchen_mode IN ('admin_print', 'auto_assign', 'chef_accept')),
          require_accept boolean NOT NULL DEFAULT false,
          max_queue_orders smallint NOT NULL DEFAULT 3 CHECK (max_queue_orders BETWEEN 1 AND 20),
          rating_band_minutes smallint NOT NULL DEFAULT 5 CHECK (rating_band_minutes BETWEEN 0 AND 30),
          qr_confirm_required boolean NOT NULL DEFAULT true,
          ratings_enabled boolean NOT NULL DEFAULT false,
          business_day_cutoff time NOT NULL DEFAULT '04:00',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_outlet_hotel_code UNIQUE (hotel_id, code)
        );
        CREATE INDEX ix_outlet_hotel_status ON outlet (hotel_id, status, name);

        -- System roles have hotel_id NULL and are readable by every hotel; custom roles
        -- belong to one hotel. Permission strings are validated against the code registry.
        CREATE TABLE role (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          hotel_id uuid REFERENCES hotel (id) ON DELETE RESTRICT,
          code varchar(32) NOT NULL CHECK (code ~ '^[a-z][a-z0-9_]{1,31}$'),
          name varchar(80) NOT NULL,
          description text,
          permissions text[] NOT NULL DEFAULT '{}',
          is_system boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_role_system CHECK (is_system = (hotel_id IS NULL))
        );
        CREATE UNIQUE INDEX uq_role_system_code ON role (code) WHERE hotel_id IS NULL;
        CREATE UNIQUE INDEX uq_role_hotel_code ON role (hotel_id, code) WHERE hotel_id IS NOT NULL;

        -- outlet_ids NULL = all outlets. Outlets are archived, never deleted, so the array
        -- cannot dangle; ids are validated against the hotel's outlets on write.
        CREATE TABLE membership (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          hotel_id uuid NOT NULL REFERENCES hotel (id) ON DELETE RESTRICT,
          user_id uuid NOT NULL REFERENCES app_user (id) ON DELETE RESTRICT,
          role_id uuid NOT NULL REFERENCES role (id) ON DELETE RESTRICT,
          outlet_ids uuid[],
          status varchar(16) NOT NULL DEFAULT 'invited'
            CHECK (status IN ('invited', 'active', 'disabled')),
          invited_by_user_id uuid REFERENCES app_user (id) ON DELETE SET NULL,
          -- Set when the person accepts. Re-enabling a disabled member who never accepted
          -- puts them back to 'invited', not 'active': consent is per hotel.
          joined_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_membership_hotel_user UNIQUE (hotel_id, user_id),
          CONSTRAINT ck_membership_active_joined CHECK (status <> 'active' OR joined_at IS NOT NULL),
          CONSTRAINT ck_membership_outlets CHECK (outlet_ids IS NULL OR cardinality(outlet_ids) > 0)
        );
        CREATE INDEX ix_membership_hotel_created ON membership (hotel_id, created_at DESC, id DESC);
        CREATE INDEX ix_membership_user ON membership (user_id);
        CREATE INDEX ix_membership_role ON membership (role_id);

        CREATE TABLE audit_log (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          hotel_id uuid NOT NULL REFERENCES hotel (id) ON DELETE RESTRICT,
          actor_user_id uuid REFERENCES app_user (id) ON DELETE SET NULL,
          actor_kind varchar(8) NOT NULL DEFAULT 'staff'
            CHECK (actor_kind IN ('staff', 'platform', 'system')),
          action varchar(64) NOT NULL,
          entity_type varchar(48) NOT NULL,
          entity_id uuid,
          before_json jsonb,
          after_json jsonb,
          ip varchar(64),
          note text,
          occurred_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_audit_hotel_occurred ON audit_log (hotel_id, occurred_at DESC, id DESC);
        """
    )

    # ---- integrity triggers ---------------------------------------------------------
    op.execute(
        """
        -- A flag feature needs flag_value; a limit feature must not carry one.
        CREATE FUNCTION check_feature_value() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE k text;
        BEGIN
          SELECT kind INTO k FROM feature_catalog WHERE key = NEW.feature_key;
          IF k = 'flag' AND (NEW.flag_value IS NULL OR NEW.limit_value IS NOT NULL) THEN
            RAISE EXCEPTION 'feature % is a flag: set flag_value only', NEW.feature_key
              USING ERRCODE = 'check_violation';
          ELSIF k = 'limit' AND NEW.flag_value IS NOT NULL THEN
            RAISE EXCEPTION 'feature % is a limit: set limit_value only (NULL = unlimited)',
              NEW.feature_key USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_plan_feature_value BEFORE INSERT OR UPDATE ON plan_feature
          FOR EACH ROW EXECUTE FUNCTION check_feature_value();
        CREATE TRIGGER trg_override_value BEFORE INSERT OR UPDATE ON hotel_entitlement_override
          FOR EACH ROW EXECUTE FUNCTION check_feature_value();

        -- A membership's role must be a system role or a role of the same hotel. The FK
        -- alone would accept another hotel's role id (FK checks ignore RLS). This runs
        -- under RLS, so another hotel's role is simply not found.
        CREATE FUNCTION check_membership_role() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE role_hotel uuid; found_role boolean;
        BEGIN
          SELECT true, hotel_id INTO found_role, role_hotel FROM role WHERE id = NEW.role_id;
          IF found_role IS NULL OR (role_hotel IS NOT NULL AND role_hotel <> NEW.hotel_id) THEN
            RAISE EXCEPTION 'role % is not available to hotel %', NEW.role_id, NEW.hotel_id
              USING ERRCODE = 'foreign_key_violation';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_membership_role BEFORE INSERT OR UPDATE OF role_id, hotel_id
          ON membership FOR EACH ROW EXECUTE FUNCTION check_membership_role();

        -- Entitlement cache invalidation (see module docstring).
        CREATE FUNCTION bump_entitlements_for_hotel() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          UPDATE hotel SET entitlements_version = entitlements_version + 1
           WHERE id = COALESCE(NEW.hotel_id, OLD.hotel_id);
          RETURN NULL;
        END $$;
        CREATE FUNCTION bump_entitlements_for_all() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          -- Statement-level: one UPDATE per statement, however many rows changed.
          -- Over-invalidating is only a cache miss; plan and catalogue edits are rare.
          UPDATE hotel SET entitlements_version = entitlements_version + 1;
          RETURN NULL;
        END $$;
        CREATE TRIGGER trg_subscription_entitlements
          AFTER INSERT OR UPDATE OR DELETE ON subscription
          FOR EACH ROW EXECUTE FUNCTION bump_entitlements_for_hotel();
        CREATE TRIGGER trg_override_entitlements
          AFTER INSERT OR UPDATE OR DELETE ON hotel_entitlement_override
          FOR EACH ROW EXECUTE FUNCTION bump_entitlements_for_hotel();
        CREATE TRIGGER trg_plan_feature_entitlements
          AFTER INSERT OR UPDATE OR DELETE ON plan_feature
          FOR EACH STATEMENT EXECUTE FUNCTION bump_entitlements_for_all();
        CREATE TRIGGER trg_catalog_entitlements
          AFTER INSERT OR UPDATE OR DELETE ON feature_catalog
          FOR EACH STATEMENT EXECUTE FUNCTION bump_entitlements_for_all();
        """
    )
    for table in [
        "hotel",
        "app_user",
        "plan",
        "feature_catalog",
        "subscription",
        "hotel_entitlement_override",
        "hotel_tax_profile",
        "outlet",
        "role",
        "membership",
    ]:
        op.execute(
            f"CREATE TRIGGER trg_{table}_touch BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION touch_updated_at()"
        )

    # ---- seed data (before RLS, so the owner can insert system rows) ----------------
    for code, name, perms in SYSTEM_ROLES:
        perm_array = "ARRAY[" + ", ".join(f"'{p}'" for p in perms) + "]::text[]"
        op.execute(
            "INSERT INTO role (id, hotel_id, code, name, permissions, is_system) "
            f"VALUES ('{_rid(code)}', NULL, '{code}', '{name}', {perm_array}, true)"
        )
    for order, (key, kind, desc, default) in enumerate(FEATURES):
        flag = _sql_literal(default) if kind == "flag" else "NULL"
        limit = _sql_literal(default) if kind == "limit" else "NULL"
        op.execute(
            "INSERT INTO feature_catalog (key, kind, description, default_flag, default_limit, "
            f"sort_order) VALUES ('{key}', '{kind}', '{desc}', {flag}, {limit}, {order})"
        )
    kinds = {key: kind for key, kind, _d, _v in FEATURES}
    for code, name, monthly, annual, order in PLANS:
        op.execute(
            "INSERT INTO plan (code, name, price_monthly_minor, price_annual_minor, "
            f"is_placeholder, sort_order) VALUES ('{code}', '{name}', {monthly}, {annual}, "
            f"true, {order})"
        )
        for key, value in PLAN_FEATURES[code].items():
            flag = _sql_literal(value) if kinds[key] == "flag" else "NULL"
            limit = _sql_literal(value) if kinds[key] == "limit" else "NULL"
            op.execute(
                "INSERT INTO plan_feature (plan_id, feature_key, flag_value, limit_value) "
                f"SELECT id, '{key}', {flag}, {limit} FROM plan WHERE code = '{code}'"
            )

    # ---- row-level security ---------------------------------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY hotel_isolation ON {table} "
            "USING (hotel_id = app_hotel_id()) WITH CHECK (hotel_id = app_hotel_id())"
        )
    # PERMISSIVE policies are OR-ed. Both below only widen SELECT, never writes.
    op.execute("CREATE POLICY role_system_read ON role FOR SELECT USING (hotel_id IS NULL)")
    # Login must find a user's memberships before the hotel is known. Keyed on
    # app.user_id, which is set only after the password has been verified, and it
    # exposes only that user's own rows (vethra_api 0013).
    op.execute(
        "CREATE POLICY membership_self_read ON membership FOR SELECT "
        "USING (user_id = app_user_id())"
    )

    # Audit logs are append-only for the application role.
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hotel_app') THEN
            REVOKE UPDATE, DELETE ON audit_log, platform_audit_log FROM hotel_app;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for table in reversed(UNSCOPED_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute(
        "DROP FUNCTION IF EXISTS check_feature_value, check_membership_role, "
        "bump_entitlements_for_hotel, bump_entitlements_for_all, touch_updated_at, "
        "app_hotel_id, app_user_id CASCADE"
    )
