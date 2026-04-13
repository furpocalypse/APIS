# Deployment guide

APIS is deployed to **Azure Container Apps** (ACA) with managed
**Postgres Flexible Server** + **Cache for Redis**, and is built to absorb
a 10,000+ concurrent registration stampede via KEDA HTTP autoscaling.

This document covers the full lifecycle: container image, infrastructure,
secrets, deploy, load test, rollback. For component-level detail, see:

- [infra/README.md](infra/README.md) — Bicep module + bootstrap
- [loadtest/README.md](loadtest/README.md) — Locust scenarios + how to run
  the stampede

## Architecture at a glance

```
Internet → ACA Envoy ingress (TLS, autoscale-aware)
            │
            ├── apis-{env}-web      (1..N replicas, KEDA HTTP)
            │     pod = Caddy → Unix socket → Hypercorn (Django ASGI)
            │
            └── apis-{env}-worker   (1..M replicas, KEDA Redis-queue)
                  pod = Celery worker

Managed dependencies (per environment):
  • Azure Database for PostgreSQL Flexible Server (PgBouncer enabled)
  • Azure Cache for Redis (Standard, replicated)
  • Azure Container Registry (ACR)
  • ACA Job: apis-{env}-migrate (one-shot, runs before each rollout)
  • Azure Key Vault (consumed via secretRef on Container Apps)
  • Log Analytics workspace (ACA logs land here)
```

The same [infra/main.bicep](infra/main.bicep) module produces both `stage`
and `prod` — they differ only in their `parameters.*.json` files.

## Container image

Built from the multi-stage [Dockerfile](Dockerfile):

1. `node:lts` builds the SolidJS bundle via Vite.
2. `caddy:2` provides the Caddy binary.
3. `python:3.14-slim-trixie` installs Python deps with `uv sync --frozen`,
   copies the SolidJS bundle in, runs `collectstatic`.

Inside the runtime container:

- **Caddy** ([Caddyfile](Caddyfile)) listens on `:80`, serves
  `/static/*` from `/app/apis/static/` with `Cache-Control: max-age=31536000,
  immutable`, reverse-proxies everything else to Hypercorn over a Unix
  socket.
- **Hypercorn** ([hypercorn.toml](hypercorn.toml)) runs the ASGI app on
  `unix:/tmp/apis.sock` with `workers=4` and the asyncio worker class.
- **supervisord** ([supervisord.conf](supervisord.conf)) keeps both alive
  and SIGQUITs the container if either dies (so ACA replaces the pod).

The image's entrypoint is [start.sh](start.sh):

| Command             | Behavior                                           |
|---------------------|----------------------------------------------------|
| `start.sh` (no arg) | Run Caddy + Hypercorn under supervisord (web pod)  |
| `start.sh worker`   | Run Celery worker                                  |
| `start.sh migrate`  | Run `manage.py migrate --noinput` and exit         |

Migrations no longer run on every container start. Set
`RUN_MIGRATIONS_ON_START=true` in your local env if you want the old
auto-migrate behavior for docker-compose dev — the
[docker-compose.yaml](docker-compose.yaml) `migrate` service handles it
once-per-`up` for you instead.

## Health and readiness probes

| Path       | Returns                                                                |
|------------|------------------------------------------------------------------------|
| `/healthz` | `200 ok` (process is alive)                                            |
| `/readyz`  | JSON `{postgres, redis, migrations}` — `200` only when all are `ok`    |

Implemented in [fm_eventmanager/health.py](fm_eventmanager/health.py),
wired in [fm_eventmanager/urls.py](fm_eventmanager/urls.py). Both are
exempt from maintenance mode so probes still pass during planned downtime.
ACA uses `/healthz` for liveness/startup and `/readyz` for readiness; see
the `probes` block in [infra/main.bicep](infra/main.bicep).

## Bootstrap (one-time per environment)

