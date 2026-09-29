-- Durable execution envelope for controlled publication discovery.
-- A successful row is a frozen candidate corpus, never a signal verdict.

CREATE TABLE analysis_job (
    job_id UUID PRIMARY KEY,
    operation_id UUID NOT NULL UNIQUE,
    mission_id TEXT NOT NULL REFERENCES mission ON DELETE RESTRICT,
    query_version_id TEXT NOT NULL REFERENCES query_version ON DELETE RESTRICT,
    retry_of_job_id UUID REFERENCES analysis_job(job_id) ON DELETE RESTRICT,
    job_kind TEXT NOT NULL CHECK (job_kind IN ('controlled_discovery')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'cancel_requested',
                          'succeeded', 'failed', 'cancelled')),
    requested_by TEXT NOT NULL CHECK (length(btrim(requested_by)) BETWEEN 1 AND 120),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
    result_role TEXT NOT NULL DEFAULT 'corpus_candidate_not_signals'
        CHECK (result_role = 'corpus_candidate_not_signals'),
    result JSONB,
    result_sha256 TEXT CHECK (result_sha256 IS NULL OR length(result_sha256) = 64),
    error TEXT,
    attempt_count INT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INT NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    cancel_requested_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    CHECK ((result IS NULL) = (result_sha256 IS NULL)),
    CHECK (status <> 'succeeded' OR (result IS NOT NULL AND finished_at IS NOT NULL)),
    CHECK (status NOT IN ('failed', 'cancelled') OR finished_at IS NOT NULL),
    CHECK (status NOT IN ('running', 'cancel_requested') OR
           (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
);

CREATE INDEX analysis_job_queue_idx ON analysis_job(status, created_at, job_id);
CREATE INDEX analysis_job_mission_idx ON analysis_job(mission_id, created_at DESC);
CREATE INDEX analysis_job_lease_idx ON analysis_job(lease_expires_at)
    WHERE status IN ('running', 'cancel_requested');

CREATE TABLE analysis_job_event (
    event_id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES analysis_job(job_id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'queued', 'claimed', 'heartbeat', 'cancel_requested', 'cancelled',
        'succeeded', 'failed', 'lease_expired_requeued', 'lease_expired_failed',
        'retry_created'
    )),
    actor TEXT NOT NULL CHECK (length(btrim(actor)) BETWEEN 1 AND 120),
    details JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX analysis_job_event_history_idx
    ON analysis_job_event(job_id, event_id);

CREATE FUNCTION analysis_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'analysis jobs are append-preserving and cannot be deleted';
    END IF;
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') THEN
        RAISE EXCEPTION 'terminal analysis job is immutable; create a retry job';
    END IF;
    IF (NEW.job_id, NEW.operation_id, NEW.mission_id, NEW.query_version_id,
        NEW.job_kind, NEW.requested_by, NEW.payload, NEW.input_sha256,
        NEW.result_role, NEW.max_attempts, NEW.created_at)
       IS DISTINCT FROM
       (OLD.job_id, OLD.operation_id, OLD.mission_id, OLD.query_version_id,
        OLD.job_kind, OLD.requested_by, OLD.payload, OLD.input_sha256,
        OLD.result_role, OLD.max_attempts, OLD.created_at) THEN
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

CREATE TRIGGER analysis_job_state_guard
    BEFORE UPDATE OR DELETE ON analysis_job
    FOR EACH ROW EXECUTE FUNCTION analysis_job_guard();

CREATE FUNCTION analysis_job_event_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'analysis job event history is append-only';
END;
$$;
CREATE TRIGGER analysis_job_event_no_change
    BEFORE UPDATE OR DELETE ON analysis_job_event
    FOR EACH ROW EXECUTE FUNCTION analysis_job_event_append_only();

INSERT INTO schema_migrations (version) VALUES ('034_analysis_jobs');
