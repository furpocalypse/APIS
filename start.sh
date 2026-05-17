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

# S29 / S34: single-locus migration. Running `migrate` on every container
# (web AND worker, every replica) races N concurrent migrators on boot.
# It now runs only when APIS_RUN_MIGRATIONS is truthy (default "1" so the
# single-container compose/dev case Just Works); a scaled deployment runs
# migrate exactly once from an init job / one designated locus and sets
# APIS_RUN_MIGRATIONS=0 on the app + worker replicas.
if [ "${APIS_RUN_MIGRATIONS:-1}" = "1" ]; then
    ./manage.py migrate
fi

case ${1:-} in
    worker)
        exec celery -A fm_eventmanager worker --loglevel=info
        ;;

    *)
        # S29: --forwarded-allow-ips is no longer hardcoded to `*`.
        # S13 resolved to decision-table case (c) — the real Azure-LB
        # TCP peer is not empirically established in-repo, so the edge
        # boundary is NOT assumed airtight. FORWARDED_ALLOW_IPS is
        # env-driven, default loopback; the compose/AKS deployment sets
        # it to the apis-nginx sidecar / AGIC source. gunicorn only
        # matches exact peer IPs (no CIDR), so the authoritative trust
        # enforcement is the Django layer: RequireClientIPMiddleware
        # rejects any peer outside TRUSTED_PROXY_CIDRS (MED-13) and nginx
        # does its own realip origin-lock (T1). FORWARDED_ALLOW_IPS=* is
        # an explicit, documented opt-in — never the default. ASVS V13.1.4.
        exec gunicorn fm_eventmanager.asgi:application \
            -k fm_eventmanager.worker.ApisWorker \
            --bind 0.0.0.0:8000 \
            --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
        ;;
esac