Do this once per environment (`stage`, then `prod`) before any deploy
runs. Detailed steps are in [infra/README.md](infra/README.md). Short
form:

1. **Create the resource group:**

   ```
   az group create --name apis-stage --location eastus2
   ```

2. **Create Key Vault by hand** (the Bicep template references it; can't
   self-bootstrap):

   ```
   az keyvault create -g apis-stage -n apis-stage-kv -l eastus2 \
     --enable-rbac-authorization
   ```

3. **Populate Key Vault secrets** from your existing external secret store.
   The full list and `az keyvault secret set` loop is in
   [infra/README.md](infra/README.md). At minimum:

   - `postgres-admin-password`
   - `django-secret-key`
   - `database-pass` (same value as postgres-admin-password)
   - `redis-primary-key` (placeholder — Azure generates this; we copy it
     after the first apply)
   - `square-application-id`, `square-application-secret`,
     `square-access-token`, `square-location-id`
   - `paypal-client-id`, `paypal-client-secret`
   - `email-host-password`
   - `sentry-dsn`
   - `mqtt-jwt-secret`

4. **Replace placeholders** in [infra/parameters.stage.json](infra/parameters.stage.json):
   `REPLACE_WITH_REGISTRY`, `REPLACE_SUB`, `REPLACE_RG`. Same for
   `parameters.prod.json` when you're ready to provision prod.

5. **First apply** (creates ACR, Postgres, Redis, KV role assignments,
   Container Apps):

   ```
   make infra-apply-stage
   ```

6. **Backfill the Redis key** Azure just generated into Key Vault:

   ```
   az redis list-keys -g apis-stage -n apis-stage-redis \
     --query primaryKey -o tsv \
     | az keyvault secret set --vault-name apis-stage-kv \
       --name redis-primary-key --file /dev/stdin
   ```

7. **Re-apply** so the Container Apps pick up the real Redis key:

   ```
   make infra-apply-stage
   ```

8. **Configure GitHub OIDC federated credentials** so
   [.github/workflows/deploy-aca.yml](.github/workflows/deploy-aca.yml)
   can `az login` without long-lived secrets. Set the following GitHub
   environment secrets (one set per `stage` / `prod` environment):

   - `AZURE_CLIENT_ID` — app registration's client ID
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`

   The app registration needs `Contributor` on the resource group and
   `AcrPush` on the registry.

## Deploy

End-to-end pipeline lives in
[.github/workflows/deploy-aca.yml](.github/workflows/deploy-aca.yml).
Trigger via the GitHub Actions UI: pick `stage` or `prod`. The workflow:

1. Logs into Azure via OIDC.
2. Builds the image with `SENTRY_RELEASE=$(git describe --tag --always)`
   and pushes to ACR with two tags: the SHA and `{environment}`.
3. Runs `az deployment group create` against the Bicep module
   (idempotent — only changes drift).
4. Updates the migrate Job to the new image, starts it, polls until
   `Succeeded` (fails the workflow on `Failed`/`Cancelled`/timeout).
5. Rolls the web Container App to the new image.
6. Rolls the worker Container App to the new image.
7. Smokes `https://{fqdn}/healthz` for up to 60s.

Same flow works manually:

```
az acr build -r apisstageacr -t apis:$(git describe --tag --always) .
make infra-apply-stage TAG=$(git describe --tag --always)
az containerapp job start -g apis-stage -n apis-stage-migrate
az containerapp update -g apis-stage -n apis-stage-web \
  --image apisstageacr.azurecr.io/apis:$(git describe --tag --always)
az containerapp update -g apis-stage -n apis-stage-worker \
  --image apisstageacr.azurecr.io/apis:$(git describe --tag --always)
```

## Verify

After every stage deploy, before declaring the change shippable to prod:

1. **Smoke** — confirmed by the workflow's `Smoke check` step.
2. **Local load smoke** — `make loadtest` against your dev stack first to
   prove the scenarios are correct (see [loadtest/README.md](loadtest/README.md)).
