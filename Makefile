
IMAGE	?= ghcr.io/furthemore/apis
TAG	?= $(shell git describe --tag --always)

all: help

define HELP

Commands:
	make docker-login               : Login to Github container repository
	make build-docker-image         : Make a local docker build of APIS
	make push-docker-image          : Push latest image to Github container repository
        make tag-stage                  : Tag image for deployment to stage server
        make tag-production             : Tag image for deployment to production

	make dev                        : Develop locally with Docker
	make dev-setup                  : Sets up a venv for local development
	make pre-commit-setup           : Installs (or updates) pre-commit hooks

	make lint                       : ruff check + ruff format --check (the CI gate)
	make lint-fix                   : ruff check --fix + ruff format (apply autofixes)
	make typecheck                  : Run mypy (django-stubs) over registration + fm_eventmanager
	make lint-pylint                : Run pylint (semantics + duplicate-code/R0801)
	make lint-vulture               : Run vulture (dead-code detection)
	make lint-all                   : Run every linter (ruff, mypy, pylint, vulture)

	make makemigrations             : Create new Django migrations (host-uv, dev DB)
	make migrate                    : Apply Django migrations (host-uv, dev DB)
	make createsuperuser            : Create a Django superuser (host-uv, dev DB, interactive)
	make bootstrap-admin            : Idempotent first-run admin provisioning (interactive)
	make bootstrap-admin-from-env   : Idempotent first-run admin provisioning (from BOOTSTRAP_ADMIN_* env)

	make dev-makemigrations         : Create new Django migrations (inside `make dev` container)
	make dev-migrate                : Apply Django migrations (inside `make dev` container)
	make dev-createsuperuser        : Create a Django superuser (inside `make dev` container, interactive)

	make test                       : Run Django + Playwright end-to-end suites (full regression gate)
	make test-django                : Run only the Django test suite
	make test-paypal                : Run only PayPal-tagged tests (uses uv)
	make test-coverage              : Run Django test suite under coverage; emit htmlcov/
	make test-frontend              : Run Vitest suite in registration/frontend
	make test-frontend-coverage     : Run Vitest with v8 coverage in registration/frontend
	make test-all                   : Run Django tests (with coverage) + frontend tests
	make test-check-migrations      : Fail if branch has model changes without a migration
	make test-build-frontend        : Build the Vite front-end bundle (npm install + npm run build)
	make test-collectstatic         : Populate the staticfiles manifest tests depend on

	make e2e-setup                  : Install Playwright + browsers under e2e/playwright
	make e2e                        : Run Playwright suite (spins up + tears down a local server)
	make e2e-smoke                  : Run @smoke-tagged Playwright tests only
	make e2e-ui                     : Open Playwright interactive UI

	make services-up                : Start postgres + redis + gotenberg via docker compose and wait until ready
	make services-down              : Stop those services (volumes preserved)

endef
export HELP

help:
	@echo "$${HELP}"

docker-login:
	@[ "${GITHUB_USER}" ] || ( echo ">> GITHUB_USER is not set, check out envrc.example"; exit 1 )
	@[ "${GITHUB_CR_PAT}" ] || ( echo ">> GITHUB_CR_PAT is not set, check out envrc.example"; exit 1 )
	@echo $(GITHUB_CR_PAT) | docker login ghcr.io -u $(GITHUB_USER) --password-stdin

build-docker-image:
	# tag the current latest as previous, and replace it
	-docker tag $(IMAGE):latest $(IMAGE):previous

	# build and tag new container
	docker build \
		--file Dockerfile \
		--cache-from $(IMAGE):latest \
		--cache-from $(IMAGE):production \
		--build-arg SENTRY_RELEASE=$(TAG) \
		--tag $(IMAGE):$(TAG) \
		.

	docker tag $(IMAGE):$(TAG) $(IMAGE):latest

tag-stage:
	docker tag $(IMAGE):$(TAG) $(IMAGE):stage
	docker push $(IMAGE):stage

tag-production:
	docker tag $(IMAGE):$(TAG) $(IMAGE):production
	docker push $(IMAGE):production

push-docker-image:
	docker push $(IMAGE):$(TAG)
	docker push $(IMAGE):latest

dev:
	-docker-compose up -d
	docker-compose exec app /bin/bash -c 'DJANGO_DEBUG=1 python /app/manage.py runserver_plus 0.0.0.0:8000'

dev-setup:
	uv sync
	@echo "Settings are checked into fm_eventmanager/settings.py (single canonical file)."
	@echo "Copy .env.dev.example to .env and adjust if needed; then run 'docker compose up -d'."

