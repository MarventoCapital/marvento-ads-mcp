#!/usr/bin/env bash
# Redeploy the Marvento Ads MCP stack from the latest main branch.
# Run on the droplet: sudo /opt/ads-mcp/src/deploy/update.sh
set -euo pipefail

SRC_DIR="${SRC_DIR:-/opt/ads-mcp/src}"

cd "$SRC_DIR"
BEFORE=$(git rev-parse --short HEAD)
git fetch --quiet origin
git reset --hard --quiet origin/main
AFTER=$(git rev-parse --short HEAD)
echo "source: $BEFORE -> $AFTER"

cd "$SRC_DIR/deploy"
[ -e .env ] || ln -sf /opt/ads-mcp/.env .env
# Security rebuild: do not reuse an older layer with a vulnerable CA store.
docker compose build --pull --no-cache mcp
docker compose pull caddy

# Validate the candidate image before touching the running service.
docker compose run --rm --no-deps --entrypoint python mcp -c "from importlib.metadata import version; assert tuple(map(int,version('pip').split('.'))) >= (26,2,2); assert tuple(map(int,version('certifi').split('.'))) >= (2026,10,19)"
docker compose run --rm --no-deps --entrypoint uv mcp --version

docker compose up -d --remove-orphans
docker image prune -f >/dev/null

echo "waiting for health..."
for i in $(seq 1 20); do
  status=$(docker inspect --format '{{.State.Health.Status}}' ads-mcp 2>/dev/null || echo starting)
  if [ "$status" = "healthy" ]; then
    echo "ads-mcp healthy at $AFTER"
    exit 0
  fi
  sleep 3
done
echo "ads-mcp did not become healthy; last logs:" >&2
docker logs --tail 50 ads-mcp >&2
exit 1
