#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup-monitoring.sh
#
# Generates all the config/JSON/Python files for the monitoring profile:
# InfluxDB 3 Core, Prometheus, node-exporter, Grafana — with VNET-private
# Grafana exposure, env-seeded admin password, anonymous access OFF, 1y
# retention on the apis database.
#
# Idempotent: by default skips files that already exist. Pass --force to
# overwrite.
#
# Files written (paths relative to ${TARGET}, default = current dir):
#
#   monitoring/
#   ├── prometheus.yml
#   └── grafana/
#       └── provisioning/
#           ├── datasources/
#           │   ├── influxdb.yml          (datasource: InfluxDB v3 / SQL)
#           │   └── prometheus.yml        (datasource: Prometheus)
#           └── dashboards/
#               ├── default.yml           (dashboard provider config)
#               └── apis-business-metrics.json
#                                         (starter dashboard, 6 panels)
#
#   registration/management/commands/
#   └── _influxdb_v3_reporter.py          (InfluxDB 3.x reporter, uses
#                                          the influxdb3-python SDK)
#
# After this script runs, three manual follow-ups remain — instructions
# are printed at the end:
#
#   1. Register InfluxDBv3Reporter in METRICS_BACKENDS in
#      registration/management/commands/cron_metrics.py.
#   2. Bring up the monitoring stack (see docker-compose.monitoring.yaml)
#      and create the InfluxDB operator token + apis database.
#   3. Set the monitoring env vars in .env.
#
# Usage:
#   ./setup-monitoring.sh                   # write to current dir
#   ./setup-monitoring.sh /opt/apis         # write under /opt/apis
#   ./setup-monitoring.sh --force /opt/apis # overwrite existing files
# ---------------------------------------------------------------------------

set -euo pipefail

FORCE=0
TARGET=""

for arg in "$@"; do
    case "$arg" in
        --force|-f) FORCE=1 ;;
        --help|-h)
            sed -n '2,50p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        -*) echo "unknown flag: $arg" >&2; exit 2 ;;
        *)  TARGET="$arg" ;;
    esac
done

TARGET="${TARGET:-$(pwd)}"
[ -d "$TARGET" ] || { echo "target dir does not exist: $TARGET" >&2; exit 1; }

cd "$TARGET"

write_file() {
    local path="$1"
    if [ -e "$path" ] && [ "$FORCE" -eq 0 ]; then
        printf "    \033[1;33m·\033[0m skip (exists): %s\n" "$path"
        cat >/dev/null
        return 0
    fi
    mkdir -p "$(dirname "$path")"
    cat >"$path"
    printf "    \033[1;32m✓\033[0m wrote: %s\n" "$path"
}

printf "\n\033[1;36m==>\033[0m Writing monitoring config under %s\n" "$TARGET"

# ===========================================================================
# Prometheus scrape config.
#
# Two scrape jobs:
#   - django:        scrapes django-prometheus middleware at app:81/metrics/.
#                    Requires .env to set PROMETHEUS_METRICS_EXPORT_ADDRESS
#                    =0.0.0.0 so the metrics server binds inside the
#                    container's network namespace. NO host port mapping
#                    for 81 — internal docker-network only.
#   - node:          scrapes node-exporter on the host (network_mode: host).
#                    Reachable from Prometheus (bridge network) via
#                    host.docker.internal:9100, made resolvable through
#                    `extra_hosts: host-gateway` in the compose overlay.
#   - prometheus:    self-scrape for sanity / alerting on Prometheus itself.
#
# 90-day retention is set on the prometheus CLI in docker-compose.monitoring.yaml.
# ===========================================================================
write_file monitoring/prometheus.yml <<'YAML'
global:
  scrape_interval: 30s
  scrape_timeout: 10s
  external_labels:
    deployment: apis-production

scrape_configs:
  - job_name: django
    metrics_path: /metrics/
    static_configs:
      - targets:
          - app:81

  - job_name: node
    static_configs:
      - targets:
          - host.docker.internal:9100

  - job_name: prometheus
    static_configs:
      - targets:
          - localhost:9090
YAML

