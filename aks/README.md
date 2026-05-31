# APIS on Azure Kubernetes Service

Kustomize-based manifests for running APIS on AKS. The base layer is
environment-agnostic; the `overlays/dev` and `overlays/prod` directories
patch the values that differ per environment.

## Layout

```
aks/
├── base/                          # environment-agnostic manifests
│   ├── kustomization.yaml
│   ├── namespace.yaml             # ns + Pod Security "restricted"
│   ├── serviceaccount.yaml        # bound to Azure Workload Identity
│   ├── secretproviderclass.yaml   # Key Vault CSI: which secrets to mount
│   ├── configmap-app.yaml         # non-secret env
│   ├── deployment-app.yaml        # gunicorn pod (web). AGIC terminates TLS; no nginx sidecar here.
│   ├── deployment-worker.yaml     # celery worker
│   ├── statefulset-postgres.yaml  # in-cluster Postgres
│   ├── statefulset-redis.yaml     # in-cluster Redis
│   ├── services.yaml              # ClusterIPs (web + headless DB/Redis)
│   ├── ingress.yaml               # AGIC ingress
│   ├── hpa.yaml                   # HPA for web + worker
│   ├── pdb.yaml                   # PDB for web + DB + Redis
│   └── networkpolicy.yaml         # default-deny + explicit allows
└── overlays/
    ├── dev/                       # 1 replica everywhere, sandbox creds
    └── prod/                      # 3 web replicas, prod creds, real FQDN
```

## Architecture decisions

- **gunicorn-only app pod.** The app image ships gunicorn as PID 1 on TCP :8000. nginx lives in a separate image (`apis-nginx`, see `nginx/`) used in the docker-compose topology — AKS doesn't need it because AGIC terminates TLS at the gateway and reaches gunicorn directly via the Service.
- **In-cluster Postgres + Redis** as StatefulSets backed by `managed-csi-premium` (Azure Premium SSD v1). For real production, prefer **Azure Database for PostgreSQL Flexible Server** and **Azure Cache for Redis** — see the trade-offs documented in `docker-compose.prod.yaml`. To migrate, drop both StatefulSets and point `DATABASE_HOST` / `DJANGO_REDIS_URL` at the managed endpoints.
- **AGIC for ingress.** TLS terminates at Application Gateway (WAF v2); AGIC forwards plaintext to the apis-app Service on :8000. The pod sets `TRUSTED_PROXY_MODE=proxy` with the AppGW subnet CIDR pinned in `TRUSTED_PROXY_CIDRS`. No Front Door in this topology — add it as a second trusted hop if needed.
- **Workload Identity** for the pod → ACR pull and pod → Key Vault read paths. No service-principal secrets in the cluster.
- **Key Vault Provider for Secrets Store CSI Driver** delivers credentials. Pod mounts the SPC volume; secrets are also projected into the `apis-secrets` Kubernetes Secret so the Deployment can use `envFrom`. No secrets in YAML.
- **No celery beat.** A grep of `registration/tasks.py` shows only `@shared_task` decorators — every task is dispatched by a web request. Add a beat Deployment only if scheduled work is introduced.
- **No monitoring manifests yet.** Enable Azure Managed Prometheus + Container Insights add-ons on the cluster, then add a `PodMonitor` scraping the app's `:81/metrics/` path. The `docker-compose.monitoring.yaml` stack can be ported in-cluster later if you'd rather self-host.

## One-time cluster setup

```bash
# Variables you'll reuse.
SUB=<subscription-id>
RG=<resource-group>
CLUSTER=<aks-name>
KV=<keyvault-name>
ACR=furpocalypse
APPGW=<application-gateway-name>

# 1. Cluster add-ons
az aks enable-addons -g $RG -n $CLUSTER \
    -a azure-keyvault-secrets-provider \
    -a ingress-appgw \
    --appgw-id $(az network application-gateway show -g $RG -n $APPGW --query id -o tsv)

az aks update -g $RG -n $CLUSTER \
    --enable-oidc-issuer \
    --enable-workload-identity

# 2. ACR pull access from the cluster's kubelet identity
az aks update -g $RG -n $CLUSTER --attach-acr $ACR

# 3. User-assigned Managed Identity for the app pod
az identity create -g $RG -n apis-app-mi
MI_CLIENT_ID=$(az identity show -g $RG -n apis-app-mi --query clientId -o tsv)
MI_OBJECT_ID=$(az identity show -g $RG -n apis-app-mi --query principalId -o tsv)

# 4. Federate the MI to the in-cluster ServiceAccount (per overlay namespace)
OIDC=$(az aks show -g $RG -n $CLUSTER --query oidcIssuerProfile.issuerUrl -o tsv)
for NS in apis apis-dev; do
  az identity federated-credential create \
    --name "apis-app-fic-$NS" \
    --identity-name apis-app-mi -g $RG \
    --issuer $OIDC \
    --subject system:serviceaccount:$NS:apis-app \
    --audience api://AzureADTokenExchange
done

# 5. Grant Key Vault read access to the MI
az role assignment create \
    --role "Key Vault Secrets User" \
    --assignee $MI_OBJECT_ID \
    --scope $(az keyvault show -g $RG -n $KV --query id -o tsv)

# 6. (If using KV-backed TLS cert delivery) — also grant:
#    "Key Vault Certificate User" on the cert.

# 7. Populate Key Vault with the secrets named in
#    base/secretproviderclass.yaml. Names use kebab-case in KV; the SPC
#    aliases them to UPPER_SNAKE_CASE for env vars.
for SECRET in django-secret-key database-pass postgres-password \
              bootstrap-admin-password paypal-client-id paypal-client-secret \
              paypal-webhook-id square-application-id square-access-token \
              square-location-id square-webhook-signature-key \
              mqtt-jwt-secret email-host-password sentry-dsn; do
  echo "Set $SECRET in $KV"
done
```

