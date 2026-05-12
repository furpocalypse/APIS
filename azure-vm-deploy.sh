#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# azure-vm-deploy.sh
#
# Brings up the APIS production stack on an Azure VM. Idempotent — safe to
# re-run. Performs steps 2–5 from the deploy checklist:
#
#   2. Ensure ${APIS_DATA_DIR}/{postgres,redis} exist with uid 999:999
#   3. Authenticate the VM to Azure Container Registry (Managed Identity
#      preferred; falls back to existing `az login` session)
#   4. `docker compose pull` + `up -d`, wait for the app container to report
#      `healthy` (HEALTHCHECK is baked into the image)
#   5. `bootstrap_admin --from-env` — idempotent (the command refuses to
#      create a second superuser if one already exists)
#
# Step 1 (attach + format + mount the Azure managed disk at ${APIS_DATA_DIR})
# is assumed to be done before this runs. The script *warns* if the data
# dir isn't a real mountpoint but doesn't refuse — useful for first-boot
# experimentation, dangerous in real production.
#
# Step 6 (smoke test) is printed as a `Next:` hint at the end.
#
# Usage:
#   ./azure-vm-deploy.sh                  # use defaults
#   COMPOSE_DIR=/srv/apis ./azure-vm-deploy.sh
#   ACR_NAME=furpocalypse ./azure-vm-deploy.sh
#
# Run as a non-root user that:
#   - is in the `docker` group (can run `docker compose` without sudo), AND
#   - has passwordless sudo for `mkdir -p` and `chown` (or be prepared to
#     enter your sudo password once).
# ---------------------------------------------------------------------------

set -euo pipefail

# ---- configuration (override via env) ------------------------------------

COMPOSE_DIR="${COMPOSE_DIR:-/opt/apis}"
COMPOSE_FILE="${COMPOSE_FILE:-${COMPOSE_DIR}/docker-compose.yaml}"
ACR_NAME="${ACR_NAME:-furpocalypse}"
HEALTH_TIMEOUT_SECS="${HEALTH_TIMEOUT_SECS:-90}"

# ---- helpers -------------------------------------------------------------

bold()   { printf "\033[1m%s\033[0m" "$*"; }
log()    { printf "\n\033[1;36m==>\033[0m %s\n" "$*"; }
ok()     { printf "    \033[1;32m✓\033[0m %s\n" "$*"; }
warn()   { printf "    \033[1;33m!\033[0m %s\n" "$*"; }
err()    { printf "\n\033[1;31m!!!\033[0m %s\n" "$*" >&2; }

# Read a key=value pair from a .env file (strips matching quotes).
read_env() {
    local key="$1" file="$2"
    [ -f "$file" ] || return 1
    awk -F= -v k="$key" '$1==k{
        sub(/^[^=]+=/, "", $0)
        gsub(/^["'"'"']|["'"'"']$/, "", $0)
        print
    }' "$file" | tail -1
}

# ---- pre-flight ----------------------------------------------------------

log "Pre-flight checks"

command -v docker >/dev/null 2>&1 \
    || { err "docker is not installed."; exit 1; }
docker compose version >/dev/null 2>&1 \
    || { err "docker compose plugin missing (try: sudo apt-get install docker-compose-plugin)."; exit 1; }
command -v az >/dev/null 2>&1 \
    || { err "az CLI is not installed (try: curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash)."; exit 1; }
ok "docker, compose, az CLI present"

[ -f "$COMPOSE_FILE" ]                  || { err "compose file not found: $COMPOSE_FILE"; exit 1; }
[ -f "${COMPOSE_DIR}/.env" ]            || { err ".env not found: ${COMPOSE_DIR}/.env (copy .env.prod.example, fill in real values)"; exit 1; }
[ -f "${COMPOSE_DIR}/database.env" ]    || { err "database.env not found: ${COMPOSE_DIR}/database.env"; exit 1; }
ok "compose file + .env + database.env present"

# Refuse to deploy if .env still has known-bad placeholder values.
declare -a PLACEHOLDERS=(
    "supersecretcrypt"
    "your-strong-password-here"
    "ProdTestAdmin-2026-Strong!"
    "local-prodtest-secret-do-not-use"
    "InsertSecretHere"
    "REPLACE_ME"
)
for ph in "${PLACEHOLDERS[@]}"; do
    if grep -qF "$ph" "${COMPOSE_DIR}/.env"; then
        err ".env contains the placeholder value '${ph}' — refusing to deploy."
        err "Replace every placeholder with a real production secret first."
        exit 1
    fi
done
ok ".env has no known placeholder secrets"

# Find APIS_DATA_DIR (env override > .env > default in compose).
DATA_DIR="${APIS_DATA_DIR:-$(read_env APIS_DATA_DIR "${COMPOSE_DIR}/.env" || true)}"
DATA_DIR="${DATA_DIR:-/srv/apis}"

if mountpoint -q "$DATA_DIR" 2>/dev/null; then
    ok "APIS_DATA_DIR=${DATA_DIR} is a real mountpoint (good — managed disk)"
elif [ -d "$DATA_DIR" ]; then
    warn "APIS_DATA_DIR=${DATA_DIR} exists but is NOT a separate mountpoint."
    warn "Data will live on the VM's OS disk — VM rebuilds will lose state."
    warn "If this is production, abort, attach a managed disk, and re-run."
    warn "Continuing in 5 seconds..."
    sleep 5
else
    warn "APIS_DATA_DIR=${DATA_DIR} does not exist yet — will be created."
fi

# ---- Step 2: data directory ownership ------------------------------------

log "Step 2 — ${bold "data directories"}"

