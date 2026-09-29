CREATE TABLE composition_assessment_snapshot (
    assessment_id UUID PRIMARY KEY,
    snapshot_id UUID NOT NULL REFERENCES hybrid_snapshot ON DELETE CASCADE,
    snapshot_content_sha256 TEXT NOT NULL,
    payload JSONB NOT NULL,
    content_sha256 TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (snapshot_id, content_sha256)
);
CREATE FUNCTION composition_assessment_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM hybrid_snapshot h
        WHERE h.snapshot_id = NEW.snapshot_id
          AND h.content_sha256 = NEW.snapshot_content_sha256
          AND NEW.payload->>'snapshot_id' = h.snapshot_id::text
          AND NEW.payload->>'snapshot_content_sha256' = h.content_sha256
          AND NEW.payload->'provenance' = h.payload->'provenance'
          AND NEW.payload->>'input_text_hash' = h.payload->>'input_text_hash'
          AND NEW.payload->>'assessment_content_sha256' = NEW.content_sha256
          AND NEW.payload->>'version' = NEW.policy_version
          AND NEW.payload->>'policy_hash' = NEW.policy_hash
          AND jsonb_typeof(NEW.payload->'candidates') = 'array'
          AND jsonb_array_length(NEW.payload->'candidates') = jsonb_array_length(h.payload->'candidates')
          AND NOT EXISTS (
              SELECT 1 FROM jsonb_array_elements(h.payload->'candidates') c
              WHERE (SELECT count(*) FROM jsonb_array_elements(NEW.payload->'candidates') a
                     WHERE a->>'candidate_id' = c->>'candidate_id' AND a->'work_ids' = c->'work_ids') <> 1
          )
    ) THEN
        RAISE EXCEPTION 'Оценка требует точный неизменный состав и происхождение гибридного снимка.';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER composition_assessment_input_guard BEFORE INSERT ON composition_assessment_snapshot
    FOR EACH ROW EXECUTE FUNCTION composition_assessment_validate();
CREATE TRIGGER composition_assessment_no_update BEFORE UPDATE ON composition_assessment_snapshot
    FOR EACH ROW EXECUTE FUNCTION query_expansion_no_update();
CREATE INDEX composition_assessment_history_idx ON composition_assessment_snapshot(snapshot_id, created_at DESC);
INSERT INTO schema_migrations(version) VALUES ('027_composition_assessments');
