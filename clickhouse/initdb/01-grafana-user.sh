#!/bin/bash
# Runs once, on the first start of an empty ClickHouse volume.
# Grafana gets its own read-only user: a dashboard never needs to write.
set -euo pipefail
clickhouse client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --multiquery <<SQL
CREATE USER IF NOT EXISTS grafana IDENTIFIED WITH sha256_password BY '${GRAFANA_CH_PASSWORD}'
    SETTINGS readonly = 2;  -- 2 = no writes, but may set per-query settings (Grafana sets a timeout)
GRANT SELECT ON quakes.* TO grafana;
SQL
