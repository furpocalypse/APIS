#!/usr/bin/env bash
# Bring up docker-compose services, migrate, seed, and launch a Django dev
# server with E2E_MODE=1 in the background.
#
# Exits 0 once the server is answering HTTP on ${APIS_E2E_PORT:-8000}. The
# server PID is written to e2e/playwright/.server.pid so down.sh can reap it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
PIDFILE="$HERE/../.server.pid"
LOGFILE="$HERE/../server.log"
PORT="${APIS_E2E_PORT:-8000}"
HOST="${APIS_E2E_HOST:-127.0.0.1}"

cd "$REPO"

# Bring up backing services via the Makefile so the polling/health logic is
# in one place. Respect SKIP_DOCKER_SERVICES / CI to avoid clashing with
# GitHub Actions service containers.
if [[ "${SKIP_DOCKER_SERVICES:-${CI:+1}}" != "1" ]]; then
  echo ">>> make services-up"
  make -C "$REPO" services-up
fi

# Reuse Makefile TEST_ENV values so this script mirrors ``make test``.
export DJANGO_SETTINGS_MODULE=fm_eventmanager.settings_test
export DJANGO_SECRET_KEY=test
export DJANGO_DEBUG=1
export E2E_MODE=1
export PAYPAL_CLIENT_ID="${PAYPAL_CLIENT_ID:-test}"
export PAYPAL_CLIENT_SECRET="${PAYPAL_CLIENT_SECRET:-test}"
export PAYPAL_WEBHOOK_ID="${PAYPAL_WEBHOOK_ID:-test-webhook}"
export SQUARE_APPLICATION_ID="${SQUARE_APPLICATION_ID:-test}"
export SQUARE_ACCESS_TOKEN="${SQUARE_ACCESS_TOKEN:-test}"
export SQUARE_LOCATION_ID="${SQUARE_LOCATION_ID:-test}"
export CSRF_TRUSTED_ORIGINS='http://*,https://*'
export DATABASE_HOST="${TEST_DATABASE_HOST:-127.0.0.1}"
export DATABASE_PORT="${TEST_DATABASE_PORT:-5432}"
export DATABASE_USER="${TEST_DATABASE_USER:-apis}"
export DATABASE_PASS="${TEST_DATABASE_PASS:-secret}"
export DATABASE_NAME="${TEST_DATABASE_NAME:-apis_e2e}"
export DJANGO_DATABASE_POOL=False
export DJANGO_REDIS_URL="redis://${TEST_REDIS_HOST:-127.0.0.1}:${TEST_REDIS_PORT:-6379}/3"
export CELERY_BROKER_URL="redis://${TEST_REDIS_HOST:-127.0.0.1}:${TEST_REDIS_PORT:-6379}/4"
export CELERY_RESULT_BACKEND="redis://${TEST_REDIS_HOST:-127.0.0.1}:${TEST_REDIS_PORT:-6379}/4"
export IDEMPOTENCY_KEY_LOCK_LOCATION="redis://${TEST_REDIS_HOST:-127.0.0.1}:${TEST_REDIS_PORT:-6379}"
export STATIC_ROOT="$REPO/build/e2e-static"
export MAINTENANCE_MODE_STATE_FILE_PATH="$REPO/build/maintenance_mode_state.txt"
export GOTENBERG_HOST="http://127.0.0.1:3000"

mkdir -p "$STATIC_ROOT" "$(dirname "$LOGFILE")"

echo ">>> creating database if needed"
PGPASSWORD="$DATABASE_PASS" psql \
  -h "$DATABASE_HOST" -p "$DATABASE_PORT" -U "$DATABASE_USER" \
  -tc "SELECT 1 FROM pg_database WHERE datname='$DATABASE_NAME'" | grep -q 1 \
  || PGPASSWORD="$DATABASE_PASS" createdb \
       -h "$DATABASE_HOST" -p "$DATABASE_PORT" -U "$DATABASE_USER" \
       "$DATABASE_NAME"

echo ">>> building frontend bundle (Vite)"
( cd registration/frontend && npm install --no-audit --no-fund && npm run build )

echo ">>> collectstatic"
uv run python manage.py collectstatic --noinput >/dev/null

echo ">>> migrating"
uv run python manage.py migrate --noinput

echo ">>> seeding reference data"
uv run python manage.py e2e_seed

echo ">>> launching server on ${HOST}:${PORT}"
nohup uv run python manage.py runserver --noreload "${HOST}:${PORT}" \
  > "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"

# Wait for the server to answer. Fail fast with logs on timeout.
for _ in $(seq 1 60); do
  if curl --silent --fail --max-time 2 "http://${HOST}:${PORT}/registration/" \
       >/dev/null 2>&1; then
    echo ">>> server is up"
    exit 0
  fi
  sleep 1
done

echo ">>> server failed to come up; last 50 log lines:"
tail -n 50 "$LOGFILE" || true
exit 1
