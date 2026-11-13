"""Fill docs/passoff_template.html with live numbers, screenshot the dashboard, render the PDF.

    set -a; . ./.env; set +a; .venv/bin/python -m docs.build_passoff
"""
import html
import re
import subprocess
from datetime import datetime
from pathlib import Path

from pipeline.ch import ClickHouse

DOCS = Path(__file__).resolve().parent
ch = ClickHouse()


def one(sql):
    return ch.rows(sql)[0]


download_log = (DOCS.parent / "data" / "download.log").read_text()
dl = re.search(r"done: ([\d,]+) new events, (\d+) requests, (\d+)s", download_log)
dl_events, dl_requests, dl_secs = dl.group(1), int(dl.group(2)) + 1, int(dl.group(3))  # +1: the test month

counts = one("""SELECT (SELECT count() FROM quakes.events) AS phys, (SELECT count() FROM quakes.events FINAL) AS fin,
                       (SELECT uniqExact(id) FROM quakes.events) AS ids,
                       (SELECT countIf(source='seed') FROM quakes.events) AS seed_rows,
                       (SELECT toString(min(time)) FROM quakes.events) AS first_t""")
size = one("""SELECT formatReadableSize(sum(data_compressed_bytes)) AS c, formatReadableSize(sum(data_uncompressed_bytes)) AS u
              FROM system.parts WHERE active AND database='quakes' AND table='events'""")
runs = one("""SELECT countIf(status='ok' AND mode IN ('feed','backfill')) AS live_ok,
                     countIf(status='error') AS errs, countIf(mode='revisions' AND status='ok') AS sweeps,
                     sumIf(inserted, status='ok' AND mode IN ('feed','backfill')) AS live_new,
                     sumIf(updated, status='ok') AS revised,
                     toString(minIf(started_at, status='ok')) AS first_run, toString(max(started_at)) AS last_run
              FROM quakes.ingest_runs""")
steady = one("""SELECT round(avg(fetched)) AS f, round(avg(inserted), 1) AS i, round(avg(unchanged)) AS u
                FROM quakes.ingest_runs WHERE status='ok' AND mode='feed' AND started_at > (
                  SELECT min(started_at) FROM quakes.ingest_runs WHERE status='ok' AND mode='feed')""")
gaps = ch.rows("""SELECT count() AS n FROM (SELECT started_at, lagInFrame(finished_at) OVER (ORDER BY started_at
                  ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS prev FROM quakes.ingest_runs
                  WHERE status='ok' AND mode IN ('feed','backfill'))
                  WHERE prev > '2000-01-01' AND dateDiff('minute', prev, started_at) > 10""")[0]["n"]
seam = ch.rows("""SELECT formatDateTime(toStartOfHour(time), '%H:00') AS h, countIf(source='seed') AS s,
                         countIf(source!='seed') AS l FROM quakes.events FINAL
                  WHERE time >= '2026-11-12 22:00:00' AND time < '2026-11-13 02:00:00' GROUP BY h ORDER BY min(time)""")
hist = one("""SELECT round(countIf(mag >= 4.5) / dateDiff('day', toDate('2000-01-01'), toDate('2026-01-01')), 1) AS avg45
              FROM quakes.events FINAL WHERE time >= '2000-01-01' AND time < '2026-01-01'""")
last24 = one("SELECT count() AS n FROM quakes.events FINAL WHERE mag >= 4.5 AND time >= now() - INTERVAL 1 DAY")

seam_txt = ", ".join(f"{r['h']} UTC: {r['s']} seed / {r['l']} live" for r in seam)
rows = [
    ("Historical seed", f"{int(counts['seed_rows']):,} events, {counts['first_t'][:10]} → 2026-11-12 23:59 UTC"),
    ("Download (once)", f"{dl_requests} FDSN requests, 683 monthly .csv.gz files, 236 MB on disk, "
                        f"{dl_secs // 60} min {dl_secs % 60} s, 0 errors"),
    ("Bulk load", "683 files streamed gzipped into ClickHouse in 13.6 s"),
    ("Storage", f"{size['u']} uncompressed → {size['c']} compressed (columnar)"),
    ("Integrity", f"{int(counts['phys']):,} physical rows; {int(counts['fin']):,} after FINAL; "
                  f"{int(counts['ids']):,} distinct ids. The difference is duplicates not yet merged "
                  f"away, and FINAL hides them at query time."),
    ("Idempotency", f"A typical feed run fetches ~{int(steady['f'])} events and inserts ~{steady['i']}; "
                    f"~{int(steady['u'])} are skipped as already stored. Unit tests cover re-running a batch (0 inserted)."),
    ("Seed/trickle seam", seam_txt),
    ("Unattended runs", f"{runs['live_ok']} successful live runs from {runs['first_run'][:16]} to "
                        f"{runs['last_run'][:16]} UTC, {runs['sweeps']} revision sweep(s), "
                        f"{runs['live_new']} new quakes, {runs['revised']} revisions applied, "
                        f"{gaps} gaps over 10 min between successful runs"),
    ("Failures", f"{runs['errs']} failed runs, both in the first minute (a bug in my insert: a trailing "
                 f"newline in the SQL). They were recorded in the ledger, and the next run re-covered the window. "
                 f"(Those two rows show non-zero 'inserted' counts from before a later fix. Nothing was written.)"),
    ("Live vs. history", f"{last24['n']} M4.5+ quakes in the last 24 h vs. a 2000–2025 average of {hist['avg45']}/day"),
    ("Dashboard latency", "All 13 panels 11–320 ms, including FINAL over ~4.9M rows"),
    ("Disk safety", "ClickHouse system logs given a 3-day TTL; container logs capped at 50 MB each"),
    ("Cost", "$0.00"),
]
results = "\n".join(f"<tr><th style='width:20%'>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in rows)

now = datetime.now()
subprocess.run(["google-chrome-stable", "--headless=new", "--disable-gpu", "--hide-scrollbars",
                "--window-size=1600,1500", "--virtual-time-budget=25000", f"--screenshot={DOCS / 'dashboard.png'}",
                "http://127.0.0.1:3000/d/quakes?orgId=1&kiosk"], check=True, capture_output=True, timeout=120)

page = (DOCS / "passoff_template.html").read_text()
page = (page.replace("__DATE__", now.strftime("%B %-d, %Y"))
            .replace("__ROWS__", f"{int(counts['fin']):,}")
            .replace("__REQUESTS__", str(dl_requests))
            .replace("__RESULTS__", results)
            .replace("__SHOT_CAPTION__", f"Grafana, captured {now:%b %-d %-I:%M %p} Mountain time "
                                         f"(times on the dashboard are local; the database stores UTC)."))
assert "__" not in re.sub(r"<[^>]+>", "", page).replace("__init__", ""), "unfilled placeholder"
(DOCS / "passoff.html").write_text(page)
subprocess.run(["google-chrome-stable", "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                f"--print-to-pdf={DOCS / 'passoff.pdf'}", (DOCS / "passoff.html").as_uri()],
               check=True, capture_output=True, timeout=120)
print("wrote", DOCS / "passoff.html", "and", DOCS / "passoff.pdf")
