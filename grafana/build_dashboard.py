"""Generates grafana/dashboards/quakes.json. Edit the SQL here, then: python grafana/build_dashboard.py"""
import json
from pathlib import Path

DS = {"type": "grafana-clickhouse-datasource", "uid": "clickhouse"}
LIVE = "mode IN ('feed','backfill')"


def target(sql):
    return {"refId": "A", "datasource": DS, "editorType": "sql", "queryType": "table", "format": 1,
            "rawSql": " ".join(sql.split())}


def panel(pid, title, kind, sql, x, y, w, h, desc="", **extra):
    p = {"id": pid, "type": kind, "title": title, "description": desc, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": [target(sql)],
         "fieldConfig": {"defaults": {}, "overrides": []}, "options": {}}
    p.update(extra)
    return p


def stat(pid, title, sql, x, desc="", unit=None, thresholds=None, decimals=None):
    d = {"color": {"mode": "thresholds"},
         "thresholds": {"mode": "absolute", "steps": thresholds or [{"color": "blue", "value": None}]}}
    if unit:
        d["unit"] = unit
    if decimals is not None:
        d["decimals"] = decimals
    return panel(pid, title, "stat", sql, x, 0, 3, 4, desc,
                 fieldConfig={"defaults": d, "overrides": []},
                 options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                          "colorMode": "background", "graphMode": "none", "textMode": "value"})


