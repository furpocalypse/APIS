# Azure infrastructure

[main.bicep](main.bicep) declares the entire stage and prod stacks: ACR, Log
Analytics, Container App Environment, two Container Apps (`apis-{env}-web`,
`apis-{env}-worker`), one migration Job (`apis-{env}-migrate`), Azure DB for
PostgreSQL Flexible Server (with PgBouncer), Azure Cache for Redis, Key Vault,
and a user-assigned managed identity wired up for ACR pull and Key Vault
secrets read.

## Bootstrap (one-time per environment)

1. Create the resource group:

   ```
   az group create --name apis-stage --location eastus2
   ```

2. Create the Key Vault by hand the first time, since the Bicep template
   references it via `keyVault` parameter resolution:

   ```
   az keyvault create -g apis-stage -n apis-stage-kv -l eastus2 --enable-rbac-authorization
   ```

3. Populate the secrets the deployment will consume. Replace placeholder
   values with the real ones from your external secret store:

   ```
   for s in postgres-admin-password django-secret-key database-pass \
            redis-primary-key square-application-id square-application-secret \
            square-access-token square-location-id paypal-client-id \
            paypal-client-secret email-host-password sentry-dsn \
            mqtt-jwt-secret; do
     az keyvault secret set --vault-name apis-stage-kv --name "$s" --value "<value>"
   done
   ```

   Note: `redis-primary-key` is set to a placeholder during bootstrap; after the
   first `make infra-apply-stage` succeeds, copy the actual key:

   ```
   az redis list-keys -g apis-stage -n apis-stage-redis --query primaryKey -o tsv \
     | az keyvault secret set --vault-name apis-stage-kv --name redis-primary-key --file /dev/stdin
   ```

   Then re-run `make infra-apply-stage` so the Container Apps pick up the new
   value.

4. Edit [parameters.stage.json](parameters.stage.json) — replace
   `REPLACE_WITH_REGISTRY` and `REPLACE_SUB` / `REPLACE_RG` placeholders.

## Deploy

```
make infra-plan-stage      # what-if preview
make infra-apply-stage     # actually deploy
```

The full deploy + image push + migration cycle is wired into
[../.github/workflows/deploy-aca.yml](../.github/workflows/deploy-aca.yml). The
Make targets are for ad-hoc work and bootstrap.
