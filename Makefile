
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
TEST_REDIS_HOST    ?= $(shell docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' apis-redis-1 2>/dev/null || echo 127.0.0.1)
TEST_REDIS_PORT    ?= 6379

TEST_ENV = \
	DJANGO_SETTINGS_MODULE=fm_eventmanager.settings_test \
	DJANGO_SECRET_KEY=test \
	PAYPAL_CLIENT_ID=test \
	PAYPAL_CLIENT_SECRET=test \
	CSRF_TRUSTED_ORIGINS='http://*,https://*' \
	DATABASE_HOST=$(TEST_DATABASE_HOST) DATABASE_PORT=$(TEST_DATABASE_PORT) \
	DATABASE_USER=$(TEST_DATABASE_USER) DATABASE_PASS=$(TEST_DATABASE_PASS) \
	DATABASE_NAME=$(TEST_DATABASE_NAME) DJANGO_DATABASE_POOL=False \
	DJANGO_REDIS_URL=redis://$(TEST_REDIS_HOST):$(TEST_REDIS_PORT)/1 \
	CELERY_BROKER_URL=redis://$(TEST_REDIS_HOST):$(TEST_REDIS_PORT)/2 \
	CELERY_RESULT_BACKEND=redis://$(TEST_REDIS_HOST):$(TEST_REDIS_PORT)/2 \
	IDEMPOTENCY_KEY_LOCK_LOCATION=redis://$(TEST_REDIS_HOST):$(TEST_REDIS_PORT)

test:
	$(TEST_ENV) uv run python manage.py test registration --verbosity 1 --keepdb

test-paypal:
	$(TEST_ENV) uv run python manage.py test --tag=paypal --tag=PayPal --verbosity 1 --keepdb