pre-commit-setup:
	pip3 install pre-commit
	pre-commit install

# --------------------------------------------------------------------------
# Django management commands (dev environment)
# --------------------------------------------------------------------------
# Thin wrappers around `manage.py` for the most common ops commands. These
# use the default DJANGO_SETTINGS_MODULE (fm_eventmanager.settings) — the
# single canonical settings file. For the test-context migration check,
# see `test-check-migrations`.

makemigrations:
	uv run python manage.py makemigrations

migrate:
	uv run python manage.py migrate

createsuperuser:
	uv run python manage.py createsuperuser

# First-run admin provisioning. Idempotent: refuses to run if any superuser
# already exists. Use `bootstrap-admin` for an interactive prompt, or
# `bootstrap-admin-from-env` to consume BOOTSTRAP_ADMIN_USERNAME /
# BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD from env (Key Vault →
# Managed Identity → env in Azure deploys).
bootstrap-admin:
	uv run python manage.py bootstrap_admin

bootstrap-admin-from-env:
	uv run python manage.py bootstrap_admin --from-env

# Docker-compose-exec equivalents of the three above. These run inside the
# `app` container started by `make dev`, so the dev container must already
# be up (`docker compose up -d` / `make dev` in another shell). They use
# fm_eventmanager.settings (the canonical settings module baked into the
# image), pointing at the docker-network postgres rather than a host DB.

dev-makemigrations:
	docker-compose exec app python /app/manage.py makemigrations

dev-migrate:
	docker-compose exec app python /app/manage.py migrate

dev-createsuperuser:
	docker-compose exec app python /app/manage.py createsuperuser

# --------------------------------------------------------------------------
# Local test runner
#
# Requires: `docker compose up -d postgres redis` (to bring up the backing
# services) and a local uv-backed venv via `make dev-setup`.
#
# Settings (Decision #11): the ONE canonical
# `fm_eventmanager/settings.py` driven by the tracked `.env.test`
# (CELERY_TASK_ALWAYS_EAGER etc. come from that env file, not a separate
# settings module). DATABASE_* / REDIS_* / CELERY_* env vars below point
# the test run at the docker compose service hostnames
# resolved via 127.0.0.1 port bindings — adjust if your docker compose
# exposes different ports.
# --------------------------------------------------------------------------

# Ports exposed by docker-compose.yaml (override from the command line if
# your local mapping differs, e.g. `make test TEST_DATABASE_PORT=5433`).
# These are explicit TEST_* names so they do not collide with shell env
# leakage from direnv / .env / docker-compose contexts that can set
# DATABASE_HOST=postgres for the dev container.
TEST_DATABASE_HOST ?= 127.0.0.1
TEST_DATABASE_PORT ?= 5432
TEST_DATABASE_USER ?= apis
TEST_DATABASE_PASS ?= secret
TEST_DATABASE_NAME ?= apis
TEST_REDIS_HOST    ?= 127.0.0.1
TEST_REDIS_PORT    ?= 6379

TEST_STATIC_ROOT ?= $(CURDIR)/build/test-static

# Load the project's .env (sandbox credentials for PayPal, Square, etc.) into
# the Makefile's environment when it exists. Tests in registration/tests/
# (test_master, test_upgrades) hit the real sandbox APIs — they fail without
# real creds. CI supplies these via secrets; local dev sourced them from
# .env via direnv, but sub-make target invocations don't inherit that, so
# re-load here. .env is gitignored; never print its contents.
ifneq (,$(wildcard $(CURDIR)/.env))
-include $(CURDIR)/.env
export
endif

# Fallbacks when no creds are configured (e.g. a bare clone). Using ``?=``
# so the .env values above win.
PAYPAL_CLIENT_ID      ?= test
PAYPAL_CLIENT_SECRET  ?= test
SQUARE_APPLICATION_ID ?= test
SQUARE_ACCESS_TOKEN   ?= test
SQUARE_LOCATION_ID    ?= test

# Decision #11: the test posture lives in the tracked, secret-free,
# fully-independent .env.test (APIS_ENV=test). TEST_ENV is a command
# prefix that loads it (set -a exports every KEY=value) so the existing
# `$(TEST_ENV) uv run …` call sites are unchanged. Pre-exported env vars
# (e.g. real PAYPAL_* sandbox creds for `make test-paypal`) can still be
# layered by exporting them before invoking make and re-sourcing is
# idempotent. CI does NOT use this — django.yml loads .env.ci directly.
TEST_ENV = set -a && . ./.env.test && set +a &&

