#!/usr/bin/env bash
# Regenerate cloudflare-ips.conf from Cloudflare's published CIDR list.
# Run this periodically (every few months — the list changes rarely but
# does change). After running, rebuild and push the nginx image.
#
# Usage:
#   ./nginx/refresh-cloudflare-ips.sh
#
# Exits non-zero if the fetched list looks wrong (e.g. empty, or too few
# entries) so it can be safely wired into CI.
set -euo pipefail

cd "$(dirname "$0")"

V4_URL="https://www.cloudflare.com/ips-v4"
V6_URL="https://www.cloudflare.com/ips-v6"
OUT="cloudflare-ips.conf"

echo "Fetching $V4_URL ..."
V4_RANGES=$(curl -sfL "$V4_URL")
echo "Fetching $V6_URL ..."
V6_RANGES=$(curl -sfL "$V6_URL")

V4_COUNT=$(printf '%s\n' "$V4_RANGES" | grep -c .)
V6_COUNT=$(printf '%s\n' "$V6_RANGES" | grep -c .)

# Sanity: Cloudflare has historically had ~15 v4 and ~7 v6 ranges. Refuse
# to overwrite the file if the fetch returned suspiciously little.
if [ "$V4_COUNT" -lt 10 ] || [ "$V6_COUNT" -lt 5 ]; then
    echo "ERROR: fetched lists look truncated (v4=$V4_COUNT v6=$V6_COUNT). Aborting." >&2
    exit 1
fi

TODAY=$(date -u +%Y-%m-%d)

{
    cat <<EOF
# Cloudflare CIDR ranges (IPv4 + IPv6).
#
# Source: ${V4_URL} and ${V6_URL}
# Last refreshed: ${TODAY}
# Regenerate with:  ./nginx/refresh-cloudflare-ips.sh
#
# Two uses:
#   1. \`set_real_ip_from\` (in http block) — tells the realip module
#      which upstream IPs are allowed to rewrite \$remote_addr from the
#      CF-Connecting-IP header.
#   2. \`geo \$cf_trusted_source\` — drives a 403 in the server block
#      so direct origin hits (anyone bypassing Cloudflare by guessing
#      the origin IP) get refused. Defense-in-depth on top of (1).

# --- realip module: trust CF-Connecting-IP from these networks ---------

EOF
    # set_real_ip_from for each v4 and v6 range
    printf '%s\n' "$V4_RANGES" | sed -E 's|^|set_real_ip_from |;s|$|;|'
    echo
    printf '%s\n' "$V6_RANGES" | sed -E 's|^|set_real_ip_from |;s|$|;|'
    echo

    cat <<'EOF'
real_ip_header    CF-Connecting-IP;
real_ip_recursive on;

# --- IP allowlist for the server block (mapped to $cf_trusted_source) --
# Reads $realip_remote_addr — the *original* peer IP before the realip
# rewrite. So this checks "is the TCP source a Cloudflare edge IP?"
# rather than "is the original client a Cloudflare IP?" (which would be
# circular).

geo $realip_remote_addr $cf_trusted_source {
    default 0;

EOF
    printf '%s\n' "$V4_RANGES" | sed -E 's|^|    |;s|$|     1;|'
    echo
    printf '%s\n' "$V6_RANGES" | sed -E 's|^|    |;s|$|     1;|'
    echo "}"
} > "$OUT.tmp"

mv "$OUT.tmp" "$OUT"
echo "Wrote $(realpath "$OUT") (v4=$V4_COUNT v6=$V6_COUNT)"
