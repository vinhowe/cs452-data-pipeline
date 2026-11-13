#!/bin/bash
# Run one ClickHouse query from the host:  ./q.sh "SELECT count() FROM quakes.events FINAL"
# Reads the password from .env without echoing it; the password travels in a header, not the URL.
set -euo pipefail
cd "$(dirname "$0")"
pw=$(grep '^CLICKHOUSE_PASSWORD=' .env | cut -d= -f2-)
q="$1"
# Only read queries take an output format; GRANT/INSERT/DELETE would choke on one.
if [[ "${q^^}" =~ ^[[:space:]]*(SELECT|WITH|SHOW|DESCRIBE|EXPLAIN) ]]; then q="$q FORMAT PrettyCompactMonoBlock"; fi
curl -sS http://127.0.0.1:8123/ -H "X-ClickHouse-User: pipeline" -H "X-ClickHouse-Key: $pw" --data-binary "$q"
