"""One ingest run. The scheduler calls run_once() every few minutes.

Decisions each run makes:
  * Where did we leave off?  The window_end of the last *successful* feed/backfill run
    (or the newest event in the table, right after the seed). A failed run never moves
    this watermark, so the next run automatically re-covers whatever was missed.
  * Feed or backfill?  If we left off less than ~23h ago, the 24-hour live feed covers
    the gap. If longer (machine was off, USGS was down all day), page through the FDSN
    history API from the watermark instead.
  * Revisions sweep:  every few hours, ask FDSN for events *updated* since the last sweep,
    whatever their origin time, to catch magnitude/status corrections to older quakes.
  * Dedup:  compare each fetched (id, updated) with what's stored; insert only new ids
    and newer revisions. ReplacingMergeTree is the safety net if anything slips through.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from pipeline import usgs
from pipeline.ch import ClickHouse, sql_str

FEED_SPAN = timedelta(hours=24)
FEED_SAFE = timedelta(hours=23)       # leave an hour of overlap at the feed's edge
BACKFILL_OVERLAP = timedelta(hours=1)
REVISION_SWEEP = timedelta(hours=float(os.environ.get("REVISION_SWEEP_HOURS", "6")))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ts(v: str | None) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(v.replace(" ", "T")).replace(tzinfo=timezone.utc)


def classify(rows: list[dict], stored: dict[str, datetime]) -> tuple[list[dict], int, int, int]:
    """Split fetched rows into (rows_to_insert, inserted, updated, unchanged)."""
    keep, ins, upd, same = [], 0, 0, 0
    newest: dict[str, dict] = {}
    for r in rows:  # a single fetch can contain the same id twice; keep its newest revision
        prev = newest.get(r["id"])
        if prev is None or usgs.parse_ts(r["updated"]) > usgs.parse_ts(prev["updated"]):
            newest[r["id"]] = r
    for r in newest.values():
        have = stored.get(r["id"])
        if have is None:
            keep.append(r); ins += 1
        elif usgs.parse_ts(r["updated"]) > have:
            keep.append(r); upd += 1
        else:
            same += 1
    return keep, ins, upd, same


def stored_revisions(ch: ClickHouse, ids: list[str]) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    for i in range(0, len(ids), 5000):
        chunk = ids[i:i + 5000]
        q = (f"SELECT id, toString(max(updated)) AS u FROM quakes.events "
             f"WHERE id IN ({','.join(sql_str(x) for x in chunk)}) GROUP BY id")
        for row in ch.rows(q):
            out[row["id"]] = ts(row["u"])
    return out


def last_ok(ch: ClickHouse, modes: tuple[str, ...]) -> datetime | None:
    m = ",".join(sql_str(x) for x in modes)
    r = ch.rows(f"SELECT toString(max(window_end)) AS w, count() AS n FROM quakes.ingest_runs "
                f"WHERE status = 'ok' AND mode IN ({m})")
    return ts(r[0]["w"]) if r and int(r[0]["n"]) else None


def _run(ch: ClickHouse, mode: str, window: tuple[datetime, datetime], fetch) -> dict:
    run = {"run_id": str(uuid.uuid4()), "started_at": now_utc(), "mode": mode,
           "window_start": window[0], "window_end": window[1],
           "fetched": 0, "inserted": 0, "updated": 0, "unchanged": 0, "status": "ok", "error": ""}
    try:
        rows = [r for text in fetch() for r in usgs.parse_csv(text)]
        run["fetched"] = len(rows)
        keep, run["inserted"], run["updated"], run["unchanged"] = classify(
            rows, stored_revisions(ch, sorted({r["id"] for r in rows})))
        if keep:
            ch.insert_usgs_csv(usgs.to_csv(keep).encode(), source=mode)
    except Exception as e:  # recorded, not swallowed: the ledger shows the failure
        run["status"], run["error"] = "error", f"{type(e).__name__}: {e}"[:1000]
    run["finished_at"] = now_utc()
    for k in ("started_at", "finished_at", "window_start", "window_end"):
        run[k] = run[k].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    ch.insert_json("quakes.ingest_runs", [run])
    return run


def run_once(ch: ClickHouse) -> list[dict]:
    results = []
    now = now_utc()

    # 1. New events: feed or backfill.
    mark = last_ok(ch, ("feed", "backfill"))
    if mark is None:  # first run after the seed: start from the newest seeded event
        r = ch.rows("SELECT toString(max(time)) AS t, count() AS n FROM quakes.events")
        mark = ts(r[0]["t"]) if int(r[0]["n"]) else now  # empty table: just take the feed
    if now - mark < FEED_SAFE:
        results.append(_run(ch, "feed", (now - FEED_SPAN, now), lambda: [usgs.fetch_feed_csv()]))
    else:
        start = mark - BACKFILL_OVERLAP
        results.append(_run(ch, "backfill", (start, now), lambda: usgs.fetch_range_csv(start, now)))

    # 2. Revisions to older events, every REVISION_SWEEP.
    last_sweep = last_ok(ch, ("revisions",))
    if last_sweep is None or now - last_sweep >= REVISION_SWEEP:
        since = (last_sweep or now - timedelta(days=1)) - timedelta(minutes=10)
        results.append(_run(ch, "revisions", (since, now),
                            lambda: usgs.fetch_range_csv(EPOCH, now, updated_after=since,
                                                         min_window=timedelta(days=1))))
    return results