# ===========================================================================
# Grafana datasource: InfluxDB 3 (SQL mode).
#
# v3 Core uses a single "database" namespace (no orgs / buckets). The
# Grafana InfluxDB datasource speaks v3 via its SQL backend over Flight
# SQL on port 8181 (HTTP) or 8182 (gRPC); we use HTTP since it's simpler
# to expose in compose without TLS plumbing.
#
# The token is read from the GRAFANA container's INFLUXDB_TOKEN env var;
# the operator generates it after first-boot (see Next-steps output).
# ===========================================================================
write_file monitoring/grafana/provisioning/datasources/influxdb.yml <<'YAML'
apiVersion: 1

datasources:
  - name: InfluxDB
    uid: apis-influxdb
    type: influxdb
    access: proxy
    url: http://influxdb:8181
    isDefault: true
    editable: false
    jsonData:
      # SQL is the v3-native query language. InfluxQL is also accepted by
      # v3 for backward compat — if your queries are simpler in InfluxQL,
      # switch this to `InfluxQL`.
      version: SQL
      dbName: apis
      httpMode: POST
      insecureGrpc: true
    secureJsonData:
      # Grafana reads $INFLUXDB_TOKEN from its container env. The token
      # is created via `influxdb3 create token --admin` after first-boot
      # of the InfluxDB container (see "Next steps" output).
      token: ${INFLUXDB_TOKEN}
YAML

write_file monitoring/grafana/provisioning/datasources/prometheus.yml <<'YAML'
apiVersion: 1

datasources:
  - name: Prometheus
    uid: apis-prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: false
    editable: false
    jsonData:
      httpMethod: POST
      timeInterval: 30s
YAML

# ===========================================================================
# Grafana dashboard provisioning + starter dashboard.
#
# Six panels:
#   1. Total completed orders (timeseries)
#   2. Badges by price level (bar chart, latest values)
#   3. Staff total / active (stat panel)
#   4. Charity donations (timeseries, USD; cents/100)
#   5. Org donations (timeseries, USD)
#   6. Django request rate (Prometheus, by view, req/s)
#
# Queries use SQL for the InfluxDB v3 panels (panels 1-5) and PromQL
# for the Prometheus panel (6). The `${event}` template variable lets
# the operator switch between events without editing the dashboard.
# ===========================================================================
write_file monitoring/grafana/provisioning/dashboards/default.yml <<'YAML'
apiVersion: 1

providers:
  - name: apis
    orgId: 1
    folder: APIS
    type: file
    disableDeletion: true
    updateIntervalSeconds: 30
    allowUiUpdates: false
    options:
      path: /etc/grafana/provisioning/dashboards
      foldersFromFilesStructure: false
YAML

