-- Optional Russian draft descriptions do not modify frozen scientific results.
CREATE TABLE candidate_presentation (
    presentation_id UUID PRIMARY KEY,
    mission_id TEXT NOT NULL,
    score_run_id BIGINT NOT NULL REFERENCES analysis_run(run_id) ON DELETE RESTRICT,
    candidate_id BIGINT NOT NULL REFERENCES signal_candidate(candidate_id) ON DELETE RESTRICT,
    composition_sha256 TEXT NOT NULL CHECK (length(composition_sha256)=64),
    input_sha256 TEXT NOT NULL CHECK (length(input_sha256)=64),
    version TEXT NOT NULL,
    model TEXT NOT NULL,
    report_sha256 TEXT NOT NULL CHECK (length(report_sha256)=64),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(candidate_id,input_sha256,version,model)
);
CREATE FUNCTION candidate_presentation_validate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE candidate_run BIGINT; candidate_mission TEXT;
BEGIN
    SELECT c.run_id,r.mission_id INTO candidate_run,candidate_mission
      FROM signal_candidate c JOIN analysis_run r ON r.run_id=c.run_id WHERE candidate_id=NEW.candidate_id;
    IF candidate_run IS DISTINCT FROM NEW.score_run_id OR candidate_mission IS DISTINCT FROM NEW.mission_id
       OR NEW.payload->'binding'->>'mission_id' IS DISTINCT FROM NEW.mission_id
       OR (NEW.payload->'binding'->>'score_run_id')::BIGINT IS DISTINCT FROM NEW.score_run_id
       OR (NEW.payload->'binding'->>'candidate_id')::BIGINT IS DISTINCT FROM NEW.candidate_id
       OR NEW.payload->'binding'->>'composition_sha256' IS DISTINCT FROM NEW.composition_sha256
       OR NEW.payload->'binding'->>'input_sha256' IS DISTINCT FROM NEW.input_sha256
       OR NEW.payload->'binding'->>'version' IS DISTINCT FROM NEW.version
       OR NEW.payload->>'model' IS DISTINCT FROM NEW.model
       OR NEW.payload->>'report_sha256' IS DISTINCT FROM NEW.report_sha256
       OR NEW.payload->>'status' IS DISTINCT FROM 'machine_generated_draft'
       OR NEW.payload->>'scientific_results_modified' IS DISTINCT FROM 'false'
       OR NEW.payload->>'claim_faithfulness_verified' IS DISTINCT FROM 'false'
       OR NEW.payload->>'cloud_transmission' IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'presentation does not match candidate or guarded draft payload';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER candidate_presentation_guard BEFORE INSERT ON candidate_presentation
  FOR EACH ROW EXECUTE FUNCTION candidate_presentation_validate();
CREATE TRIGGER candidate_presentation_no_change BEFORE UPDATE OR DELETE ON candidate_presentation
  FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();
INSERT INTO schema_migrations (version) VALUES ('060_candidate_presentation');