# Fail loudly if a model change on the branch lacks a migration. Django's
# test runner applies existing migrations to the throwaway test database, so
# we don't need a separate `migrate` step — but we DO need to catch missing
# migrations up-front. `makemigrations --check --dry-run` exits non-zero
# when it would have created a migration, making this a pre-test gate.
test-check-migrations:
	$(TEST_ENV) uv run python manage.py makemigrations --check --dry-run

# Build the Vite front-end bundle. The output
# (``registration/static/bundler/``, including ``manifest.json``) is picked
# up by Django's ``AppDirectoriesFinder`` and then by ``collectstatic``.
# Mirrors the "Build frontend" step in .github/workflows/django.yml so local
# test runs see the same static files as CI.
#
# Prerequisite target — depends on the manifest file so ``make`` rebuilds
# only when it's missing or source files are newer. If ``npm`` is not on
# PATH (sandboxes that haven't set up Node), the build step is skipped with
# a warning — tests that don't render Vite-backed templates still pass; the
# handful that do (``onsite_admin``'s SPA host view) will surface as real
# failures and require a Node environment to fix. Use ``make
# test-build-frontend-force`` to force a fresh install + build.
VITE_MANIFEST := registration/static/bundler/manifest.json
VITE_SOURCES  := $(shell find registration/frontend/src -type f 2>/dev/null) \
                 registration/frontend/package.json \
                 registration/frontend/vite.config.ts

$(VITE_MANIFEST): $(VITE_SOURCES)
	@if command -v npm >/dev/null 2>&1; then \
		cd registration/frontend && npm install && npm run build; \
	else \
		echo ">>> npm not found on PATH — skipping Vite build."; \
		echo ">>> Install Node.js (e.g. node_22 / nodejs_22) to enable this step."; \
		echo ">>> Writing a placeholder manifest so Django/tests can load the SPA host template."; \
		mkdir -p $(dir $@); \
		printf '%s\n' \
			'{' \
			'  "src/index.tsx": {' \
			'    "file": "assets/index.placeholder.js",' \
			'    "src": "src/index.tsx",' \
			'    "isEntry": true,' \
			'    "css": [],' \
			'    "imports": []' \
			'  }' \
			'}' > $@; \
	fi

test-build-frontend: $(VITE_MANIFEST)

test-build-frontend-force:
	cd registration/frontend && npm install && npm run build

test-collectstatic: test-build-frontend
	mkdir -p $(TEST_STATIC_ROOT)
	$(TEST_ENV) uv run python manage.py collectstatic --noinput

# --------------------------------------------------------------------------
# Backing services for tests (postgres, redis, gotenberg)
# --------------------------------------------------------------------------
#
# ``services-up`` is idempotent: if the services are already running (either
# from a prior ``make services-up`` or from the user's own ``docker compose
# up``), it just polls their TCP ports and exits. When the user is on a host
# without docker, it fails loudly with a pointer; CI and any environment that
# sets ``SKIP_DOCKER_SERVICES=1`` short-circuits (GitHub Actions provides its
# own service containers).
#
# ``gotenberg`` lives behind a compose profile in docker-compose.yaml so
# dev workflows that don't need the PDF renderer aren't forced to run it;
# tests DO need it, so we opt in with ``--profile gotenberg`` here.
# Auto-skip in CI — GitHub Actions declares postgres/redis/gotenberg as
# service containers. Callers can still force it off with
# ``SKIP_DOCKER_SERVICES=0``.
SKIP_DOCKER_SERVICES ?= $(if $(CI),1,)

services-up:
ifeq ($(SKIP_DOCKER_SERVICES),1)
	@echo ">>> SKIP_DOCKER_SERVICES=1 set — leaving backing services alone"
else
	@command -v docker >/dev/null 2>&1 || { \
		echo ">>> docker is not on PATH. Install Docker, or run with"; \
		echo ">>> SKIP_DOCKER_SERVICES=1 and start postgres/redis/gotenberg yourself"; \
		echo ">>> at 127.0.0.1:5432 / 6379 / 3000."; \
		exit 1; \
	}
	@docker compose version >/dev/null 2>&1 || { \
		echo ">>> 'docker compose' plugin is required (Compose V2)."; \
		exit 1; \
	}
	@echo ">>> docker compose up -d --wait postgres redis gotenberg"
	@docker compose --profile gotenberg up -d --wait postgres redis gotenberg
