#!/bin/sh
# Local stack on Apple `container` (ARC.5, ADR-0002): a private network, a media volume, the app
# and the worker, next to the PostgreSQL container that `make db` runs. Same image as STAGE/LIVE.
#
# Usage: deploy/local/stack.sh up | down | reset | status | logs [app|worker]
#
# Safety: this script only creates, stops or deletes the resources named below (all mpmv-*).
# It never stops or deletes the database container or its volume; `make db-reset` does that.
set -eu

NETWORK=mpmv-net
APP=mpmv-app
WORKER=mpmv-worker
MEDIA_VOLUME=mpmv-media
DB=mpmv-db
IMAGE="${MPMV_IMAGE:-mpmv-app:dev}"
PORT="${MPMV_DEV_PORT:-8000}"

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
# Generated once per checkout, git-ignored (build/), keeps sessions valid across restarts.
ENV_FILE="$ROOT/build/dev-stack.env"
APP_UID=10001

die() {
  echo "stack: $*" >&2
  exit 1
}

db_address() {
  # Apple `container` 1.0 has no DNS between containers: resolve the database by its address.
  container inspect "$DB" | python3 -c '
import json, sys
networks = json.load(sys.stdin)[0]["status"]["networks"]
print(networks[0]["ipv4Address"].split("/")[0])'
}

ensure_env_file() {
  mkdir -p "$(dirname "$ENV_FILE")"
  (
    umask 077
    [ -f "$ENV_FILE" ] || printf 'DJANGO_SECRET_KEY=%s\n' \
      "$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')" >"$ENV_FILE"
    # Field encryption key (SEC.3), generated once: replacing it makes encrypted data unreadable.
    grep -q '^MPMV_FIELD_ENCRYPTION_KEYS=' "$ENV_FILE" || printf 'MPMV_FIELD_ENCRYPTION_KEYS=local:%s\n' \
      "$(python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')" \
      >>"$ENV_FILE"
  )
}

down() {
  container stop "$WORKER" >/dev/null 2>&1 || true
  container delete "$WORKER" >/dev/null 2>&1 || true
  container stop "$APP" >/dev/null 2>&1 || true
  container delete "$APP" >/dev/null 2>&1 || true
}

run_image() {
  # Shared settings for every process type. The app and the worker join the stack network and
  # the default one, where the database container lives.
  db_ip=$(db_address)
  container run --network "$NETWORK" --network default \
    --env-file "$ENV_FILE" \
    -e DJANGO_SETTINGS_MODULE=mpmv.settings.stage \
    -e "DATABASE_URL=postgres://mpmv@$db_ip:5432/mpmv" \
    -e DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
    -e DJANGO_SECURE_SSL_REDIRECT=false \
    "$@"
}

wait_healthy() {
  i=0
  while [ "$i" -lt 60 ]; do
    if container exec "$APP" python -m mpmv.healthcheck >/dev/null 2>&1; then
      echo "mpmv: app ready on http://127.0.0.1:$PORT/ (image $IMAGE)"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  die "app not healthy; see: container logs $APP"
}

up() {
  container inspect "$DB" >/dev/null 2>&1 || die "database not running: make db"
  container image inspect "$IMAGE" >/dev/null 2>&1 || die "image $IMAGE missing: make image"
  ensure_env_file

  container network inspect "$NETWORK" >/dev/null 2>&1 \
    || container network create "$NETWORK" >/dev/null
  if ! container volume inspect "$MEDIA_VOLUME" >/dev/null 2>&1; then
    container volume create "$MEDIA_VOLUME" >/dev/null
    # New volumes are root-owned ext4: hand the media root to the image's non-root user once.
    container run --rm --user 0 -v "$MEDIA_VOLUME:/data" "$IMAGE" \
      chown "$APP_UID:$APP_UID" /data
  fi

  down
  run_image --rm "$IMAGE" migrate
  run_image -d --name "$APP" --read-only --tmpfs /tmp \
    -p "127.0.0.1:$PORT:8000" -v "$MEDIA_VOLUME:/app/media" "$IMAGE" web >/dev/null
  run_image -d --name "$WORKER" --read-only --tmpfs /tmp "$IMAGE" worker >/dev/null
  wait_healthy
}

reset() {
  down
  container volume delete "$MEDIA_VOLUME" >/dev/null 2>&1 || true
  container network delete "$NETWORK" >/dev/null 2>&1 || true
  rm -f "$ENV_FILE"
}

status() {
  container list --all | awk 'NR == 1 || $1 ~ /^mpmv-/'
}

logs() {
  case "${1:-app}" in
    app) container logs --follow "$APP" ;;
    worker) container logs --follow "$WORKER" ;;
    *) die "logs: app or worker" ;;
  esac
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  reset) reset ;;
  status) status ;;
  logs) logs "${2:-app}" ;;
  *) die "usage: $0 up|down|reset|status|logs [app|worker]" ;;
esac
