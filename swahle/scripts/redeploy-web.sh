#!/usr/bin/env bash
# Redeploy the live swahle-web container (plain `docker run` on the bridge
# network, NOT docker compose -- see swahle-web-deploy memory / DEPLOY.md).
# Secrets/config live in swahle/.env and are loaded via --env-file so nothing
# has to be retyped from memory on every redeploy.
set -euo pipefail
cd "$(dirname "$0")/../.."

docker compose -f swahle/docker-compose.yml build web
docker rm -f swahle-web 2>/dev/null || true

docker run -d \
  --name swahle-web \
  --restart unless-stopped \
  --network bridge \
  --add-host host.docker.internal:host-gateway \
  -p 8001:8000 \
  -v /mnt/nfs:/mnt/nfs \
  --env-file swahle/.env \
  swahle-web

echo "Redeployed swahle-web. Tailing logs (Ctrl-C to stop)..."
docker logs -f swahle-web
