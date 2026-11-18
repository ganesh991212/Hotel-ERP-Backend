#!/bin/sh
# Schema migrations + idempotent seed (plans; platform admin if PLATFORM_ADMIN_EMAIL/PASSWORD are set).
# Production runs this as Render's pre-deploy command; staging (free plan) runs it from start.sh.
set -eu
alembic upgrade head
python -m app.seed
