"""The always-on loop inside the ingester container: run, sleep to the next tick, repeat.

Docker's `restart: unless-stopped` covers crashes and reboots; this loop covers
everything else by never letting one bad run kill the process.
"""
from __future__ import annotations

import os
import time
import traceback
from datetime import datetime, timezone

from pipeline.ch import ClickHouse
from pipeline.ingest import run_once

INTERVAL = int(os.environ.get("INTERVAL_SECONDS", "300"))


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}", flush=True)


def main() -> None:
    ch = ClickHouse()
    while True:
        try:
            ch.apply_schema()
            for r in run_once(ch):
                log(f"{r['mode']:<9} {r['status']:<5} fetched={r['fetched']} inserted={r['inserted']} "
                    f"updated={r['updated']} unchanged={r['unchanged']} {r['error']}")
        except Exception:  # e.g. ClickHouse itself unreachable, so the run couldn't even be logged
            log("run failed before it could be recorded:\n" + traceback.format_exc())
        # Sleep to the next wall-clock multiple of INTERVAL (:00, :05, :10, ...).
        time.sleep(INTERVAL - (time.time() % INTERVAL))


if __name__ == "__main__":
    main()
