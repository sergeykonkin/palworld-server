#!/usr/bin/env bash
# Push compose.yaml to the server and apply it.
# .env is deliberately NOT synced — it holds the admin password and lives only there.
set -euo pipefail

# Untracked local settings: PALWORLD_HOST, PALWORLD_DIR
if [ -f "$(dirname "$0")/.env.local" ]; then . "$(dirname "$0")/.env.local"; fi

HOST="${PALWORLD_HOST:?not set — put PALWORLD_HOST in .env.local or export it}"
DIR="${PALWORLD_DIR:-/opt/palworld}"

echo "→ syncing compose.yaml to ${HOST}:${DIR}"
scp -q compose.yaml "${HOST}:${DIR}/compose.yaml"

echo "→ validating and applying"
ssh "${HOST}" "cd ${DIR} && docker compose config --quiet && docker compose up -d"

echo "→ waiting for health"
ssh "${HOST}" 'for i in $(seq 1 30); do
  s=$(docker inspect -f "{{.State.Health.Status}}" palworld-server 2>/dev/null || echo missing)
  [ "$s" = "healthy" ] && { echo "healthy"; exit 0; }
  sleep 5
done
echo "not healthy after 150s — check: docker compose logs --tail 50"
exit 1'
