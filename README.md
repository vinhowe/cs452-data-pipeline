# USGS Earthquake Pipeline (CS 452 Data Pipeline project)

Bulk-loads the USGS earthquake catalog since 1970, then keeps itself current every
5 minutes with nobody pushing a button. Everything runs locally in Docker.

```
USGS FDSN API ──(once)──> download_history.py ──> data/raw/*.csv.gz ──> load_seed.py ──┐
                                                                                        ├─> ClickHouse ──> Grafana
USGS live feed (all_day.csv) ──(every 5 min)──> ingester container (feed | backfill | revisions) ──┘   quakes.events
USGS FDSN API (updatedafter / gap backfill) ─────────┘                                           quakes.ingest_runs
```

| Stage | Choice |
|---|---|
| 1. Source | USGS Earthquakes: FDSN API for history, `all_day.csv` summary feed for live |
| 2. Ingestion | Python loop in a Docker container (`restart: unless-stopped`), every 5 min |
| 3. Buffer | Skipped: direct writes are fine at ~300 events/hour |
| 4. Store | ClickHouse 25.3, `ReplacingMergeTree(updated)` |
| 5. Liveness | Grafana 11.6 dashboard + `ingest_runs` ledger |

## Run it

```bash
docker compose up -d --build                    # clickhouse, ingester, grafana (all on 127.0.0.1)
.venv/bin/python -m seed.download_history --end 2026-11-13   # once; resumable; ~10 min
set -a; . ./.env; set +a; .venv/bin/python -m seed.load_seed  # bulk load from disk
```

Dashboard: http://127.0.0.1:3000 (view-only without login). Ad-hoc SQL: `./q.sh "SELECT ..."`.
From other devices on the tailnet (tailnet-only, not Funnel):
`tailscale serve --bg --https=3443 http://127.0.0.1:3000` → `https://<host>.<tailnet>.ts.net:3443`;
turn off with `tailscale serve --https=3443 off`.
Evidence queries for the demo and the follow-up: [`sql/evidence.sql`](sql/evidence.sql).
Tests: `.venv/bin/python -m pytest tests`.

`.env` (git-ignored, mode 600) holds three randomly generated local passwords:
`CLICKHOUSE_PASSWORD` (ingester), `GRAFANA_CH_PASSWORD` (Grafana's read-only ClickHouse user),
`GRAFANA_ADMIN_PASSWORD`.

## How it handles the four requirements

**Idempotency.** Each run compares fetched `(id, updated)` pairs with what's stored and inserts
only new ids and newer revisions. Re-running the same batch inserts nothing. ClickHouse's
`ReplacingMergeTree(updated)` is the safety net. It collapses duplicates in background merges, so
exact queries use `FINAL`. The seed load skips the comparison and relies on the engine alone.

**Gaps and failures.** Every attempt, including failed ones, is a row in `quakes.ingest_runs`.
The watermark is the end of the last *successful* run, so a failed run or a powered-off machine
leads to a catch-up, not a hole. Under 23 h behind: the 24-hour feed covers it. Over 23 h: page
through FDSN from the watermark, splitting any window that hits the 20,000-event limit. Every 6 h
a revisions sweep (`updatedafter`) catches corrections to older quakes.

**Secrets.** Generated into `.env`, passed to containers as environment variables, sent to
ClickHouse in HTTP headers (not URLs). Grafana uses a separate read-only user.

**Cost.** $0. Everything runs on this machine. USGS needs no key.

## Layout

| Path | What |
|---|---|
| `pipeline/usgs.py` | Fetch + parse, shared by the seed and the ingester |
| `pipeline/ingest.py` | One run: watermark, feed vs. backfill, revisions sweep, dedup, ledger |
| `pipeline/scheduler.py` | The always-on loop in the container |
| `pipeline/ch.py` | Minimal ClickHouse HTTP client |
| `seed/` | One-time download and bulk load |
| `sql/schema.sql`, `sql/insert_usgs_csv.sql` | Tables, and the one insert used by every path |
| `grafana/build_dashboard.py` | Generates the dashboard JSON |
| `docs/` | Pass-off document |
