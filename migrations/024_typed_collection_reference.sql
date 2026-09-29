-- New runs receive a real FK, closing the delete-vs-start race.
-- Do not infer or backfill missing legacy input provenance.
ALTER TABLE analysis_run ADD COLUMN collection_batch_id BIGINT REFERENCES collection_batch ON DELETE RESTRICT;
CREATE INDEX analysis_run_collection_idx ON analysis_run (collection_batch_id);
CREATE FUNCTION validate_run_collection_binding() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND (
        NEW.collection_batch_id IS DISTINCT FROM OLD.collection_batch_id
        OR NEW.notes->>'collection_batch_id' IS DISTINCT FROM OLD.notes->>'collection_batch_id'
    ) THEN RAISE EXCEPTION 'analysis collection binding is immutable'; END IF;
    IF TG_OP = 'INSERT' AND NEW.notes->>'collection_batch_id' IS NOT NULL
       AND NEW.collection_batch_id IS NULL THEN
        RAISE EXCEPTION 'new collection reference requires a typed FK';
    END IF;
    IF NEW.collection_batch_id IS NOT NULL AND (
        NEW.notes->>'collection_batch_id' IS DISTINCT FROM NEW.collection_batch_id::text
        OR NOT EXISTS (SELECT 1 FROM collection_batch b JOIN query_version q USING (query_version_id)
            WHERE b.batch_id = NEW.collection_batch_id AND b.mission_id = NEW.mission_id
              AND b.query_version_id = NEW.query_version_id AND b.status = 'complete'
              AND b.seal_status IN ('sealed', 'legacy_frozen')
              AND (q.payload->>'as_of_date' IS NULL OR (q.payload->>'as_of_date')::date = NEW.as_of_date))
    ) THEN RAISE EXCEPTION 'run requires matching sealed collection/query/cutoff'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER analysis_run_collection_guard BEFORE INSERT OR UPDATE ON analysis_run
    FOR EACH ROW EXECUTE FUNCTION validate_run_collection_binding();
INSERT INTO schema_migrations (version) VALUES ('024_typed_collection_reference');
