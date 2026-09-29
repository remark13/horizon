-- Read-only audit of completed SAIA jobs. This is not a live SLO benchmark.
-- Link discovery to full analysis by the saved source-profile provenance,
-- never merely by mission name or nearest time.
BEGIN READ ONLY;

SELECT job_kind, status, count(*) AS jobs
FROM analysis_job
WHERE created_at >= '2026-09-25'::timestamptz
  AND created_at < '2026-09-28'::timestamptz
GROUP BY job_kind, status
ORDER BY job_kind, status;

WITH linked AS (
    SELECT f.job_id AS analysis_job_id,
           d.job_id AS discovery_job_id,
           f.requested_by,
           d.created_at AS discovery_created_at,
           extract(epoch FROM f.created_at - d.finished_at) AS handoff_gap_s,
           extract(epoch FROM d.finished_at - d.created_at) AS discovery_s,
           extract(epoch FROM f.finished_at - f.created_at) AS analysis_s,
           extract(epoch FROM f.finished_at - d.created_at) AS linked_total_s
    FROM analysis_job f
    JOIN query_version q ON q.query_version_id = f.query_version_id
    JOIN analysis_job d ON d.job_id::text =
        q.payload->'collection_profile'->'provenance'->>'balanced_discovery_job_id'
    WHERE f.job_kind = 'controlled_full_analysis'
      AND f.status = 'succeeded' AND d.status = 'succeeded'
      AND f.finished_at IS NOT NULL AND d.finished_at IS NOT NULL
      AND d.created_at <= f.finished_at
), recent_continuous AS (
    SELECT * FROM linked
    WHERE handoff_gap_s BETWEEN 0 AND 60
      AND discovery_created_at >= '2026-09-25'::timestamptz
      AND discovery_created_at < '2026-09-28'::timestamptz
)
SELECT count(*) AS linked_successes,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY linked_total_s))::numeric, 1)
           AS p50_linked_seconds,
       round((percentile_cont(0.95) WITHIN GROUP (ORDER BY linked_total_s))::numeric, 1)
           AS p95_linked_seconds,
       count(*) FILTER (WHERE linked_total_s > 1200) AS over_20_min,
       round(max(linked_total_s)::numeric, 1) AS max_linked_seconds
FROM recent_continuous;

WITH linked AS (
    SELECT f.requested_by, d.created_at AS discovery_created_at,
           extract(epoch FROM f.created_at - d.finished_at) AS handoff_gap_s,
           extract(epoch FROM d.finished_at - d.created_at) AS discovery_s,
           extract(epoch FROM f.finished_at - f.created_at) AS analysis_s,
           extract(epoch FROM f.finished_at - d.created_at) AS linked_total_s
    FROM analysis_job f
    JOIN query_version q ON q.query_version_id = f.query_version_id
    JOIN analysis_job d ON d.job_id::text =
        q.payload->'collection_profile'->'provenance'->>'balanced_discovery_job_id'
    WHERE f.job_kind = 'controlled_full_analysis'
      AND f.status = 'succeeded' AND d.status = 'succeeded'
      AND f.finished_at IS NOT NULL AND d.finished_at IS NOT NULL
)
SELECT requested_by, count(*) AS linked_successes,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY linked_total_s))::numeric, 1)
           AS p50_linked_seconds,
       round(max(linked_total_s)::numeric, 1) AS max_linked_seconds,
       round(avg(discovery_s)::numeric, 1) AS mean_discovery_seconds,
       round(avg(analysis_s)::numeric, 1) AS mean_analysis_seconds
FROM linked
WHERE handoff_gap_s BETWEEN 0 AND 60
  AND discovery_created_at >= '2026-09-25'::timestamptz
  AND discovery_created_at < '2026-09-28'::timestamptz
GROUP BY requested_by
ORDER BY requested_by;

COMMIT;
