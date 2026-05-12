# APIS reverse-proxy image. Stripped down to nginx + the configs + a
# small entrypoint that renders the templated parts at boot.
#
# Image:  furpocalypse.azurecr.io/apis-nginx:x.x.x
# Builds: docker build -t furpocalypse.azurecr.io/apis-nginx:0.5.0 ./nginx
#
# This image is used in the Docker / docker-compose topology only. In AKS
# we DON'T deploy this — Application Gateway Ingress Controller terminates
# TLS at the gateway and forwards plaintext to the app service.
FROM nginx:1.27-alpine

# envsubst for templated boot-time rendering of server_name + upstream.
RUN apk add --no-cache gettext

# Drop the default site that nginx:alpine ships with so it can't accidentally
# answer requests for the wrong vhost.
RUN rm -f /etc/nginx/conf.d/default.conf

# Bake the static configs in. Cloudflare ranges are refreshable via
# ./refresh-cloudflare-ips.sh (see comment in that file); rebuild the image
# afterwards to pick up changes.
COPY nginx.conf /etc/nginx/nginx.conf
COPY realip.cloudflare.conf /etc/nginx/realip.cloudflare.conf
COPY realip.proxy.conf.template /etc/nginx/realip.proxy.conf.template
COPY realip.none.conf /etc/nginx/realip.none.conf
COPY server.cloudflare.conf /etc/nginx/server.cloudflare.conf
COPY server.proxy.conf.template /etc/nginx/server.proxy.conf.template
COPY server.none.conf /etc/nginx/server.none.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run nginx as a non-root user (CIS Docker 4.1 / ASVS V14.1). The official
# nginx:alpine image ships an `nginx` user (uid 101); we use it.
USER nginx

EXPOSE 80 443
ENTRYPOINT ["/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]

# Healthcheck — hit the local nginx listener. /healthz returns 204 (see
# the server template). Uses wget because curl isn't in the slim image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/healthz || exit 1
