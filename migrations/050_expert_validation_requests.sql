-- Scout handoff is separate from an expert's later opinion.
CREATE TABLE expert_validation_request (
    request_id UUID PRIMARY KEY,
    mission_id TEXT NOT NULL,
    score_run_id BIGINT NOT NULL REFERENCES analysis_run(run_id) ON DELETE RESTRICT,
    candidate_ids JSONB NOT NULL CHECK (jsonb_typeof(candidate_ids) = 'array'),
    candidate_snapshot JSONB NOT NULL CHECK (jsonb_typeof(candidate_snapshot) = 'array'),
    requested_by TEXT NOT NULL CHECK (length(btrim(requested_by)) BETWEEN 1 AND 120),
    recipient TEXT NOT NULL CHECK (length(btrim(recipient)) BETWEEN 1 AND 120),
    note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX expert_validation_request_recent_idx
    ON expert_validation_request(created_at DESC, request_id DESC);
CREATE TRIGGER expert_validation_request_no_change
    BEFORE UPDATE OR DELETE ON expert_validation_request
    FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();
INSERT INTO schema_migrations (version) VALUES ('050_expert_validation_requests');
