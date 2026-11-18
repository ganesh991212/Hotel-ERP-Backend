#!/bin/sh
# Container entrypoint. ONE process on purpose: the realtime hub is in-memory (docs/10-deployment.md, "Scaling limit").
set -eu
if [ "${RUN_RELEASE_ON_START:-false}" = "true" ]; then
  sh scripts/release.sh
fi
exec uvicorn app.main:app \
  --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers 1 \
  --proxy-headers --forwarded-allow-ips "*"
