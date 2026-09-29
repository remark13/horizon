-- A human-selected external record is attached to one frozen candidate/run.
-- The link does not change the scientific score or claim independent validation.
CREATE TABLE candidate_external_evidence_link (
    link_id UUID PRIMARY KEY,
    mission_id TEXT NOT NULL,
    score_run_id BIGINT NOT NULL REFERENCES analysis_run(run_id) ON DELETE RESTRICT,
    candidate_id BIGINT NOT NULL REFERENCES signal_candidate(candidate_id) ON DELETE RESTRICT,
    composition_sha256 TEXT NOT NULL CHECK (length(composition_sha256) = 64),
    observation_id UUID NOT NULL REFERENCES external_evidence_observation(observation_id) ON DELETE RESTRICT,
    observation_sha256 TEXT NOT NULL CHECK (length(observation_sha256) = 64),
    source TEXT NOT NULL,
    role TEXT NOT NULL,
    record_url TEXT NOT NULL CHECK (length(record_url) BETWEEN 10 AND 2048),
    record_snapshot JSONB NOT NULL CHECK (jsonb_typeof(record_snapshot) = 'object'),
    assessment TEXT NOT NULL CHECK (assessment IN ('relevant_to_topic', 'background_only')),
    linked_by TEXT NOT NULL CHECK (length(btrim(linked_by)) BETWEEN 1 AND 120),
    rationale TEXT NOT NULL CHECK (length(btrim(rationale)) BETWEEN 20 AND 2000),
    scientific_score_modified BOOLEAN NOT NULL DEFAULT FALSE CHECK (scientific_score_modified = FALSE),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, record_url)
);

CREATE INDEX candidate_external_evidence_link_history_idx
    ON candidate_external_evidence_link(mission_id, score_run_id, candidate_id, created_at DESC);

CREATE FUNCTION candidate_external_evidence_link_validate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    candidate_run BIGINT;
    candidate_mission TEXT;
    evidence_source TEXT;
    evidence_role TEXT;
    evidence_checksum TEXT;
    evidence_payload JSONB;
BEGIN
    SELECT c.run_id, r.mission_id INTO candidate_run, candidate_mission
      FROM signal_candidate c JOIN analysis_run r ON r.run_id = c.run_id
      WHERE c.candidate_id = NEW.candidate_id;
    IF candidate_run IS DISTINCT FROM NEW.score_run_id
       OR candidate_mission IS DISTINCT FROM NEW.mission_id THEN
        RAISE EXCEPTION 'candidate does not belong to the frozen score run and mission';
    END IF;
    SELECT source, role, report_payload_sha256, payload
      INTO evidence_source, evidence_role, evidence_checksum, evidence_payload
      FROM external_evidence_observation WHERE observation_id = NEW.observation_id;
    IF evidence_source IS DISTINCT FROM NEW.source
       OR evidence_role IS DISTINCT FROM NEW.role
       OR evidence_checksum IS DISTINCT FROM NEW.observation_sha256
       OR evidence_payload->>'status' IS DISTINCT FROM 'complete'
       OR evidence_payload->'observations' IS NULL
       OR NOT (evidence_payload->'observations' @> jsonb_build_array(NEW.record_snapshot))
       OR NEW.record_snapshot->>'url' IS DISTINCT FROM NEW.record_url THEN
        RAISE EXCEPTION 'external record is not present in the saved complete observation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER candidate_external_evidence_link_input_guard
    BEFORE INSERT ON candidate_external_evidence_link
    FOR EACH ROW EXECUTE FUNCTION candidate_external_evidence_link_validate();
CREATE TRIGGER candidate_external_evidence_link_no_change
    BEFORE UPDATE OR DELETE ON candidate_external_evidence_link
    FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();

INSERT INTO schema_migrations (version) VALUES ('054_candidate_external_evidence_links');