endif

services-down:
ifeq ($(SKIP_DOCKER_SERVICES),1)
	@echo ">>> SKIP_DOCKER_SERVICES=1 set — leaving services running"
else
	docker compose --profile gotenberg stop postgres redis gotenberg
endif

test-django: services-up test-check-migrations test-collectstatic
	$(TEST_ENV) uv run python manage.py test registration --verbosity 1

# Top-level regression gate: the Django suite and the Vitest SPA suite.
# The Playwright e2e suite is excluded here because ``playwright install
# --with-deps`` assumes apt-based distros and can't be run reliably on
# every contributor's host. Run ``make e2e`` explicitly (or rely on the
# Playwright E2E GitHub Action) when you need end-to-end coverage.
test: test-django test-frontend

test-paypal: test-check-migrations test-collectstatic
	$(TEST_ENV) uv run python manage.py test --tag=paypal --tag=PayPal --verbosity 1

# Run the Django test suite under coverage measurement. Emits terminal
# summary + an HTML report under ``htmlcov/``. Configuration (source paths,
# omit list) lives in ``pyproject.toml`` under ``[tool.coverage.*]``.
test-coverage: test-check-migrations test-collectstatic
	$(TEST_ENV) uv run coverage erase
	$(TEST_ENV) uv run coverage run manage.py test registration --verbosity 1
	$(TEST_ENV) uv run coverage report
	$(TEST_ENV) uv run coverage html

# Run the Solid.js SPA test suite (Vitest). ``npm install`` is idempotent so
# this works both in CI and on a fresh clone.
test-frontend:
	cd registration/frontend && npm install && npm run test:run

# Same as ``test-frontend`` but with v8 coverage enabled; report lands under
# ``registration/frontend/coverage/``.
test-frontend-coverage:
	cd registration/frontend && npm install && npm run test:coverage

# Aggregate target: run the Django suite under coverage, then the SPA tests.
test-all: test-coverage test-frontend

# --------------------------------------------------------------------------
# Playwright end-to-end suite (e2e/playwright/)
# --------------------------------------------------------------------------
#
# ``e2e-setup`` installs Playwright + browsers; safe to re-run. ``e2e``
# orchestrates a local server (scripts/up.sh brings services up, migrates,
# seeds, and runs ``manage.py runserver`` in the background with
# ``E2E_MODE=1``) then runs Playwright, and ``scripts/down.sh`` tears the
# server down whether tests passed or failed.
E2E_DIR := $(CURDIR)/e2e/playwright

e2e-setup:
	cd $(E2E_DIR) && npm install --no-audit --no-fund
	cd $(E2E_DIR) && npx playwright install --with-deps chromium firefox

e2e: e2e-setup
	@set -e; \
	bash $(E2E_DIR)/scripts/up.sh; \
	status=0; \
	( cd $(E2E_DIR) && npx playwright test ) || status=$$?; \
	bash $(E2E_DIR)/scripts/down.sh; \
	exit $$status

e2e-smoke: e2e-setup
	@set -e; \
	bash $(E2E_DIR)/scripts/up.sh; \
	status=0; \
	( cd $(E2E_DIR) && npx playwright test --grep @smoke ) || status=$$?; \
	bash $(E2E_DIR)/scripts/down.sh; \
	exit $$status

e2e-ui:
	bash $(E2E_DIR)/scripts/up.sh
	cd $(E2E_DIR) && npx playwright test --ui || true
	bash $(E2E_DIR)/scripts/down.sh

# mypy/pylint load Django via the django-stubs / pylint-django plugins, which
# import the canonical settings.py; that needs a (throwaway) secret key
# and a STATIC_ROOT.
# Decision #11: mypy/pylint import the Django settings (django plugins),
# so they need a clean importable env (DEBUG/ALLOWED_HOSTS/…). Reuse the
# tracked .env.test posture rather than a partial inline env that would
# trip settings.py's !DEBUG ALLOWED_HOSTS guard.
LINT_ENV = set -a && . ./.env.test && set +a &&

lint:
	uv run ruff check .
	uv run ruff format --check .

lint-fix:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	$(LINT_ENV) uv run mypy registration fm_eventmanager

lint-pylint:
	$(LINT_ENV) uv run pylint registration --fail-under=9.0

lint-vulture:
	uv run vulture

# D-LINT extras (shell / docker / actions / yaml). Each runs the tool
# when its binary is present; when absent it prints an install hint and
# exits 0 so `make lint-all` is runnable everywhere (CI installs the
# tools and they then run for real — not "if configured" in CI).
SHELL_SCRIPTS = start.sh nginx/entrypoint.sh nginx/refresh-cloudflare-ips.sh