write_file monitoring/grafana/provisioning/dashboards/apis-business-metrics.json <<'JSON'
{
  "annotations": {"list": []},
  "editable": false,
  "graphTooltip": 1,
  "id": null,
  "uid": "apis-business",
  "title": "APIS — Business metrics",
  "description": "Per-event business metrics emitted by registration.management.commands.cron_metrics, plus Django request rate from django-prometheus.",
  "tags": ["apis", "business"],
  "timezone": "browser",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "60s",
  "time": {"from": "now-30d", "to": "now"},
  "templating": {
    "list": [
      {
        "name": "event",
        "label": "Event",
        "type": "query",
        "datasource": {"type": "influxdb", "uid": "apis-influxdb"},
        "query": "SELECT DISTINCT event FROM orders ORDER BY event",
        "refresh": 1,
        "multi": false,
        "includeAll": false,
        "current": {}
      }
    ]
  },
  "panels": [
    {
      "id": 1,
      "type": "timeseries",
      "title": "Total completed orders",
      "datasource": {"type": "influxdb", "uid": "apis-influxdb"},
      "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8},
      "targets": [
        {
          "refId": "A",
          "rawSql": "SELECT time, count FROM orders WHERE event = '$event' AND time >= $__timeFrom AND time <= $__timeTo ORDER BY time"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []},
      "options": {"legend": {"displayMode": "list"}, "tooltip": {"mode": "single"}}
    },
    {
      "id": 2,
      "type": "barchart",
      "title": "Badges by price level (latest)",
      "datasource": {"type": "influxdb", "uid": "apis-influxdb"},
      "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8},
      "targets": [
        {
          "refId": "A",
          "rawSql": "SELECT level, last_value(count ORDER BY time) AS count FROM badge_total WHERE event = '$event' AND time >= $__timeFrom AND time <= $__timeTo GROUP BY level ORDER BY count DESC"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []},
      "options": {"orientation": "horizontal", "showValue": "always"}
    },
    {
      "id": 3,
      "type": "stat",
      "title": "Staff (total / active)",
      "datasource": {"type": "influxdb", "uid": "apis-influxdb"},
      "gridPos": {"x": 0, "y": 8, "w": 8, "h": 6},
      "targets": [
        {
          "refId": "A",
          "rawSql": "SELECT 'total' AS metric, last_value(count ORDER BY time) AS value FROM staff_total WHERE event = '$event' UNION ALL SELECT 'active' AS metric, last_value(count ORDER BY time) AS value FROM staff_active WHERE event = '$event'"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []},
      "options": {"colorMode": "value", "graphMode": "area", "textMode": "value_and_name"}
    },
    {
      "id": 4,
      "type": "timeseries",
      "title": "Charity donations (USD)",
      "datasource": {"type": "influxdb", "uid": "apis-influxdb"},
      "gridPos": {"x": 8, "y": 8, "w": 8, "h": 6},
      "targets": [
        {
          "refId": "A",
          "rawSql": "SELECT time, CAST(count AS DOUBLE) / 100.0 AS usd FROM charity_donations WHERE event = '$event' AND time >= $__timeFrom AND time <= $__timeTo ORDER BY time"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "currencyUSD"}, "overrides": []},
      "options": {"legend": {"displayMode": "list"}, "tooltip": {"mode": "single"}}
    },
    {
      "id": 5,
      "type": "timeseries",
      "title": "Org donations (USD)",
      "datasource": {"type": "influxdb", "uid": "apis-influxdb"},
      "gridPos": {"x": 16, "y": 8, "w": 8, "h": 6},
      "targets": [
        {
          "refId": "A",
          "rawSql": "SELECT time, CAST(count AS DOUBLE) / 100.0 AS usd FROM org_donations WHERE event = '$event' AND time >= $__timeFrom AND time <= $__timeTo ORDER BY time"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "currencyUSD"}, "overrides": []},
      "options": {"legend": {"displayMode": "list"}, "tooltip": {"mode": "single"}}
    },
    {
      "id": 6,
      "type": "timeseries",
      "title": "Django request rate (Prometheus, by view)",
      "datasource": {"type": "prometheus", "uid": "apis-prometheus"},
      "gridPos": {"x": 0, "y": 14, "w": 24, "h": 8},
      "targets": [
        {
          "refId": "A",
          "expr": "sum by (view) (rate(django_http_requests_total_by_view_transport_method_total[5m]))",
          "legendFormat": "{{view}}"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "reqps"}, "overrides": []},
      "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}
    }
  ]
}
JSON

# ===========================================================================
# InfluxDB 3.x reporter — standalone Python module imported by cron_metrics.
#
# Uses the influxdb3-python SDK (must be added to pyproject.toml). Writes
# are batched in-memory and flushed once per cron_metrics run so a single
# command invocation produces one HTTP round-trip, not N.
# ===========================================================================
write_file registration/management/commands/_influxdb_v3_reporter.py <<'PYTHON'
"""InfluxDB 3.x reporter for the cron_metrics command.

Replaces the legacy `InfluxDBReporter` (which targets InfluxDB 1.x — the
OSS 1.x line went EOL October 2025). Registered as `InfluxDBv3Reporter`
in METRICS_BACKENDS in cron_metrics.py — set
``APIS_METRICS_BACKEND=InfluxDBv3Reporter`` in env to switch.

Configured by environment variables:

    INFLUXDB_URL        e.g. http://influxdb:8181 (v3 default port)
    INFLUXDB_TOKEN      operator/admin token created after first-boot via
                        `influxdb3 create token --admin`
    INFLUXDB_DATABASE   target database (default: "apis")
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from django.contrib.sites.models import Site

# influxdb3-python is declared in pyproject.toml. Import at module-load
# time so a misconfigured deploy fails fast in cron_metrics.handle() rather
# than at first write() call.
from influxdb_client_3 import InfluxDBClient3, Point  # type: ignore[import-not-found]


class InfluxDBv3Reporter:
    """CronReporterABC implementation against InfluxDB 3.x.

    v3 Core has a single "database" namespace (no orgs/buckets like v2).
    Authentication is via an operator/admin token. Writes go through the
    /api/v3/write_lp endpoint via the influxdb3-python SDK.
    """

    def __init__(self, config: dict):
        # `config` is the legacy `APIS_METRICS_SETTINGS` dict, accepted for
        # signature compatibility with `InfluxDBReporter`. Every real value
        # comes from env so an operator can rotate the token without
        # redeploying.
        host = os.environ.get("INFLUXDB_URL", config.get("url", "http://influxdb:8181"))
        token = os.environ["INFLUXDB_TOKEN"]  # required
        self._database = os.environ.get(
            "INFLUXDB_DATABASE", config.get("database", "apis")
        )

        self._client = InfluxDBClient3(
            host=host,
            token=token,
            database=self._database,
        )
        self._points: list[Point] = []

    @staticmethod
    def _timestamp(now: datetime | None = None) -> datetime:
        return now if now is not None else datetime.now(tz=timezone.utc)

    def _point(self, event, topic: str, value, **kwargs) -> Point:
        site = Site.objects.get_current().domain
        point = (
            Point(topic)
            .tag("event", str(event))
            .tag("site", site)
            .field("count", int(value))
            .time(self._timestamp(kwargs.pop("timestamp", None)))
        )
        for key, val in kwargs.items():
            # Tag values must be strings.
            point = point.tag(key, str(val))
        return point

    def batch(self, event, topic: str, value, **kwargs) -> None:
        """Stage a metric for flush() — preferred entry point for
        cron_metrics, which loops over events and emits multiple points.
        """
        self._points.append(self._point(event, topic, value, **kwargs))

    def write(self, event, topic: str, value, **kwargs) -> None:
        """Write a single metric immediately — for compatibility with the
        CronReporterABC contract used by callers that don't batch.
        """
        self._client.write(record=self._point(event, topic, value, **kwargs))

    def flush(self) -> None:
        if self._points:
            self._client.write(record=self._points)
            self._points.clear()
        self._client.close()
PYTHON

# ---- Next-step instructions ---------------------------------------------

cat <<'EOF'

Done.

Three manual follow-ups remain:

  1. Register the new reporter in cron_metrics.py:

     File: registration/management/commands/cron_metrics.py

     Add this import alongside the existing ones:

         from registration.management.commands._influxdb_v3_reporter import (
             InfluxDBv3Reporter,
         )

     Then add it to the METRICS_BACKENDS registry:

         METRICS_BACKENDS = {
             "DummyReporter": DummyReporter,
             "InfluxDBReporter": InfluxDBReporter,
             "InfluxDBv3Reporter": InfluxDBv3Reporter,   # <-- add this line
         }

  2. Bring up the monitoring stack:

         docker compose \
             -f docker-compose.prod.yaml \
             -f docker-compose.monitoring.yaml \
             up -d

     Wait for InfluxDB to be healthy, then create the operator token + DB:

         # Capture the token output (it's only shown once).
         docker compose exec influxdb influxdb3 create token --admin

         # Create the apis database with 1-year retention.
         docker compose exec influxdb influxdb3 create database apis \
             --retention-period 1y \
             --token <paste-the-token-from-the-previous-command>

  3. Update .env on the VM with the captured token + monitoring vars
     (see the monitoring section added to .env.prod.example) and
     restart Grafana so it picks up the new INFLUXDB_TOKEN:

         docker compose -f docker-compose.prod.yaml -f docker-compose.monitoring.yaml \
             restart grafana

  4. Access Grafana via VNET-private path:

         From a workstation already in the VNET (Bastion / VPN / peered):
             http://<vm-private-ip>:3000

         Or via SSH port-forward from outside:
             ssh -L 3000:<vm-private-ip>:3000 azureuser@<vm-public-ip>
             # then open http://localhost:3000

     The "APIS — Business metrics" dashboard appears under the APIS
     folder once cron_metrics has been run at least once to populate
     InfluxDB.

  5. Schedule cron_metrics (e.g. via host cron):

         */10 * * * * apis docker compose -f /opt/apis/docker-compose.prod.yaml \
             -f /opt/apis/docker-compose.monitoring.yaml \
             exec -T app /app/manage.py cron_metrics >/dev/null 2>&1

EOF
