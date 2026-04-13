
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

	make test                       : Run Django test suite against docker-compose services (uses uv)
	make test-paypal                : Run only PayPal-tagged tests (uses uv)
	make test-check-migrations      : Fail if branch has model changes without a migration
	make test-build-frontend        : Build the Vite front-end bundle (npm install + npm run build)
	make test-collectstatic         : Populate the staticfiles manifest tests depend on

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
	cp fm_eventmanager/settings.py.devel fm_eventmanager/settings.py

	@echo "ACTION REQUIRED: Review fm_eventmanager/settings.py"

pre-commit-setup:
	pip3 install pre-commit
	pre-commit install

# --------------------------------------------------------------------------
# Local test runner
#
# Requires: `docker compose up -d postgres redis` (to bring up the backing
# services) and a local uv-backed venv via `make dev-setup`.
#
# Settings: uses `fm_eventmanager/settings.py.docker` verbatim via
# `fm_eventmanager/settings_test.py` (an exact copy checked in for this
# purpose). DATABASE_* / REDIS_* / CELERY_* env vars below point the test
# run at the docker compose service hostnames resolved via 127.0.0.1 port
# bindings — adjust if your docker compose exposes different ports.
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

TEST_ENV = \
	DJANGO_SETTINGS_MODULE=fm_eventmanager.settings_test \
	DJANGO_SECRET_KEY=test \
	PAYPAL_CLIENT_ID=$(PAYPAL_CLIENT_ID) \
	PAYPAL_CLIENT_SECRET=$(PAYPAL_CLIENT_SECRET) \
	SQUARE_APPLICATION_ID=$(SQUARE_APPLICATION_ID) \
	SQUARE_ACCESS_TOKEN=$(SQUARE_ACCESS_TOKEN) \
	SQUARE_LOCATION_ID=$(SQUARE_LOCATION_ID) \
	CSRF_TRUSTED_ORIGINS='http://*,https://*' \
	DATABASE_HOST=$(TEST_DATABASE_HOST) DATABASE_PORT=$(TEST_DATABASE_PORT) \
	DATABASE_USER=$(TEST_DATABASE_USER) DATABASE_PASS=$(TEST_DATABASE_PASS) \
	DATABASE_NAME=$(TEST_DATABASE_NAME) DJANGO_DATABASE_POOL=False \
	DJANGO_REDIS_URL=redis://$(TEST_REDIS_HOST):$(TEST_REDIS_PORT)/1 \
	CELERY_BROKER_URL=redis://$(TEST_REDIS_HOST):$(TEST_REDIS_PORT)/2 \
	CELERY_RESULT_BACKEND=redis://$(TEST_REDIS_HOST):$(TEST_REDIS_PORT)/2 \
	IDEMPOTENCY_KEY_LOCK_LOCATION=redis://$(TEST_REDIS_HOST):$(TEST_REDIS_PORT) \
	STATIC_ROOT=$(TEST_STATIC_ROOT) \
	MAINTENANCE_MODE_STATE_FILE_PATH=$(CURDIR)/build/maintenance_mode_state.txt \
	GOTENBERG_HOST=http://127.0.0.1:3000

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

test: test-check-migrations test-collectstatic
	$(TEST_ENV) uv run python manage.py test registration --verbosity 1

test-paypal: test-check-migrations test-collectstatic
	$(TEST_ENV) uv run python manage.py test --tag=paypal --tag=PayPal --verbosity 1
