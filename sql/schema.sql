-- Applied by the ingester on every start (idempotent).

-- One row per earthquake *revision*. USGS revises events after the fact (magnitude
-- corrected, status automatic -> reviewed), so the same id legitimately arrives more
-- than once. ReplacingMergeTree(updated) keeps, per ORDER BY key, the row with the
-- largest `updated` -- but only when parts merge in the background. Until then both
-- rows exist, so queries that must be exact use FINAL (or argMax).
--
-- Why ORDER BY id and no PARTITION BY toYYYYMM(time)? Dedup only happens between rows
-- with the same sorting key *in the same partition*. Origin time is itself revisable,
-- so partitioning on it could put two revisions of one quake in different partitions
-- where they'd never be merged. At ~5M rows a single partition scans in milliseconds.
CREATE TABLE IF NOT EXISTS quakes.events
(
    id          String,
    time        DateTime64(3, 'UTC'),
    updated     DateTime64(3, 'UTC'),
    latitude    Float64,
    longitude   Float64,
    depth_km    Nullable(Float64),
    mag         Nullable(Float32),
    mag_type    LowCardinality(String),
    place       String,
    event_type  LowCardinality(String),
    status      LowCardinality(String),
    net         LowCardinality(String),
    nst         Nullable(UInt16),
    gap         Nullable(Float32),
    dmin        Nullable(Float32),
    rms         Nullable(Float32),
    source      LowCardinality(String),   -- seed | feed | backfill | revisions
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated)
ORDER BY id;

-- The continuity ledger: one row per ingest attempt, success or failure.
-- This is how we prove (or disprove) "no gaps" a week from now.
CREATE TABLE IF NOT EXISTS quakes.ingest_runs
(
    run_id       UUID,
    started_at   DateTime64(3, 'UTC'),
    finished_at  DateTime64(3, 'UTC'),
    mode         LowCardinality(String),  -- feed | backfill | revisions
    window_start DateTime64(3, 'UTC'),
    window_end   DateTime64(3, 'UTC'),
    fetched      UInt32,
    inserted     UInt32,                  -- ids we had never seen
    updated      UInt32,                  -- newer revision of an id we had
    unchanged    UInt32,                  -- already had this exact revision: skipped
    status       LowCardinality(String),  -- ok | error
    error        String
)
ENGINE = MergeTree
ORDER BY started_at;
