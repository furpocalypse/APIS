#!/bin/sh
# Render the mode-specific http-level realip include and the server
# block(s), validate, then exec nginx.
set -eu

TRUSTED_PROXY_MODE="${TRUSTED_PROXY_MODE:-cloudflare}"
NGINX_UPSTREAM="${NGINX_UPSTREAM:-app:8000}"
NGINX_SERVER_NAMES="${NGINX_SERVER_NAMES:-_}"

REALIP_OUT=/tmp/realip-http.conf
SERVER_OUT=/tmp/server.conf

case "$TRUSTED_PROXY_MODE" in
    cloudflare)
        if [ ! -r /app/certs/fullchain.pem ] || [ ! -r /app/certs/privkey.pem ]; then
            echo "FATAL: TRUSTED_PROXY_MODE=cloudflare requires" >&2
            echo "       /app/certs/fullchain.pem and /app/certs/privkey.pem" >&2
            echo "       to be bind-mounted into the container." >&2
            echo "  Mount your cert directory in compose:" >&2
            echo "    volumes:" >&2
            echo "      - /opt/apis/certs:/app/certs:ro" >&2
            exit 2
        fi
        # Http-level realip + CF source allowlist.
        cp /etc/nginx/realip.cloudflare.conf "$REALIP_OUT"
        # Server block with TLS terminator + the cf_source_ip guard.
        export NGINX_SERVER_NAMES NGINX_UPSTREAM
        envsubst '${NGINX_SERVER_NAMES} ${NGINX_UPSTREAM}' \
            < /etc/nginx/server.cloudflare.conf > "$SERVER_OUT"
        ;;

    proxy)
        if [ -z "${TRUSTED_PROXY_CIDRS:-}" ]; then
            echo "FATAL: TRUSTED_PROXY_MODE=proxy requires TRUSTED_PROXY_CIDRS" >&2
            echo "       (comma-separated list of upstream proxy CIDRs)" >&2
            exit 2
        fi

        CLIENT_IP_HEADER="${CLIENT_IP_HEADER:-X-Forwarded-For}"
        REAL_IP_RECURSIVE="${REAL_IP_RECURSIVE:-on}"

        # Build set_real_ip_from directives.
        TRUSTED_PROXY_CIDRS_DIRECTIVES=""
        OLD_IFS="$IFS"
        IFS=','
        for cidr in $TRUSTED_PROXY_CIDRS; do
            cidr=$(echo "$cidr" | tr -d ' \t')
            [ -n "$cidr" ] && \
                TRUSTED_PROXY_CIDRS_DIRECTIVES="${TRUSTED_PROXY_CIDRS_DIRECTIVES}set_real_ip_from ${cidr};
"
        done
        IFS="$OLD_IFS"

        export TRUSTED_PROXY_CIDRS_DIRECTIVES CLIENT_IP_HEADER REAL_IP_RECURSIVE
        envsubst '${TRUSTED_PROXY_CIDRS_DIRECTIVES} ${CLIENT_IP_HEADER} ${REAL_IP_RECURSIVE}' \
            < /etc/nginx/realip.proxy.conf.template > "$REALIP_OUT"

        export NGINX_SERVER_NAMES NGINX_UPSTREAM
        envsubst '${NGINX_SERVER_NAMES} ${NGINX_UPSTREAM}' \
            < /etc/nginx/server.proxy.conf.template > "$SERVER_OUT"
        ;;

    none)
        cp /etc/nginx/realip.none.conf "$REALIP_OUT"
        # server.none.conf has no envsubst tokens for SERVER_NAMES (just
        # uses `_`) but does have ${NGINX_UPSTREAM} for proxy_pass.
        export NGINX_UPSTREAM
        envsubst '${NGINX_UPSTREAM}' \
            < /etc/nginx/server.none.conf > "$SERVER_OUT"
        ;;

    *)
        echo "FATAL: TRUSTED_PROXY_MODE must be 'cloudflare', 'proxy', or 'none' (got: ${TRUSTED_PROXY_MODE})" >&2
        exit 2
        ;;
esac

# Validate before exec. Failing fast here is far less painful than nginx
# half-starting and the container looking healthy from outside.
nginx -t -c /etc/nginx/nginx.conf

# Hand off. CMD from the Dockerfile is `nginx -g 'daemon off;'`.
exec "$@"
