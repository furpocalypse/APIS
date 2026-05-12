# APIS reverse-proxy image

A standalone nginx container that fronts the APIS app pod. Used in the
docker-compose topology only; **NOT** deployed in AKS (Application
Gateway Ingress Controller terminates TLS there and reaches gunicorn
directly).

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | `nginx:1.27-alpine` + the configs + entrypoint. Non-root `nginx` user (uid 101). |
| `nginx.conf` | HTTP preamble (mime, logging, TLS profile, temp paths, upstream pool, include). Mode-specific server block is included from `/tmp/server.conf`. |
| `cloudflare-ips.conf` | Static list of Cloudflare CIDRs. Provides `set_real_ip_from` directives + a `geo $cf_trusted_source` block. Refresh via `./refresh-cloudflare-ips.sh`. |
| `server.cloudflare.conf` | `TRUSTED_PROXY_MODE=cloudflare` — HTTP→HTTPS 301 + HTTPS terminator with CF-IP allowlist and the cert at `/app/certs/`. |
| `server.proxy.conf.template` | `TRUSTED_PROXY_MODE=proxy` — HTTP only (TLS terminates upstream), set_real_ip_from from `TRUSTED_PROXY_CIDRS`. |
| `server.none.conf` | `TRUSTED_PROXY_MODE=none` — HTTP only, no trust enforcement. Local testing only. |
| `entrypoint.sh` | Picks the right server config, substitutes env vars, validates, execs nginx. |
| `refresh-cloudflare-ips.sh` | Regenerates `cloudflare-ips.conf` from `cloudflare.com/ips-{v4,v6}`. |

## Modes

| `TRUSTED_PROXY_MODE` | Front | Listener | Real IP from | Origin lock |
|---|---|---|---|---|
| `cloudflare` (default) | Cloudflare proxied DNS | 80 (redirect) + 443 (TLS) | `CF-Connecting-IP` | Cloudflare CIDR allowlist (403 on miss) |
| `proxy` | AppGW / Front Door / etc | 80 | `X-Forwarded-For` from `TRUSTED_PROXY_CIDRS` | None (upstream enforces) |
| `none` | direct exposure | 80 | TCP peer IP | None — local testing only |

## Required env vars

- `TRUSTED_PROXY_MODE` — `cloudflare` (default), `proxy`, or `none`.
- `NGINX_SERVER_NAMES` — space-separated FQDNs for the HTTPS listener (cloudflare mode) or HTTP listener (proxy mode). Must match the cert's SANs. Default `_` (catch-all, only works for catch-all certs).
- `NGINX_UPSTREAM` — `host:port` of the app pool. Default `app:8000` (the docker-compose service name).
- `TRUSTED_PROXY_CIDRS` — required when `mode=proxy`. Comma-separated CIDR list.
- `CLIENT_IP_HEADER` — defaults to `X-Forwarded-For`. Override for Front Door (`X-Azure-ClientIP`).
- `REAL_IP_RECURSIVE` — defaults to `on`. Walk XFF right-to-left.

## Required volume mounts

When `TRUSTED_PROXY_MODE=cloudflare`:

```yaml
volumes:
  - /opt/apis/certs:/app/certs:ro
```

The directory must contain `fullchain.pem` and `privkey.pem`. Either:

- **Cloudflare Origin Certificate** — issued in the Cloudflare dashboard (15-year validity, only trusted by the Cloudflare edge). Download the cert and key, rename to `fullchain.pem` / `privkey.pem`. Set Cloudflare SSL mode to "Full (Strict)".
- **Let's Encrypt** — `certbot --dns-cloudflare` for DNS-01 (port 80 isn't reachable through the proxy). Symlink the live files into `/opt/apis/certs/`.

start.sh refuses to start in `cloudflare` mode if either file is missing.

## Building

```bash
docker build -t furpocalypse.azurecr.io/apis-nginx:0.5.1 ./nginx
docker push furpocalypse.azurecr.io/apis-nginx:0.5.1
```

## Refreshing the Cloudflare IP list

Cloudflare publishes the canonical list at `cloudflare.com/ips-v4` and
`ips-v6`. The list changes rarely (years between updates) but should be
refreshed periodically.

```bash
./nginx/refresh-cloudflare-ips.sh   # updates cloudflare-ips.conf in place
docker build -t furpocalypse.azurecr.io/apis-nginx:<next-tag> ./nginx
docker push furpocalypse.azurecr.io/apis-nginx:<next-tag>
```

The script refuses to overwrite the file if the fetched lists look
truncated (sanity check).
