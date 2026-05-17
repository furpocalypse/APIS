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

# S34: single-locus migration. `migrate` runs ONLY on the web entrypoint,
# never the worker (the worker waits on DB readiness and must not race a
# schema change). Even among web replicas it is gated on
# APIS_RUN_MIGRATIONS (default "1" so the single-container compose/dev
# case Just Works); a scaled deployment runs migrate exactly once from a
# dedicated init job / one designated locus and sets APIS_RUN_MIGRATIONS=0
# on every app + worker replica. This removes the start.sh:15 cold-start
# race where web AND worker both ran `migrate` concurrently.

case ${1:-} in
    worker)
        # No migrate here by design (S34). Celery tasks are idempotent /
        # retry; a brief wait for the web locus to finish migrating is
        # preferable to a second concurrent migrator.
        exec celery -A fm_eventmanager worker --loglevel=info
        ;;

    *)
        if [ "${APIS_RUN_MIGRATIONS:-1}" = "1" ]; then
            ./manage.py migrate
        fi

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
