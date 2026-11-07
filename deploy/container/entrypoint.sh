#!/bin/sh
# Entry point of the mpmv-app image (ARC.5). The first argument selects the process type:
#   web      gunicorn on $MPMV_HTTP_PORT (default)
#   worker   background tasks and periodic jobs
#   migrate  apply database migrations (a dedicated deploy step, never at web start-up)
#   manage   any Django management command: `manage check --deploy`
# Anything else is executed as is.
set -eu

role="${1:-web}"
if [ "$#" -gt 0 ]; then shift; fi

case "$role" in
  web)
    exec gunicorn mpmv.wsgi:application \
      --bind "0.0.0.0:${MPMV_HTTP_PORT:-8000}" \
      --workers "${WEB_CONCURRENCY:-3}" \
      --worker-tmp-dir /dev/shm \
      --access-logfile - \
      --error-logfile - \
      "$@"
    ;;
  worker)
    # Placeholder until ARC.9 (Procrastinate, ADR-0004) is merged. The container must stay up so
    # the stack and the Quadlet units already have their final shape; ARC.9 replaces this line
    # with the worker command (expected: `exec python manage.py procrastinate worker`).
    echo "mpmv worker: no task queue yet (ARC.9); idling." >&2
    exec sleep infinity
    ;;
  migrate)
    exec python manage.py migrate --noinput "$@"
    ;;
  manage)
    exec python manage.py "$@"
    ;;
  *)
    exec "$role" "$@"
    ;;
esac
