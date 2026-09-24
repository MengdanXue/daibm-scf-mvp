#!/usr/bin/env sh
# Create .env with random secrets when it does not exist yet. Idempotent.
set -eu
cd "$(dirname "$0")/../.."
if [ -f .env ]; then
  echo "[DAIBM-SCF] .env already exists; leaving it unchanged."
  exit 0
fi
rand() { python3 -c "import secrets; print(secrets.token_urlsafe($1))"; }
umask 077
sed \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand 24)|" \
  -e "s|^DAIBM_DEMO_PASSWORD=.*|DAIBM_DEMO_PASSWORD=Demo-$(rand 9)-7a!|" \
  -e "s|^DAIBM_METRICS_TOKEN=.*|DAIBM_METRICS_TOKEN=$(rand 24)|" \
  .env.example > .env
echo "[DAIBM-SCF] Created .env with random secrets."
grep '^DAIBM_DEMO_PASSWORD=' .env