panels = [
    stat(1, "Events stored", "SELECT count() FROM quakes.events FINAL", 0,
         "Distinct earthquakes after dedup (FINAL collapses revisions)."),
    stat(2, "New quakes, last hour", f"""SELECT sum(inserted) FROM quakes.ingest_runs
         WHERE status='ok' AND {LIVE} AND started_at > now() - INTERVAL 1 HOUR""", 3,
         "Never-seen-before events added by unattended runs in the last 60 minutes."),
    stat(3, "Minutes since last good run", """SELECT dateDiff('second', max(finished_at), now64(3)) / 60
         FROM quakes.ingest_runs WHERE status='ok'""", 6,
         "The staleness alarm. The scheduler runs every 5 minutes, so >15 means something is wrong.",
         unit="m", decimals=1, thresholds=[{"color": "green", "value": None},
                                           {"color": "orange", "value": 15}, {"color": "red", "value": 60}]),
    stat(4, "Failed runs, last 24h", """SELECT count() FROM quakes.ingest_runs
         WHERE status='error' AND started_at > now() - INTERVAL 1 DAY""", 9,
         "Failures are recorded, not swallowed. The next run re-covers the window.",
         thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}]),
    stat(5, "Newest quake", "SELECT toUnixTimestamp64Milli(max(time)) FROM quakes.events", 12,
         "Origin time of the most recent event stored.", unit="dateTimeFromNow"),
    stat(6, "Ingest runs, last 24h", """SELECT count() FROM quakes.ingest_runs
         WHERE status='ok' AND started_at > now() - INTERVAL 1 DAY""", 15,
         "Expected about 288 feed runs a day (every 5 min) plus 4 revision sweeps."),
    stat(12, "Disk free", "SELECT min(free_space) FROM system.disks", 18,
         "Free space on the drive holding ClickHouse's data (the host disk).", unit="bytes", decimals=0,
         thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 10e9},
                     {"color": "green", "value": 25e9}]),
    stat(13, "ClickHouse on disk", "SELECT sum(bytes_on_disk) FROM system.parts WHERE active", 21,
         "Everything ClickHouse stores, including its own 3-day system logs.", unit="bytes", decimals=1),

    panel(7, "It's alive: rows written by each unattended run", "timeseries",
          f"""SELECT started_at AS time, inserted AS new_quakes, updated AS revised
              FROM quakes.ingest_runs WHERE $__timeFilter(started_at) AND status='ok' AND {LIVE}
              ORDER BY time""", 0, 4, 14, 9,
          "One bar pair per scheduled run (every 5 min). Nobody pushes a button. Log scale, so the "
          "first run's 24-hour catch-up doesn't flatten the normal 1-5 new quakes per run.",
          fieldConfig={"defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 70, "barAlignment": 0,
                                               "lineWidth": 1, "showPoints": "always", "pointSize": 4,
                                               "scaleDistribution": {"type": "log", "log": 10}}},
                       "overrides": []},
          options={"legend": {"displayMode": "list", "placement": "bottom"},
                   "tooltip": {"mode": "multi"}}),
    panel(8, "Run ledger (latest first)", "table",
          """SELECT started_at, mode, status, fetched, inserted, updated, unchanged,
                    round(dateDiff('millisecond', started_at, finished_at)/1000, 1) AS secs, error
             FROM quakes.ingest_runs ORDER BY started_at DESC LIMIT 50""", 14, 4, 10, 9,
          "Every attempt, success or failure. This is the continuity evidence."),

    panel(9, "Last 7 days of earthquakes (M1.5+)", "geomap",
          """SELECT latitude, longitude, mag, place, time FROM quakes.events FINAL
             WHERE time > now() - INTERVAL 7 DAY AND mag >= 1.5 ORDER BY mag""", 0, 13, 14, 12,
          "Point size scales with magnitude.",
          options={"view": {"id": "coords", "lat": 15, "lon": 180, "zoom": 1.9},
                   "controls": {"showZoom": True, "mouseWheelZoom": False},
                   "basemap": {"type": "osm-standard", "name": "OpenStreetMap"},
                   "layers": [{"type": "markers", "name": "Quakes",
                               "location": {"mode": "coords", "latitude": "latitude", "longitude": "longitude"},
                               "config": {"showLegend": True,
                                          "style": {"size": {"field": "mag", "min": 2, "max": 14, "fixed": 4},
                                                    "color": {"field": "mag"}, "opacity": 0.6}}}],
                   "tooltip": {"mode": "details"}},
          fieldConfig={"defaults": {"color": {"mode": "continuous-YlRd"}}, "overrides": []}),
    panel(10, "Live vs. history: M4.5+ quakes per day vs. 2000-2025 daily average", "timeseries",
          """SELECT toDateTime(toDate(time)) AS day, count() AS m45_today,
                    (SELECT countIf(mag >= 4.5) / dateDiff('day', toDate('2000-01-01'), toDate('2026-01-01'))
                       FROM quakes.events FINAL WHERE time >= '2000-01-01' AND time < '2026-01-01') AS avg_2000_2025
             FROM quakes.events FINAL WHERE mag >= 4.5 AND time >= today() - 30
             GROUP BY day ORDER BY day""", 14, 13, 10, 12,
          "Joins the live trickle to the historical seed in one query.",
          timeFrom="30d", hideTimeOverride=False,
          fieldConfig={"defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 60}},
                       "overrides": [{"matcher": {"id": "byName", "options": "avg_2000_2025"},
                                      "properties": [{"id": "custom.drawStyle", "value": "line"},
                                                     {"id": "custom.lineWidth", "value": 3},
                                                     {"id": "custom.fillOpacity", "value": 0}]}]}),
    panel(11, "Quakes per year since 1970: all vs. M5+ (log scale)", "timeseries",
          """SELECT toDateTime(toStartOfYear(time)) AS year, count() AS all_events,
                    countIf(mag >= 5) AS m5_plus
             FROM quakes.events FINAL WHERE time < toStartOfYear(now()) GROUP BY year ORDER BY year""",
          0, 25, 24, 9,
          "The seed. Why does the 'all' line climb while M5+ stays flat?",
          timeFrom="57y",
          fieldConfig={"defaults": {"custom": {"scaleDistribution": {"type": "log", "log": 10},
                                               "lineWidth": 2}}, "overrides": []}),
]

dash = {"uid": "quakes", "title": "USGS Earthquake Pipeline", "tags": ["cs452"], "timezone": "browser",
        "refresh": "1m", "time": {"from": "now-6h", "to": "now"}, "schemaVersion": 39,
        "editable": True, "panels": panels}
out = Path(__file__).resolve().parent / "dashboards" / "quakes.json"
out.write_text(json.dumps(dash, indent=2))
print(f"wrote {out}")
