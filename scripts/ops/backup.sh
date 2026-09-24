#!/usr/bin/env sh
# Hash-verified backup of the running compose stack: database (every table),
# audit data (ledger + security events) and calibration artifacts.
#   scripts/ops/backup.sh [name]      -> ./backups/<name>/ (DAIBM_BACKUP_DIR)
set -eu
cd "$(dirname "$0")/../.."
NAME="${1:-daibm-$(date -u +%Y%m%dT%H%M%SZ)}"
PROJECT="${COMPOSE_PROJECT_NAME:-daibm-scf-mvp}"
docker compose -p "$PROJECT" exec -T mvp \
  python -m app.ops.backup create --out "/backups/$NAME" \
  --artifacts /app/artifacts/candidates/calibration
docker compose -p "$PROJECT" exec -T mvp python -m app.ops.backup verify "/backups/$NAME"
echo "[DAIBM-SCF] Backup written to ${DAIBM_BACKUP_DIR:-./backups}/$NAME"