3. **Stage stampede** — run `make loadtest-stampede TARGET_HOST=https://stage.apis.example.com`
   from a `Standard_D4s_v5` VM in the same Azure region.

   Pass criteria:
   - p95 on `GET /registration/` < 1500 ms
   - 0 5xx
   - ACA reaches steady-state replica count within 90 s of ramp start
   - Postgres CPU < 80%, Redis CPU < 60% (Azure Portal → resource → Metrics)

4. **Failure injection** — kill one web replica mid-test
   (`az containerapp revision deactivate ...`); confirm requests don't
   drop.
5. **Rollback drill** — see below.

Only once all five pass, deploy `prod`.

## Rollback

ACA keeps prior revisions around. To shift traffic back:

```
# List revisions, newest first
az containerapp revision list -g apis-prod -n apis-prod-web \
  --query "[].{name:name, active:properties.active, traffic:properties.trafficWeight, created:properties.createdTime}" -o table

# Switch to multiple-revision mode and split traffic away from the bad one
az containerapp revision set-mode -g apis-prod -n apis-prod-web --mode multiple
az containerapp ingress traffic set -g apis-prod -n apis-prod-web \
  --revision-weight <previous-good-revision>=100 <broken-revision>=0
```

Migrations are forward-only: if a deploy ran a migration that the
previous image can't tolerate, you need a code-level fix-forward, not a
rollback. We rely on Django migrations being backwards-compatible across
adjacent releases; document this constraint when reviewing migration
PRs.

## Secrets and configuration

All sensitive env vars come from Key Vault via `secretRef` on the
Container App. Non-sensitive vars (`ALLOWED_HOSTS`, `DJANGO_DEBUG=False`,
hostnames, etc.) are inlined in [infra/main.bicep](infra/main.bicep). To
add a new secret:

1. `az keyvault secret set --vault-name apis-{env}-kv --name foo --value ...`
2. Add a new entry to `commonSecrets` and `commonEnv` in
   [infra/main.bicep](infra/main.bicep).
3. `make infra-apply-{env}`.

The full inventory of env var **names** (not values) is in
[example.env](example.env).

## Scaling knobs

Edit `infra/parameters.*.json` and re-apply.

| Knob                   | Stage default | Prod default | Notes                                |
|------------------------|---------------|--------------|--------------------------------------|
| `webMinReplicas`       | 1             | 2            | Always-warm replicas                 |
| `webMaxReplicas`       | 10            | 30           | Cap; revisit after stampede load test|
| `workerMinReplicas`    | 1             | 1            |                                      |
| `workerMaxReplicas`    | 5             | 20           | KEDA scales on Celery queue length   |
| `postgresSku`          | `D2ds_v5`     | `D4ds_v5`    | Re-tier after load testing           |
| `postgresStorageGb`    | 64            | 128          | Auto-grow is enabled                 |
| `redisCapacity`        | 1 (C1)        | 2 (C2)       | C2 = 2.5 GiB                         |

The KEDA HTTP rule on `apis-{env}-web` triggers at
`concurrentRequests=50` per replica. Hypercorn runs `workers=4` per
replica; on a 1.0 vCPU container that's a comfortable per-replica
ceiling. Adjust both together.

## Known not-in-scope

- **Multi-region failover.** Single region only. Pick `westus3` or
  `eastus2` based on attendee geography.
- **Blue/green database migrations.** Forward-only; migrations must be
  backwards-compatible with the previous image.
- **MQTT broker hosting in Azure.** Continues to use the existing
  external broker. Revisit if registration flow needs it live.
- **Azure-hosted Sentry / metrics replacements.** Sentry, Coveralls, and
  the existing OSS monitoring profile carry over unchanged. ACA logs
  flow to Log Analytics by default; stand up App Insights separately
  if you want APM-style traces.
