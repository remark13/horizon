-- Scout-written PESTLE/industry hypotheses are not scientific/expert outcomes.
CREATE TABLE candidate_analysis_note (
    note_id UUID PRIMARY KEY,
    mission_id TEXT NOT NULL,
    score_run_id BIGINT NOT NULL REFERENCES analysis_run(run_id) ON DELETE RESTRICT,
    candidate_id BIGINT NOT NULL REFERENCES signal_candidate(candidate_id) ON DELETE RESTRICT,
    composition_sha256 TEXT NOT NULL CHECK (length(composition_sha256)=64),
    revision INTEGER NOT NULL CHECK (revision>0),
    version TEXT NOT NULL CHECK (version='candidate-impact-note-v1'),
    report_sha256 TEXT NOT NULL CHECK (length(report_sha256)=64),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(candidate_id,revision)
);
CREATE FUNCTION candidate_analysis_note_validate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE candidate_run BIGINT; candidate_mission TEXT; candidate_status TEXT;
BEGIN
    SELECT c.run_id,r.mission_id,r.status INTO candidate_run,candidate_mission,candidate_status
      FROM signal_candidate c JOIN analysis_run r ON r.run_id=c.run_id WHERE candidate_id=NEW.candidate_id;
    IF candidate_run IS DISTINCT FROM NEW.score_run_id OR candidate_mission IS DISTINCT FROM NEW.mission_id
       OR candidate_status IS DISTINCT FROM 'done'
       OR NEW.payload->'binding'->>'mission_id' IS DISTINCT FROM NEW.mission_id
       OR (NEW.payload->'binding'->>'score_run_id')::BIGINT IS DISTINCT FROM NEW.score_run_id
       OR (NEW.payload->'binding'->>'candidate_id')::BIGINT IS DISTINCT FROM NEW.candidate_id
       OR NEW.payload->'binding'->>'composition_sha256' IS DISTINCT FROM NEW.composition_sha256
       OR ((NEW.payload->'binding'->>'evidence_sha256') ~ '^[a-f0-9]{64}$') IS NOT TRUE
       OR NEW.payload->'binding'->>'version' IS DISTINCT FROM NEW.version
       OR (NEW.payload->>'revision')::INTEGER IS DISTINCT FROM NEW.revision
       OR NEW.payload->>'note_id' IS DISTINCT FROM NEW.note_id::TEXT
       OR NEW.payload->>'report_sha256' IS DISTINCT FROM NEW.report_sha256
       OR NEW.payload->>'status' IS DISTINCT FROM 'scout_hypotheses_not_expert_validated'
       OR NEW.payload->>'scientific_results_modified' IS DISTINCT FROM 'false'
       OR NEW.payload->>'claim_faithfulness_verified' IS DISTINCT FROM 'false'
       OR NEW.payload->>'author_identity_verified' IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'analysis note does not match candidate or hypothesis-only payload';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER candidate_analysis_note_guard BEFORE INSERT ON candidate_analysis_note
  FOR EACH ROW EXECUTE FUNCTION candidate_analysis_note_validate();
CREATE TRIGGER candidate_analysis_note_no_change BEFORE UPDATE OR DELETE ON candidate_analysis_note
  FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();
INSERT INTO schema_migrations (version) VALUES ('061_candidate_analysis_note');
