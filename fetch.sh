#!/usr/bin/env bash
# Pull the server's live compose.yaml back into the repo, to catch drift
# from edits made directly on the box. Review with: git diff
set -euo pipefail

# Untracked local settings: PALWORLD_HOST, PALWORLD_DIR
if [ -f "$(dirname "$0")/.env.local" ]; then . "$(dirname "$0")/.env.local"; fi

HOST="${PALWORLD_HOST:?not set — put PALWORLD_HOST in .env.local or export it}"
DIR="${PALWORLD_DIR:-/opt/palworld}"

scp -q "${HOST}:${DIR}/compose.yaml" ./compose.yaml
echo "→ pulled from ${HOST}"
git diff --stat -- compose.yaml || true
