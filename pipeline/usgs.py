"""Talking to USGS. Shared by the one-time seed and the recurring ingest, so both
paths fetch exactly the same CSV columns and feed them through the same insert SQL."""
from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timedelta, timezone

import requests

FDSN = "https://earthquake.usgs.gov/fdsnws/event/1/query"
FEED_DAY = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.csv"
FDSN_LIMIT = 20_000  # USGS refuses (HTTP 400) any query matching more than this

HEADERS = {"User-Agent": "cs452-data-pipeline (student project)"}
session = requests.Session()
session.headers.update(HEADERS)


class TooMany(Exception):
    """The window matched more than FDSN_LIMIT events; split it and retry."""


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _get(url: str, params: dict | None = None, retries: int = 4) -> str:
    """GET with retry and exponential backoff for transient failures (5xx, timeouts)."""
    delay = 5
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=120)
            if r.status_code == 400 and "exceeds search limit" in r.text:
                raise TooMany(r.text.strip().splitlines()[2] if r.text.count("\n") > 2 else r.text)
            if r.status_code == 204:  # FDSN: "no events matched"
                return ""
            r.raise_for_status()
            return r.text
        except TooMany:
            raise
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                raise  # our fault; retrying won't help
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def fetch_window_csv(start: datetime, end: datetime, *, updated_after: datetime | None = None) -> str:
    """One FDSN request for events with origin time in [start, end). Raises TooMany."""
    params = {"format": "csv", "starttime": iso(start), "endtime": iso(end), "orderby": "time-asc"}
    if updated_after is not None:
        params["updatedafter"] = iso(updated_after)
    return _get(FDSN, params)


def fetch_range_csv(start: datetime, end: datetime, *, updated_after: datetime | None = None,
                    min_window: timedelta = timedelta(minutes=10)) -> list[str]:
    """Fetch [start, end) by recursive bisection: if a window is too big, split it in half."""
    try:
        return [fetch_window_csv(start, end, updated_after=updated_after)]
    except TooMany:
        if end - start <= min_window:
            raise
        mid = start + (end - start) / 2
        return (fetch_range_csv(start, mid, updated_after=updated_after, min_window=min_window)
                + fetch_range_csv(mid, end, updated_after=updated_after, min_window=min_window))


def fetch_feed_csv() -> str:
    """The live summary feed: every event from the past 24 hours, refreshed every minute."""
    return _get(FEED_DAY)


def parse_csv(text: str) -> list[dict]:
    """Parse USGS CSV into dicts keyed by the CSV header (time, id, updated, ...)."""
    if not text.strip():
        return []
    return list(csv.DictReader(io.StringIO(text)))


def to_csv(rows: list[dict]) -> str:
    """Re-serialize parsed rows with the original USGS header, for the shared insert path."""
    if not rows:
        return ""
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def parse_ts(s: str) -> datetime:
    """USGS timestamps look like 2024-01-01T00:59:19.656Z."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
