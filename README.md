# APIS EventManager

![Build](https://github.com/furpocalypse/APIS/actions/workflows/django.yml/badge.svg) [![Coverage Status](https://coveralls.io/repos/github/furpocalypse/APIS/badge.svg)](https://coveralls.io/github/furpocalypse/APIS)

Data Model snapshot (7 December 2020): https://i.imgur.com/A4fPDf5.png

Stack:
  + Ubuntu 22.04 (LTS)
  + Python 3.14
  + Django 6.0
  + PostgreSQL 16.10
  + Bootstrap 3/jQuery 1.12
  + SolidJS
  + MQTT event passing

## Features
  + Take payments for pre-registration using [Square][square], both online
    and in-person with an [iPad app][ipad] as a customer-facing
    display, with cash drawer and receipt printer integration.
  + Manage staff registration and department hierarchies.
  + Handle dealer applications, registration, and payments.
  + Create limited-use discounts.
  + Handle on-site registration on your own kiosks, or via a public URL.
  + Populate attendee information by scanning their ID.
  + Print badges on the fly with a custom template on any compatible card
    or label printer, with Unicode-supported fonts (Emoji!)
  + Protect admin and volunteer logins with TOTP 2-Factor or FIDO U2F.

![Screenshot of Cash Register Position](/docs/admin-onsite.png)

## Quick start

The runtime for both dev and prod is Docker. There are two compose files:

| Compose file | Purpose | Image source |
|---|---|---|
| `docker-compose.yaml` (default) | **Development** — local build, source bind-mounts for hot-reload, no hardening | `build: .` (from local `Dockerfile`) |
| `docker-compose.prod.yaml` | **Production** — pulls a tagged image from ACR, full hardening, bind mounts to a persistent disk | `furpocalypse.azurecr.io/apis:x.x.x` |

Optional monitoring stack (InfluxDB 3 + Prometheus + node-exporter + Grafana): `docker-compose.monitoring.yaml` is an overlay that works with either of the above. Run `./setup-monitoring.sh` once to drop the supporting config files, then add the overlay to your `docker compose -f ...` invocation.

### Local development

```bash
git clone https://github.com/furpocalypse/APIS.git
cd APIS

# Decision #11: `.env.dev` is TRACKED, complete and secret-free — the dev
# compose loads it directly, so `docker compose up` Just Works after a
# clean clone. Only the DB credential file is per-machine:
cp database.env.example database.env
# (database.env is gitignored. DATABASE_USER / DATABASE_PASS in .env.dev
# must match POSTGRES_USER / POSTGRES_PASSWORD in database.env; edit
# .env.dev in place only if you change those.)

# Install Docker if you don't have it; on Ubuntu:
#   curl -fsSL https://get.docker.com | sh
#   sudo usermod -aG docker $USER   # then log out + back in

# Build + start the stack
docker compose up -d

# Create the initial admin (idempotent — refuses if a superuser exists)
docker compose exec app /app/manage.py bootstrap_admin
```

Then open <http://localhost:8000/registration/> in a browser. Dev exposes gunicorn directly on 8000 — no nginx sidecar in the dev compose. Production has `apis-nginx` in front (TLS + Cloudflare gate); see [`docker-compose.prod.yaml`](docker-compose.prod.yaml) and [`nginx/README.md`](nginx/README.md).

### Production deploy (Azure VM)

```bash
# On the VM, in /opt/apis:
cp .env.production.example .env.production   # Decision #11: prod compose loads .env.production (APIS_ENV=production); fill in REAL secrets / ACR token / etc.
cp database.env.example database.env         # POSTGRES_PASSWORD must match DATABASE_PASS in .env.production

# Run the one-shot deploy script — handles data-dir ownership, ACR login,
# pull + up, healthcheck wait, and admin bootstrap. Idempotent.
./azure-vm-deploy.sh
```

See [`.env.production.example`](.env.production.example), [`docker-compose.prod.yaml`](docker-compose.prod.yaml), [`docs/deploy-preflight.md`](docs/deploy-preflight.md) (image-digest + netpol/CIDR preflight), and the [`azure-vm-deploy.sh`](azure-vm-deploy.sh) header docstring for the full operator runbook.

### Production use — additional infrastructure

For taking on-site payments via the iPad / Square Terminal flow you'll also need an MQTT broker. See [the wiki page](https://github.com/furthemore/APIS/wiki/MQTT-Configuration) for configuration notes.

## Development

### Using [pre-commit](https://pre-commit.com/)
1. Install: `pip install pre-commit` or `brew install pre-commit`.
2. then run: `pre-commit install`, this will apply the hooks defined in `.pre-commit-config.yaml` to evey commit

### Running tests

APIS has three test suites — Django (`unittest` via `manage.py test`), the
Solid.js SPA (Vitest), and the Playwright end-to-end suite. The Makefile
targets wire up every environment variable the suites expect; prefer them
over invoking `manage.py test` / `npx vitest` / `npx playwright` directly
unless you are narrowing down a specific failure.

#### Prerequisites

- `uv` installed (see the Manual setup section above).
- Docker + Docker Compose running — the Django suite connects to real
  PostgreSQL, Redis, and Gotenberg instances rather than mocking them.
  `make test-django` runs `make services-up` for you, but you can start
  them eagerly with:

      make services-up        # postgres, redis, gotenberg via docker compose
      make services-down      # tear down (volumes preserved)

- `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` exported in the shell
  running the tests. Sandbox credentials are fine; the suites that touch
  PayPal substitute an in-process stub so no real API call is made, but the
  settings module refuses to import without the env vars set. If you have
  `direnv` wired up, these come from `.env` automatically.

#### Targets

| Target                         | What it runs                                                                 |
|--------------------------------|------------------------------------------------------------------------------|
| `make test`                    | Full regression gate: Django + Vitest + Playwright. Use before opening a PR. |
| `make test-django`             | All 450+ Django tests against a throwaway Postgres DB.                       |
| `make test-paypal`             | Only tests tagged `paypal` / `PayPal` — fastest feedback for payment work.   |
| `make test-coverage`           | Django suite under `coverage`; emits `htmlcov/` + terminal summary.          |
| `make test-all`                | Django (with coverage) + frontend. No Playwright. Good for CI parity.        |
| `make test-frontend`           | Vitest suite in `registration/frontend`.                                     |
| `make test-frontend-coverage`  | Vitest with v8 coverage.                                                     |
| `make test-check-migrations`   | Fails if you've edited a model without a matching migration.                 |
| `make test-build-frontend`     | Runs `npm install && npm run build` to produce the Vite manifest.            |
| `make test-collectstatic`      | Populates the staticfiles manifest the Django suite reads.                   |
| `make e2e-setup`               | Installs Playwright browsers under `e2e/playwright`. Run once.               |
| `make e2e`                     | Spins up a throwaway local server and runs every Playwright spec.            |
| `make e2e-smoke`               | Playwright specs tagged `@smoke` only.                                       |
| `make e2e-ui`                  | Opens Playwright's interactive UI for debugging specs.                       |

#### Running a single test

The Makefile targets run the full suite. To drive a single test, bring up
the services, then invoke `manage.py test` / `vitest` / `playwright`
directly:

```bash
make services-up

# A specific Django test class or method. The Makefile's TEST_ENV block
# documents every env var the suite needs if you want to reproduce it
# without the Makefile.
uv run python manage.py test \
    registration.tests.test_paypal_webhooks.TestPaypalCaptureWebhooks

# A specific Vitest file
cd registration/frontend && npx vitest run src/components/button.test.tsx

# A specific Playwright spec
cd e2e/playwright && npx playwright test tests/webhooks.spec.ts
```

The PayPal stub used by the webhook + checkout tests lives in
`registration/e2e/paypal_stub.py`; the Playwright harness toggles it on via
`E2E_MODE=1` when launching the local server.

#### Interpreting failures

- **`DjangoViteAssetNotFoundError: Cannot find src/index.tsx ...`** — the
  Vite manifest hasn't been built. Run `make test-build-frontend` then
  `make test-collectstatic`.
- **`couldn't get a connection after 30.00 sec`** — Postgres (or Redis)
  isn't running. Run `make services-up`.
- **`PAYPAL_WEBHOOK_ID is not configured`** logged as ERROR during webhook
  tests — expected only in unit tests that deliberately exercise the
  fail-closed path of `verify_signature` (they override the setting to an
  empty string). **In any deployed environment this line is an outage
  signal, never noise**: it means every PayPal webhook delivery is being
  rejected with 403 because `PAYPAL_WEBHOOK_ID` is missing or not reaching
  Django settings. Investigate immediately.
- **Pre-commit hook failures** — fix the reported issue and create a new
  commit; never use `--no-verify` in this repo.

[square]: https://square.com/
[ipad]: https://github.com/furthemore/APIS-Register-Swift
[android]: https://github.com/furthemore/APIS-register
[direnv]: https://direnv.net/
[uv]: https://docs.astral.sh/uv/
[uv-install]: https://docs.astral.sh/uv/#installation
