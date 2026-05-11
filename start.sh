#!/bin/sh
set -eu

# --------------------------------------------------------------------------
# Render /app/realip.conf from the requested TRUSTED_PROXY_MODE.
# nginx.conf includes /app/realip.conf to pick up real-client-IP plumbing
# and the optional Front Door origin lock. See CLAUDE.md for the security
# contract this enforces.
# --------------------------------------------------------------------------

TRUSTED_PROXY_MODE="${TRUSTED_PROXY_MODE:-none}"
# Render the realip include into /tmp (which is the tmpfs the orchestrator
# mounts — see docker-compose.yaml or docker-compose.prod.yaml). /app is read-only at runtime
# under CIS Docker 5.12. nginx.conf `include`s this file by absolute path.
REALIP_OUT=/tmp/realip.conf

case "$TRUSTED_PROXY_MODE" in
    none)
        cp /app/nginx.realip.none.conf "$REALIP_OUT"
        ;;

    proxy)
        if [ -z "${TRUSTED_PROXY_CIDRS:-}" ]; then
            echo "FATAL: TRUSTED_PROXY_MODE=proxy requires TRUSTED_PROXY_CIDRS" >&2
            echo "       (comma-separated list of upstream proxy CIDRs)" >&2
            exit 2
        fi

        CLIENT_IP_HEADER="${CLIENT_IP_HEADER:-X-Forwarded-For}"
        REAL_IP_RECURSIVE="${REAL_IP_RECURSIVE:-on}"

        # Build set_real_ip_from directives from the comma-separated CIDR list.
        TRUSTED_PROXY_CIDRS_DIRECTIVES=""
        OLD_IFS="$IFS"
        IFS=','
        for cidr in $TRUSTED_PROXY_CIDRS; do
            # Strip whitespace.
            cidr=$(echo "$cidr" | tr -d ' \t')
            if [ -n "$cidr" ]; then
                TRUSTED_PROXY_CIDRS_DIRECTIVES="${TRUSTED_PROXY_CIDRS_DIRECTIVES}set_real_ip_from ${cidr};
"
            fi
        done
        IFS="$OLD_IFS"

        # Build the FDID enforcement block. When FRONT_DOOR_ID is set, requests
        # must arrive from a trusted proxy IP AND carry a matching X-Azure-FDID.
        # When unset, $trusted_origin is unconditionally 1.
        if [ -n "${FRONT_DOOR_ID:-}" ]; then
            FDID_BLOCK="geo \$remote_addr \$trusted_origin_ip {
    default 0;
"
            OLD_IFS="$IFS"
            IFS=','
            for cidr in $TRUSTED_PROXY_CIDRS; do
                cidr=$(echo "$cidr" | tr -d ' \t')
                if [ -n "$cidr" ]; then
                    FDID_BLOCK="${FDID_BLOCK}    ${cidr} 1;
"
                fi
            done
            IFS="$OLD_IFS"
            FDID_BLOCK="${FDID_BLOCK}}

map \"\$trusted_origin_ip:\$http_x_azure_fdid\" \$trusted_origin {
    default                       0;
    \"1:${FRONT_DOOR_ID}\"        1;
}"
        else
            FDID_BLOCK="geo \$trusted_origin { default 1; }"
        fi

        export TRUSTED_PROXY_CIDRS_DIRECTIVES CLIENT_IP_HEADER REAL_IP_RECURSIVE FDID_BLOCK
        # Strict allowlist: envsubst only expands these four tokens. nginx's
        # own $variables (\$scheme, \$remote_addr, etc.) pass through.
        envsubst '${TRUSTED_PROXY_CIDRS_DIRECTIVES} ${CLIENT_IP_HEADER} ${REAL_IP_RECURSIVE} ${FDID_BLOCK}' \
            < /app/nginx.realip.proxy.conf.template > "$REALIP_OUT"
        ;;

    *)
        echo "FATAL: TRUSTED_PROXY_MODE must be 'none' or 'proxy' (got: ${TRUSTED_PROXY_MODE})" >&2
        exit 2
        ;;
esac

# Validate the assembled nginx config before we hand off to supervisord.
# Failing fast here is far less painful than nginx half-starting under
# supervisord and the eventlistener killing the container.
nginx -t -c /app/nginx.conf

./manage.py migrate

case ${1:-} in
    worker)
        celery -A fm_eventmanager worker --loglevel=info
        ;;

    *)
        exec /usr/bin/supervisord -c /app/supervisord.conf
        ;;
esac
