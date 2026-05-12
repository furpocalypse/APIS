# SHA-pinned per CIS Docker 4.2 / OWASP A08. The mutable `node:lts` tag
# means a compromised registry / hijacked tag swaps the base on next
# rebuild. Pinning to a digest binds the build to exactly the image bytes
# that produced this commit. Refresh on a deliberate cadence (monthly)
# alongside Trivy scan results, NOT on every image bump.
#
# Using the `-slim` variant rather than the full image: the Vite build
# only needs Node + npm; the full `node:lts` ships ~150 MB of extra
# Debian packages (and their CVEs). Nothing from this stage ships to
# the runtime image — only the built static files (assets, JS bundle)
# are copied across via `COPY --from=assets`.
#   Resolved version at pin time: Node 24.15.0 (current `lts` channel,
#   slim variant on Debian trixie).
FROM node:lts-slim@sha256:24dc26ef1e3c3690f27ebc4136c9c186c3133b25563ae4d7f0692e4d1fe5db0e AS assets

ENV NODE_ENVIRONMENT=production

WORKDIR /app/registration/frontend

COPY ./registration/frontend/package.json ./registration/frontend/package-lock.json /app/registration/frontend/
# `npm ci` (instead of `npm install`) requires the lockfile to agree with
# package.json and refuses to mutate the lockfile under any circumstance —
# the right behaviour for a reproducible production build. CIS Docker 4.3
# / OWASP A08. `--no-audit --no-fund` cuts unnecessary network calls; we
# enforce dependency hygiene via Trivy in CI, not the audit-on-install
# nag that npm prints.
RUN npm ci --no-audit --no-fund
COPY ./registration/frontend/ /app/registration/frontend/
RUN npm run build

# Runtime base. SHA-pinned (CIS Docker 4.2 / OWASP A08). Same refresh
# cadence as the Node pin above — bump deliberately, with Trivy scan in
# the loop. `slim-trixie` is Debian 13 (trixie) slim; this is the base
# Docker offered as `python:3.14-slim` at pin time.
#   Resolved version at pin time: Python 3.14.4.
FROM python:3.14-slim-trixie@sha256:1697e8e8d39bf168e177ac6b5fdab6df86d81cfc24dae17dfb96cfc3ef76b4dd

LABEL org.opencontainers.image.source="https://github.com/furthemore/APIS"

ARG SENTRY_RELEASE=local
ENV SENTRY_RELEASE=${SENTRY_RELEASE}
ENV PATH="/app/.venv/bin:$PATH"
# 8000: gunicorn (TCP). 81: prometheus /metrics. nginx is no longer baked
# into this image — it lives in furpocalypse.azurecr.io/apis-nginx and
# fronts this container in the docker-compose topology. AKS uses AGIC
# (ingress controller) instead, hitting 8000 directly.
EXPOSE 8000 81

RUN useradd --shell /bin/bash --create-home --home /app --uid 1000 apis

WORKDIR /app

# Single RUN: install runtime + build apt deps, purge auto-removed
# packages, drop the apt lists / caches, and neutralize SUID/SGID bits
# on every binary in the image. The container runs as a single non-root
# user (apis, uid 1000); nothing it does requires setuid privilege
# escalation, so removing the bits closes a privilege-escalation
# primitive that's only useful to an attacker who already has code
# execution. (CIS Docker 4.3, 4.5; OWASP A05.)
#
# `git` is required at build time because pyproject.toml pulls
# django-prometheus from a Git rev (no Django-6 PyPI release yet — see
# the comment in pyproject.toml). Once P1 #9 lands and we move uv sync
# to a separate builder stage, git will be dropped from the runtime
# layer entirely. Healthcheck (Step 1) uses urllib from the Python
# stdlib, so no curl is needed.
RUN set -eux; \
    apt-get update; \
    apt-get install --no-install-recommends -y \
        git; \
    apt-get autoremove --purge -y; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* /var/tmp/*; \
    find / -xdev \( -perm -4000 -o -perm -2000 \) \
        -exec chmod -s {} + 2>/dev/null || true; \
    # /var/mail is created by useradd's MAIL_DIR default and is unused
    rm -rf /var/mail /var/spool/mail

COPY --from=ghcr.io/astral-sh/uv:0.9.29 /uv /uvx /bin/

USER apis

RUN --mount=type=cache,mode=0755,uid=1000,target=/app/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project

COPY --chown=apis . /app/
COPY --from=assets --chown=apis /app/registration/static/ /app/registration/static/

RUN --mount=type=cache,mode=0755,uid=1000,target=/app/.cache/uv \
    uv sync --frozen

# DEBUG=True for this RUN only — collectstatic doesn't serve HTTP and so
# doesn't need a real ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS, but the
# settings module's DEBUG=False branch (introduced for prod hardening)
# does require those. Setting DEBUG=True scopes the relaxation to this
# single build step; runtime env still drives behavior in the running
# container.
RUN DJANGO_SECRET_KEY=collectstatic DJANGO_DEBUG=True ./manage.py collectstatic --noinput

# CIS Docker Benchmark 4.6 — declare a HEALTHCHECK so orchestrators can
# distinguish "container running" from "app actually responding". Uses
# Python (already present in the image) instead of pulling curl into the
# runtime layer. Targets gunicorn directly on :8000 (apis-nginx is in a
# separate container now). /robots.txt because:
#   - it doesn't touch the database (so a DB blip doesn't kill the pod);
#   - X-Forwarded-Proto: https clears SECURE_SSL_REDIRECT;
#   - X-Real-IP: 127.0.0.1 clears RequireClientIPMiddleware (the
#     ALLAUTH_TRUSTED_CLIENT_IP_HEADER default; without it the probe
#     would 403 with DEBUG=False because the loopback request bypasses
#     apis-nginx, which is normally the writer of X-Real-IP);
#   - Host: healthcheck.internal clears Django's ALLOWED_HOSTS check
#     without requiring the operator to put 127.0.0.1 in their prod
#     ALLOWED_HOSTS. settings.py unconditionally allowlists that name;
#     it's unreachable from outside the container so it's not a leak.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", \
"import sys, urllib.request; \
req = urllib.request.Request('http://127.0.0.1:8000/robots.txt', \
                             headers={'Host':'healthcheck.internal','X-Forwarded-Proto':'https','X-Forwarded-For':'127.0.0.1','X-Real-IP':'127.0.0.1'}); \
sys.exit(0 if urllib.request.urlopen(req, timeout=4).status == 200 else 1)"]

ENTRYPOINT ["/app/start.sh"]
