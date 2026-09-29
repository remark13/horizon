-- Durable execution of an explicitly approved multi-branch query plan.

ALTER TABLE analysis_job ADD COLUMN approved_query_plan_id UUID
    REFERENCES approved_query_plan(plan_id) ON DELETE RESTRICT;
ALTER TABLE analysis_job ALTER COLUMN mission_id DROP NOT NULL;
ALTER TABLE analysis_job ALTER COLUMN query_version_id DROP NOT NULL;

ALTER TABLE analysis_job DROP CONSTRAINT analysis_job_job_kind_check;
ALTER TABLE analysis_job ADD CONSTRAINT analysis_job_job_kind_check
    CHECK (job_kind IN (
        'controlled_discovery', 'controlled_full_analysis',
        'approved_balanced_discovery'
    ));

ALTER TABLE analysis_job DROP CONSTRAINT analysis_job_result_role_check;
ALTER TABLE analysis_job ADD CONSTRAINT analysis_job_result_role_check
    CHECK (result_role IN (
        'corpus_candidate_not_signals',
        'balanced_corpus_candidate_not_signals',
        'scientific_review_queue_not_market_forecast'
    ));

ALTER TABLE analysis_job ADD CONSTRAINT analysis_job_input_identity_check CHECK (
    (job_kind='approved_balanced_discovery'
     AND approved_query_plan_id IS NOT NULL
     AND mission_id IS NULL AND query_version_id IS NULL)
    OR
    (job_kind IN ('controlled_discovery','controlled_full_analysis')
     AND approved_query_plan_id IS NULL
     AND mission_id IS NOT NULL AND query_version_id IS NOT NULL)
);

CREATE UNIQUE INDEX analysis_job_one_active_balanced_discovery_per_plan
    ON analysis_job(approved_query_plan_id)
    WHERE job_kind='approved_balanced_discovery'
      AND status IN ('queued','running','cancel_requested');
CREATE INDEX analysis_job_query_plan_history_idx
    ON analysis_job(approved_query_plan_id,created_at DESC)
    WHERE approved_query_plan_id IS NOT NULL;

CREATE OR REPLACE FUNCTION analysis_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'analysis jobs are append-preserving and cannot be deleted';
    END IF;
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') THEN
        RAISE EXCEPTION 'terminal analysis job is immutable; create a retry job';
    END IF;
    IF (NEW.job_id, NEW.operation_id, NEW.mission_id, NEW.query_version_id,
        NEW.approved_query_plan_id, NEW.job_kind, NEW.requested_by, NEW.payload,
        NEW.input_sha256, NEW.result_role, NEW.max_attempts, NEW.created_at)
       IS DISTINCT FROM
       (OLD.job_id, OLD.operation_id, OLD.mission_id, OLD.query_version_id,
        OLD.approved_query_plan_id, OLD.job_kind, OLD.requested_by, OLD.payload,
        OLD.input_sha256, OLD.result_role, OLD.max_attempts, OLD.created_at) THEN
        RAISE EXCEPTION 'analysis job input is immutable';
    END IF;
    IF NEW.retry_of_job_id IS DISTINCT FROM OLD.retry_of_job_id THEN
        RAISE EXCEPTION 'analysis job retry lineage is immutable';
    END IF;
    IF NEW.status <> OLD.status AND NOT (
        (OLD.status = 'queued' AND NEW.status IN ('running', 'cancelled')) OR
        (OLD.status = 'running' AND NEW.status IN ('queued', 'cancel_requested', 'succeeded', 'failed')) OR
        (OLD.status = 'cancel_requested' AND NEW.status IN ('cancelled', 'failed'))
    ) THEN
        RAISE EXCEPTION 'invalid analysis job transition: % -> %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$;

INSERT INTO schema_migrations (version) VALUES ('046_balanced_query_plan_jobs');
