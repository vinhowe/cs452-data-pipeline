-- Evidence queries for the demo and the one-week follow-up.
-- Run with:  ./q.sh "<query>"   or paste into Grafana Explore.

-- 1. Liveness: new quakes added by unattended runs, per hour, last 24h.
SELECT toStartOfHour(started_at) AS hour, count() AS runs, sum(inserted) AS new_quakes, sum(updated) AS revised
FROM quakes.ingest_runs
WHERE status = 'ok' AND mode IN ('feed', 'backfill') AND started_at > now() - INTERVAL 1 DAY
GROUP BY hour ORDER BY hour;

-- 2. Continuity: any stretch longer than 10 minutes between successful live runs is a gap.
--    (The scheduler fires every 5 min.) Empty result = no gaps.
SELECT prev_end, started_at, dateDiff('minute', prev_end, started_at) AS gap_minutes
FROM (
    SELECT started_at,
           lagInFrame(finished_at) OVER (ORDER BY started_at ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS prev_end
    FROM quakes.ingest_runs
    WHERE status = 'ok' AND mode IN ('feed', 'backfill')
)
WHERE prev_end > '2000-01-01' AND dateDiff('minute', prev_end, started_at) > 10
ORDER BY started_at;

-- 3. Idempotency: rows physically stored vs. distinct quakes. FINAL applies the dedup at
--    query time, and the gap between the first two numbers is duplicates not yet merged away.
SELECT
    (SELECT count() FROM quakes.events)               AS physical_rows,
    (SELECT count() FROM quakes.events FINAL)         AS after_final,
    (SELECT uniqExact(id) FROM quakes.events)         AS distinct_ids;

-- 4. The seed/trickle seam: events per hour around the seed's end, by who wrote them.
SELECT toStartOfHour(time) AS hour, source, count() AS n
FROM quakes.events FINAL
WHERE time BETWEEN '2026-11-12 20:00:00' AND '2026-11-13 04:00:00'
GROUP BY hour, source ORDER BY hour, source;

-- 5. Revisions in action: quakes whose stored row came from a later revision than the seed.
SELECT id, time, mag, status, updated, source
FROM quakes.events FINAL
WHERE source IN ('feed', 'revisions') AND time < '2026-11-13'
ORDER BY updated DESC LIMIT 10;

-- 6. Live joined to history: today's M4.5+ count vs. the 2000-2025 daily average.
SELECT
    (SELECT count() FROM quakes.events FINAL WHERE mag >= 4.5 AND time >= now() - INTERVAL 1 DAY) AS m45_last_24h,
    (SELECT round(countIf(mag >= 4.5) / dateDiff('day', toDate('2000-01-01'), toDate('2026-01-01')), 1)
       FROM quakes.events FINAL WHERE time >= '2000-01-01' AND time < '2026-01-01')               AS avg_per_day_2000_2025;

-- 7. Time of death (for a post-mortem): the last successful run and the newest stored quake.
SELECT
    (SELECT max(finished_at) FROM quakes.ingest_runs WHERE status = 'ok') AS last_good_run,
    (SELECT max(time) FROM quakes.events)                                  AS newest_quake,
    (SELECT max(started_at) FROM quakes.ingest_runs WHERE status = 'error') AS last_error;
