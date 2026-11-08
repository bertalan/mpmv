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
    # No control socket: unused, and its default path is under the read-only /app (ARC.23).
    exec gunicorn mpmv.wsgi:application \
      --bind "0.0.0.0:${MPMV_HTTP_PORT:-8000}" \
      --workers "${WEB_CONCURRENCY:-3}" \
      --worker-tmp-dir /dev/shm \
      --no-control-socket \
      --access-logfile - \
      --error-logfile - \
      "$@"
    ;;
  worker)
    # Procrastinate worker (ARC.9, ADR-0004): queued jobs and periodic schedules.
    exec python manage.py procrastinate worker "$@"
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
