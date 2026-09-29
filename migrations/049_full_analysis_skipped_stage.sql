-- A valid optional analysis stage may be skipped with an explicit reason.
-- It must be recorded as such, not converted to success or job failure.
ALTER TABLE analysis_job_event DROP CONSTRAINT analysis_job_event_event_type_check;
ALTER TABLE analysis_job_event ADD CONSTRAINT analysis_job_event_event_type_check
    CHECK (event_type IN (
        'queued', 'claimed', 'heartbeat', 'cancel_requested', 'cancelled',
        'succeeded', 'failed', 'lease_expired_requeued', 'lease_expired_failed',
        'retry_created', 'stage_started', 'stage_succeeded', 'stage_reused',
        'stage_skipped'
    ));

INSERT INTO schema_migrations (version) VALUES ('049_full_analysis_skipped_stage');
