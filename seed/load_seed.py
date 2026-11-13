"""One-time bulk load of the saved history into ClickHouse.

This is deliberately NOT the hourly code path's row-by-row compare: each raw .csv.gz is
streamed, still compressed, straight into ClickHouse's HTTP interface, which parses it
server-side using the same INSERT ... SELECT FROM input() as the live ingester.
Re-running it is safe: ReplacingMergeTree(updated) collapses the duplicate rows.

    set -a; . ./.env; set +a; python -m seed.load_seed
"""
from __future__ import annotations

import time
from pathlib import Path

from pipeline.ch import ClickHouse

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


def main() -> None:
    ch = ClickHouse()
    ch.apply_schema()
    files = sorted(RAW.glob("*.csv.gz"))
    before = int(ch.execute("SELECT count() FROM quakes.events").strip())
    t0 = time.time()
    for i, f in enumerate(files, 1):
        ch.insert_usgs_csv(f.read_bytes(), source="seed", gzipped=True)
        if i % 60 == 0 or i == len(files):
            print(f"{i}/{len(files)} files ({f.name}) {time.time() - t0:.1f}s", flush=True)
    after = int(ch.execute("SELECT count() FROM quakes.events").strip())
    print(f"loaded {len(files)} files in {time.time() - t0:.1f}s; rows {before:,} -> {after:,}")


if __name__ == "__main__":
    main()
