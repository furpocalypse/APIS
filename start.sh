#!/bin/sh
# APIS app container entrypoint.
#
# Runs migrations, then execs either gunicorn (web) or celery (worker).
# nginx is no longer in this image — it lives in apis-nginx (see
# nginx/Dockerfile) and fronts this container in the docker-compose
# topology. AKS uses AGIC, hitting gunicorn on :8000 directly.
#
# gunicorn binds TCP :8000 so:
#   - the apis-nginx sidecar can reach it on the docker-compose network
#   - AGIC / a Service / kubectl port-forward can reach it in AKS
#   - tests / `docker run -p 8000:8000` work without compose
set -eu

./manage.py migrate

case ${1:-} in
    worker)
        exec celery -A fm_eventmanager worker --loglevel=info
        ;;

    *)
        # --forwarded-allow-ips=* is safe because the only writer of the
        # X-Forwarded-* headers we trust is whatever sits in front of this
        # listener (apis-nginx, AGIC, a test harness). Real-IP scrubbing
        # at that hop is the security boundary. See ASVS V13.1.4.
        exec gunicorn fm_eventmanager.asgi:application \
            -k fm_eventmanager.worker.ApisWorker \
            --bind 0.0.0.0:8000 \
            --forwarded-allow-ips=*
        ;;
esac