lint-shell:
	@if command -v shellcheck >/dev/null 2>&1; then \
		shellcheck $(SHELL_SCRIPTS); \
	else echo "shellcheck not installed — skipping (CI runs it)"; fi

lint-docker:
	@if command -v hadolint >/dev/null 2>&1; then \
		hadolint Dockerfile nginx/Dockerfile; \
	else echo "hadolint not installed — skipping (CI runs it)"; fi

lint-actions:
	@if command -v actionlint >/dev/null 2>&1; then \
		actionlint; \
	else echo "actionlint not installed — skipping (CI runs it)"; fi

lint-yaml:
	@if command -v yamllint >/dev/null 2>&1; then \
		yamllint .github/ docker-compose*.yaml aks/ 2>/dev/null || true; \
	else echo "yamllint not installed — skipping (CI runs it)"; fi

# S20: module-level config reads belong in fm_eventmanager/settings.py.
# Flag NEW top-level os.getenv/os.environ in registration/ (function-body
# reads are fine). Informational gate — exits 0, surfaces the anti-pattern.
lint-env-sweep:
	@hits=$$(grep -rnE '^(os\.getenv|os\.environ|[A-Z_]+ = os\.(getenv|environ))' \
		registration/ --include=*.py 2>/dev/null | grep -v '/tests/' || true); \
	if [ -n "$$hits" ]; then \
		echo "S20: module-level env reads outside settings.py (review):"; \
		echo "$$hits"; \
	else echo "S20: no module-level env reads in registration/ — clean"; fi

# S24 / plan 721-723 (BLOCKING): no raw Order.status transition write in
# the request/view layer — every status transition must route through
# registration.payments.transition_order_status (or the documented
# Pattern-A helpers in payments.py/paypal_webhook_handlers.py). A genuine
# initial-create or audited exception carries an inline
# `status-writer-ok:` justification. Exits non-zero on a violation.
lint-status-writers:
	@hits=$$(grep -rnE '\.status = (Order|ApisOrder)\.|\.status = ["'"'"']|\.update\(status=' \
		registration/views/ --include=*.py 2>/dev/null \
		| grep -v '/tests/' | grep -v 'status-writer-ok' || true); \
	if [ -n "$$hits" ]; then \
		echo "BLOCK: raw Order.status writer in registration/views/ — route via transition_order_status:"; \
		echo "$$hits"; exit 1; \
	else echo "status-writers: views route status transitions through the primitive — clean"; fi

lint-all: lint typecheck lint-pylint lint-vulture lint-shell lint-docker lint-actions lint-yaml lint-env-sweep lint-status-writers

# MED-9 (CIS Docker 4.2 / OWASP A08): resolve the current digest for
# every secondary container image so a refresh is a deliberate, reviewed
# act (a tag swap at the registry must never silently land on the next
# `docker compose pull`). Prints copy-paste-ready `image: <ref>@sha256:`
# lines; the operator bakes them into docker-compose.prod.yaml,
# nginx/Dockerfile, and the kustomize `images:` block. Uses buildx
# imagetools (no pull). See docs/deploy-preflight.md.
DIGEST_IMAGES = \
	mirror.gcr.io/library/nginx:1.27-alpine \
	mirror.gcr.io/library/postgres:16 \
	mirror.gcr.io/library/redis:8 \
	mirror.gcr.io/gotenberg/gotenberg:8 \
	furpocalypse.azurecr.io/apis-nginx:0.5.2 \
	furpocalypse.azurecr.io/apis:0.5.3

refresh-digests:
	@for img in $(DIGEST_IMAGES); do \
		d=$$(docker buildx imagetools inspect "$$img" --format '{{.Manifest.Digest}}' 2>/dev/null) || \
			{ echo "FAILED to resolve $$img (registry auth / network?)" >&2; continue; }; \
		echo "$${img%%:*}:$${img##*:}@$$d"; \
	done

.PHONY: e2e e2e-setup e2e-smoke e2e-ui test test-django \
        makemigrations migrate createsuperuser \
        bootstrap-admin bootstrap-admin-from-env \
        dev-makemigrations dev-migrate dev-createsuperuser \
        lint lint-fix typecheck lint-pylint lint-vulture lint-all \
        lint-shell lint-docker lint-actions lint-yaml lint-env-sweep \
        lint-status-writers refresh-digests
