-- Durable end-to-end analysis jobs.  The result is an explainable scientific
-- review queue, not a market forecast or a confirmed weak signal.

ALTER TABLE analysis_job DROP CONSTRAINT analysis_job_job_kind_check;
ALTER TABLE analysis_job ADD CONSTRAINT analysis_job_job_kind_check
    CHECK (job_kind IN ('controlled_discovery', 'controlled_full_analysis'));

ALTER TABLE analysis_job DROP CONSTRAINT analysis_job_result_role_check;
ALTER TABLE analysis_job ADD CONSTRAINT analysis_job_result_role_check
    CHECK (result_role IN (
        'corpus_candidate_not_signals',
        'scientific_review_queue_not_market_forecast'
    ));

ALTER TABLE analysis_job_event DROP CONSTRAINT analysis_job_event_event_type_check;
ALTER TABLE analysis_job_event ADD CONSTRAINT analysis_job_event_event_type_check
    CHECK (event_type IN (
        'queued', 'claimed', 'heartbeat', 'cancel_requested', 'cancelled',
        'succeeded', 'failed', 'lease_expired_requeued', 'lease_expired_failed',
        'retry_created', 'stage_started', 'stage_succeeded', 'stage_reused'
    ));

CREATE UNIQUE INDEX analysis_job_one_active_full_analysis_per_mission
    ON analysis_job(mission_id)
    WHERE job_kind='controlled_full_analysis'
      AND status IN ('queued', 'running', 'cancel_requested');

INSERT INTO schema_migrations (version) VALUES ('036_full_analysis_jobs');
