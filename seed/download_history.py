"""One-time historical download: the USGS catalog, month by month, saved as raw gzipped CSV.

Seeding etiquette (from the brief): pull history ONCE, keep the raw files on disk, and
bulk-load from the saved copy. This script is resumable: a month whose file already
exists is skipped, so a crash or Ctrl-C costs nothing.

    python -m seed.download_history --start 1970-01-01 --end 2026-11-13
"""
from __future__ import annotations

import argparse
import gzip
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from pipeline import usgs

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


def months(start: datetime, end: datetime):
    cur = start
    while cur < end:
        nxt = datetime(cur.year + (cur.month == 12), cur.month % 12 + 1, 1, tzinfo=timezone.utc)
        yield cur, min(nxt, end)
        cur = nxt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="1970-01-01")
    ap.add_argument("--end", required=True, help="exclusive; use today's date (UTC) so the live feed covers the seam")
    ap.add_argument("--pause", type=float, default=0.5, help="seconds between requests, to be polite")
    a = ap.parse_args()
    start = datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc)
    RAW.mkdir(parents=True, exist_ok=True)

    total_rows = requests_made = 0
    t0 = time.time()
    for m_start, m_end in months(start, end):
        out = RAW / f"{m_start:%Y-%m}.csv.gz"
        if out.exists():
            continue
        chunks = usgs.fetch_range_csv(m_start, m_end)
        requests_made += len(chunks)
        # Stitch the chunks into one file with a single header line.
        header, body = None, []
        for c in chunks:
            if not c.strip():
                continue
            first, _, rest = c.partition("\n")
            header = header or first
            body.append(rest)
        tmp = out.with_suffix(".tmp")
        with gzip.open(tmp, "wt", newline="") as f:
            if header:
                f.write(header + "\n" + "".join(body))
        os.replace(tmp, out)  # atomic: a half-written month never looks finished
        n = sum(b.count("\n") for b in body)
        total_rows += n
        print(f"{m_start:%Y-%m}: {n:>6} events in {len(chunks)} request(s)  "
              f"[total {total_rows:,}, {time.time() - t0:.0f}s]", flush=True)
        time.sleep(a.pause)
    print(f"done: {total_rows:,} new events, {requests_made} requests, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