for sub in postgres redis; do
    sudo mkdir -p "${DATA_DIR}/${sub}"
    # postgres:16 image runs as uid 999 (postgres). redis:8 image also runs
    # as uid 999. So a single chown covers both.
    sudo chown 999:999 "${DATA_DIR}/${sub}"
    sudo chmod 700 "${DATA_DIR}/${sub}"
    ok "${DATA_DIR}/${sub}  (owner 999:999, mode 700)"
done

# ---- Step 3: Azure Container Registry login ------------------------------

log "Step 3 — ${bold "authenticate to ACR (${ACR_NAME})"}"

# Prefer the VM's system-assigned Managed Identity. The VM must have AcrPull
# assigned on the registry. If MI isn't available, fall back to any existing
# user-context az session (run `az login` manually first time).
if az account show >/dev/null 2>&1; then
    ok "already authenticated to Azure as: $(az account show --query user.name -o tsv 2>/dev/null || echo unknown)"
elif az login --identity --allow-no-subscriptions >/dev/null 2>&1; then
    ok "signed in via VM Managed Identity"
else
    err "No Azure credentials. Either:"
    err "  - Assign a system-assigned Managed Identity to this VM with the"
    err "    AcrPull role on the ${ACR_NAME} registry, OR"
    err "  - Run 'az login' interactively as a privileged user once, then re-run."
    exit 1
fi

if ! az acr login --name "$ACR_NAME" >/dev/null 2>&1; then
    err "Failed to authenticate to ${ACR_NAME}.azurecr.io"
    err "Most likely cause: the identity in use lacks the AcrPull role assignment"
    err "on the ${ACR_NAME} registry. Verify with:"
    err "  az role assignment list --assignee <principal-id> --scope <acr-resource-id>"
    exit 1
fi
ok "authenticated to ${ACR_NAME}.azurecr.io"

# ---- Step 4: pull + up + wait for healthy --------------------------------

log "Step 4 — ${bold "pull image and start the stack"}"

cd "$COMPOSE_DIR"

docker compose -f "$COMPOSE_FILE" pull
ok "image pulled"

docker compose -f "$COMPOSE_FILE" up -d
ok "compose up -d issued"

# Resolve the app container ID by compose service name (handles arbitrary
# project naming derived from COMPOSE_DIR).
APP_CID="$(docker compose -f "$COMPOSE_FILE" ps --quiet app | head -1)"
if [ -z "$APP_CID" ]; then
    err "app container did not appear — check: docker compose -f $COMPOSE_FILE ps"
    exit 1
fi

printf "    waiting for healthy (timeout %ss)" "$HEALTH_TIMEOUT_SECS"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECS ))
status=""
while [ "$(date +%s)" -lt "$deadline" ]; do
    status="$(docker inspect --format '{{.State.Health.Status}}' "$APP_CID" 2>/dev/null || echo "missing")"
    case "$status" in
        healthy)   printf "\n"; ok "app container is healthy"; break ;;
        unhealthy)
            printf "\n"
            err "app container reports unhealthy. Check:"
            err "  docker compose -f $COMPOSE_FILE logs --tail 100 app"
            exit 1 ;;
        starting|"")  printf "." ;;
        *)
            printf "\n"
            err "unexpected health status: $status"
            exit 1 ;;
    esac
    sleep 5
done

if [ "$status" != "healthy" ]; then
    printf "\n"
    err "Container failed to reach healthy after ${HEALTH_TIMEOUT_SECS}s."
    err "Logs:  docker compose -f $COMPOSE_FILE logs --tail 100 app"
    exit 1
fi

# ---- Step 5: bootstrap admin (idempotent) --------------------------------

log "Step 5 — ${bold "bootstrap initial admin (idempotent)"}"

# bootstrap_admin --from-env reads BOOTSTRAP_ADMIN_{USERNAME,EMAIL,PASSWORD}
# from the container env (sourced from .env). If a superuser already exists
# the command short-circuits with success and a "nothing to do" message —
# so re-running this script is safe after the first deploy.
if docker compose -f "$COMPOSE_FILE" exec -T app /app/manage.py bootstrap_admin --from-env; then
    ok "bootstrap_admin completed"
else
    rc=$?
    err "bootstrap_admin failed with exit code $rc."
    err "Verify BOOTSTRAP_ADMIN_USERNAME / BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD"
    err "are set in ${COMPOSE_DIR}/.env, and the password is >= 12 chars + not in the"
    err "common-password list (AUTH_PASSWORD_VALIDATORS will reject weak passwords)."
    exit "$rc"
fi

# ---- Done — print next-step hints ---------------------------------------

cat <<EOF

$(bold "Done.") The stack is up and healthy.

Next:
  1. Smoke-test from the VM itself:
       curl -fsS -H 'X-Forwarded-Proto: https' -H 'X-Forwarded-For: 127.0.0.1' \\
            https://localhost/robots.txt -k

  2. ${bold "Scrub the bootstrap password from ${COMPOSE_DIR}/.env"} now that the
     admin exists. The bootstrap command unsets BOOTSTRAP_ADMIN_PASSWORD
     from os.environ at runtime, but the value is still on disk. Either
     comment it out or rotate it to an obviously-invalid placeholder so
     re-runs of this script still pass (bootstrap_admin is idempotent and
     refuses to elevate further once a superuser exists).

  3. Log in at:
       https://<vm-public-ip>/accounts/login/  (or via the Cloudflare hostname)
       (or via your AppGW / Front Door once that's wired)

  4. To inspect or follow logs:
       docker compose -f $COMPOSE_FILE logs -f app
       docker compose -f $COMPOSE_FILE ps

EOF
