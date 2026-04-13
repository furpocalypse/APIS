#!/bin/sh
set -eu

# Migrations are no longer run on every container start. In production they are
# applied by a one-shot ACA Job (`./start.sh migrate`) before web/worker
# replicas roll. For local docker-compose use, set RUN_MIGRATIONS_ON_START=true
# in your env to keep the old behavior.
if [ "${RUN_MIGRATIONS_ON_START:-false}" = "true" ]; then
    ./manage.py migrate --noinput
fi

case ${1:-web} in
    migrate)
        exec ./manage.py migrate --noinput
        ;;

    worker)
        exec celery -A fm_eventmanager worker --loglevel=info
        ;;

    web|*)
        exec /usr/bin/supervisord -c /app/supervisord.conf
        ;;
esac
