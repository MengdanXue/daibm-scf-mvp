#!/usr/bin/env sh
# Restore a backup into the running compose stack and prove it:
# every file hash is checked first; afterwards row counts, the schema revision
# and the ledger head hash must equal the manifest and the ledger chain must
# verify, otherwise the command fails.
#   scripts/ops/restore.sh <name> --yes
set -eu
cd "$(dirname "$0")/../.."
NAME="${1:?usage: scripts/ops/restore.sh <backup name> --yes}"
if [ "${2:-}" != "--yes" ]; then
  echo "Restore replaces every table of the running database; pass --yes." >&2
  exit 2
fi
PROJECT="${COMPOSE_PROJECT_NAME:-daibm-scf-mvp}"
docker compose -p "$PROJECT" exec -T mvp python -m app.ops.backup verify "/backups/$NAME"
docker compose -p "$PROJECT" exec -T mvp \
  python -m app.ops.backup restore "/backups/$NAME" \
  --artifacts /app/artifacts/candidates/calibration --yes
# Sessions and caches of the running process are discarded by a restart.
docker compose -p "$PROJECT" restart mvp
echo "[DAIBM-SCF] Restore of $NAME verified."
