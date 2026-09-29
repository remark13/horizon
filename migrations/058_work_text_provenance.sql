-- New normalization generations only. Old work text is never rewritten.
CREATE TABLE work_text_provenance (
    work_id BIGINT PRIMARY KEY REFERENCES work ON DELETE CASCADE,
    run_id BIGINT NOT NULL REFERENCES analysis_run ON DELETE CASCADE,
    policy_version TEXT NOT NULL,
    abstract_raw_record_id BIGINT REFERENCES raw_record ON DELETE RESTRICT,
    abstract_sha256 TEXT CHECK (abstract_sha256 IS NULL OR length(abstract_sha256)=64),
    selection JSONB NOT NULL CHECK (jsonb_typeof(selection)='object'),
    CHECK ((abstract_raw_record_id IS NULL) = (abstract_sha256 IS NULL))
);
CREATE INDEX work_text_provenance_run_idx ON work_text_provenance(run_id);

CREATE FUNCTION protect_work_text_provenance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_run BIGINT;
    parent_status TEXT;
    parent_kind TEXT;
    current_abstract TEXT;
BEGIN
    IF TG_OP='DELETE' THEN
        IF EXISTS (SELECT 1 FROM work w JOIN analysis_run r ON r.run_id=w.run_id
                   WHERE w.work_id=OLD.work_id AND r.status='done') THEN
            RAISE EXCEPTION 'completed canonical text provenance is immutable';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' AND EXISTS (
        SELECT 1 FROM analysis_run WHERE run_id=OLD.run_id AND status='done'
    ) THEN
        RAISE EXCEPTION 'completed canonical text provenance is immutable';
    END IF;
    SELECT w.run_id,r.status,r.kind,w.abstract INTO parent_run,parent_status,parent_kind,current_abstract
        FROM work w JOIN analysis_run r ON r.run_id=w.run_id WHERE w.work_id=NEW.work_id;
    IF parent_run IS NULL OR parent_run<>NEW.run_id
       OR parent_status<>'normalizing' OR parent_kind<>'normalize' THEN
        RAISE EXCEPTION 'text provenance requires its own active normalization';
    END IF;
    IF NEW.abstract_sha256 IS DISTINCT FROM
       encode(sha256(convert_to(current_abstract,'UTF8')),'hex') THEN
        RAISE EXCEPTION 'canonical abstract and recorded checksum disagree';
    END IF;
    IF NEW.abstract_raw_record_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM raw_record raw
        JOIN work_version v ON v.work_id=NEW.work_id
          AND v.source=raw.source AND v.source_record_id=raw.source_record_id
        JOIN source_snapshot s ON s.snapshot_id=raw.snapshot_id
        JOIN analysis_run r ON r.run_id=NEW.run_id
        WHERE raw.raw_record_id=NEW.abstract_raw_record_id AND (
          (r.notes->>'collection_batch_id' IS NULL AND s.query_version_id=r.query_version_id)
          OR EXISTS (SELECT 1 FROM collection_batch_snapshot bs
                     WHERE bs.snapshot_id=s.snapshot_id
                       AND bs.batch_id=(r.notes->>'collection_batch_id')::bigint)
        )
    ) THEN
        RAISE EXCEPTION 'selected abstract source is not bound to this work and input batch';
    END IF;
    IF NEW.selection->>'policy_version' IS DISTINCT FROM NEW.policy_version
       OR NEW.selection->>'abstract_sha256' IS DISTINCT FROM NEW.abstract_sha256
       OR NEW.selection->>'chosen_raw_record_id' IS DISTINCT FROM NEW.abstract_raw_record_id::text THEN
        RAISE EXCEPTION 'text provenance columns and selection payload disagree';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER work_text_provenance_guard BEFORE INSERT OR UPDATE OR DELETE
    ON work_text_provenance FOR EACH ROW EXECUTE FUNCTION protect_work_text_provenance();
INSERT INTO schema_migrations (version) VALUES ('058_work_text_provenance');