## Per-deploy workflow

**Single-locus migration (S34 / peer-review):** app + worker pods do
NOT run `migrate` (`APIS_RUN_MIGRATIONS=0` in `apis-config.env`; with
`replicas: 2` they would otherwise race a destructive migration). The
dedicated `apis-migrate` **Job** is the one locus. A `Job` is immutable
on spec, so each deploy must delete-then-recreate it BEFORE rolling the
Deployments:

```bash
# Validate without applying
kubectl kustomize aks/overlays/prod | kubectl apply --dry-run=client -f -

# 1. Run migrations exactly once (delete the prior immutable Job first)
kubectl -n apis delete job apis-migrate --ignore-not-found
kubectl apply -k aks/overlays/prod          # recreates the Job + all manifests
kubectl -n apis wait --for=condition=complete job/apis-migrate --timeout=600s

# 2. Roll the app/worker Deployments only AFTER migrations completed
kubectl -n apis rollout status deploy/apis-app
kubectl -n apis rollout status deploy/apis-worker
kubectl -n apis get pods,svc,ingress

# Bootstrap the initial superuser (idempotent)
kubectl -n apis exec deploy/apis-app -- ./manage.py bootstrap_admin --from-env

# Roll forward to a new image tag
# (edit overlays/prod/kustomization.yaml `images.newTag` to the new
# semver + digest, then re-apply)
kubectl apply -k aks/overlays/prod
```

## Promoting between environments

The repository's image-naming convention is **`furpocalypse.azurecr.io/apis:x.x.x`** with semver. For CIS Docker 4.2 compliance pin to `x.x.x@sha256:<digest>` once you've verified the image. Update `overlays/<env>/kustomization.yaml` → `images[0].newTag`.

## What's NOT in this scaffold

- **TLS cert lifecycle.** Two options sketched in `overlays/prod/patch-ingress.yaml`: KV-backed via a second SPC, or AppGW-managed listener cert. Pick one and wire it.
- **Backup / DR for the in-cluster Postgres.** No CronJob runs `pg_dump`, no Velero schedule. Critical decision before going live — easiest path is to switch Postgres to Azure Database for PostgreSQL Flexible Server, which gives you point-in-time restore out of the box.
- **Monitoring + alerting.** No `PodMonitor` / `PrometheusRule` resources. Enable the Azure Monitor add-ons and add them in a follow-up commit.
- **GitOps.** No Argo CD / Flux manifests. Add a kustomize-aware sync source when you have a GitOps platform in the cluster.
- **WAF policy resources.** `appgw.ingress.kubernetes.io/waf-policy-for-path` references an ARM-provisioned WAF policy; manage that via Terraform/Bicep alongside the AppGW.
- **Image pull secret.** Not needed — ACR pull works via the kubelet identity attached in step 2 above. If you separate clusters from the registry into different tenants, you'd add an `imagePullSecrets` reference on the ServiceAccount and a Docker config-json secret.

## Reference: where each env var comes from

| Var                               | Source                                            |
|-----------------------------------|---------------------------------------------------|
| `DJANGO_SECRET_KEY`               | Key Vault → SPC → `apis-secrets` → `envFrom`      |
| `DATABASE_PASS` / `POSTGRES_PASSWORD` | Key Vault → SPC → `apis-secrets`              |
| `BOOTSTRAP_ADMIN_PASSWORD`        | Key Vault → SPC → `apis-secrets`                  |
| `PAYPAL_*`                        | Key Vault → SPC → `apis-secrets` (creds) + ConfigMap (env flag) |
| `SQUARE_*`                        | Key Vault → SPC → `apis-secrets` (creds) + ConfigMap (env flag) |
| `MQTT_JWT_SECRET`                 | Key Vault → SPC → `apis-secrets`                  |
| `SENTRY_DSN` / `EMAIL_HOST_PASSWORD` | Key Vault → SPC → `apis-secrets`               |
| `DATABASE_HOST`, `DJANGO_REDIS_URL`, `CELERY_BROKER_URL` | `configmap-app.yaml` (base)             |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | `overlays/<env>/kustomization.yaml` configMapGenerator merge |
| `TRUSTED_PROXY_CIDRS`             | `overlays/<env>/kustomization.yaml` configMapGenerator       |
| `ENVIRONMENT_NAME` + banner       | `overlays/<env>/kustomization.yaml` configMapGenerator       |
| Image tag                         | `overlays/<env>/kustomization.yaml` `images[0].newTag`       |
