-- Freeze existing state, without claiming verification of legacy file manifests.
ALTER TABLE collection_batch ADD COLUMN seal_status TEXT NOT NULL DEFAULT 'legacy_frozen'
    CHECK (seal_status IN ('open', 'sealed', 'legacy_frozen'));
ALTER TABLE collection_batch ALTER COLUMN seal_status SET DEFAULT 'open';
ALTER TABLE collection_batch ADD COLUMN sealed_at TIMESTAMPTZ;

-- The same bytes under another query must not reuse a period-filtered parse.
DO $$
DECLARE found text;
BEGIN
    SELECT con.conname INTO found FROM pg_constraint con
    WHERE con.conrelid = 'source_snapshot'::regclass AND con.contype = 'u'
      AND (SELECT array_agg(att.attname::text ORDER BY att.attname::text)
           FROM unnest(con.conkey) k(attnum) JOIN pg_attribute att
           ON att.attrelid = con.conrelid AND att.attnum = k.attnum)
          = ARRAY['file_name','file_sha256','mission_id','source'];
    IF found IS NULL THEN RAISE EXCEPTION 'source_snapshot legacy UNIQUE not found'; END IF;
    EXECUTE format('ALTER TABLE source_snapshot DROP CONSTRAINT %I', found);
END $$;
ALTER TABLE source_snapshot ADD CONSTRAINT source_snapshot_unique_query_file
    UNIQUE (mission_id, query_version_id, source, file_name, file_sha256);

CREATE FUNCTION protect_collection_batch() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE expected jsonb; item jsonb; actual_count bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.seal_status <> 'open' OR NEW.sealed_at IS NOT NULL THEN
            RAISE EXCEPTION 'collection must be created open and sealed after import';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM analysis_run WHERE (notes->>'collection_batch_id') = OLD.batch_id::text) THEN
            RAISE EXCEPTION 'collection batch is referenced by an analysis run';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.seal_status <> 'open' THEN RAISE EXCEPTION 'sealed collection batch is immutable'; END IF;
    IF NEW.mission_id IS DISTINCT FROM OLD.mission_id
       OR NEW.query_version_id IS DISTINCT FROM OLD.query_version_id
       OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
       OR NEW.connector_version IS DISTINCT FROM OLD.connector_version
       OR NEW.fetched_at IS DISTINCT FROM OLD.fetched_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.status IS DISTINCT FROM OLD.status
       OR NEW.coverage IS DISTINCT FROM OLD.coverage THEN
        RAISE EXCEPTION 'collection inputs are immutable; create another package';
    END IF;
    IF NEW.seal_status <> 'sealed' OR NEW.sealed_at IS NULL THEN
        RAISE EXCEPTION 'only open-to-sealed transition is allowed';
    END IF;
    expected := NEW.coverage->'expected_corpus_files';
    IF expected IS NULL OR jsonb_typeof(expected) <> 'array' THEN
        RAISE EXCEPTION 'sealing requires declared corpus file membership';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM query_version q WHERE q.query_version_id = NEW.query_version_id
        AND q.mission_id = NEW.mission_id AND q.content_sha256 = NEW.coverage->>'mission_file_sha256') THEN
        RAISE EXCEPTION 'collection configuration hash/query binding mismatch';
    END IF;
    SELECT count(*) INTO actual_count FROM collection_batch_snapshot WHERE batch_id = NEW.batch_id;
    IF actual_count <> jsonb_array_length(expected) THEN
        RAISE EXCEPTION 'collection file membership count mismatch';
    END IF;
    IF (SELECT count(*) FROM (SELECT value->>'source', value->>'file', value->>'sha256'
        FROM jsonb_array_elements(expected) GROUP BY 1,2,3) x) <> actual_count THEN
        RAISE EXCEPTION 'duplicate declared collection file membership';
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(expected) LOOP
        IF NOT EXISTS (SELECT 1 FROM collection_batch_snapshot bs JOIN source_snapshot s USING (snapshot_id)
            WHERE bs.batch_id = NEW.batch_id AND s.mission_id = NEW.mission_id
              AND s.query_version_id = NEW.query_version_id AND s.source = item->>'source'
              AND s.file_name = item->>'file' AND s.file_sha256 = item->>'sha256'
              AND s.record_count = (item->>'records')::bigint) THEN
            RAISE EXCEPTION 'declared collection file is missing or belongs to another query';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;
CREATE TRIGGER collection_batch_protect BEFORE INSERT OR UPDATE OR DELETE ON collection_batch
    FOR EACH ROW EXECUTE FUNCTION protect_collection_batch();

CREATE FUNCTION protect_collection_membership() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE parent_state text; batch bigint;
BEGIN
    IF TG_OP = 'UPDATE' THEN RAISE EXCEPTION 'collection membership cannot be reassigned'; END IF;
    batch := CASE WHEN TG_OP = 'INSERT' THEN NEW.batch_id ELSE OLD.batch_id END;
    SELECT seal_status INTO parent_state FROM collection_batch WHERE batch_id = batch FOR SHARE;
    IF parent_state IS NOT NULL AND parent_state <> 'open' THEN
        RAISE EXCEPTION 'sealed collection membership is immutable';
    END IF;
    IF TG_OP = 'INSERT' AND NOT EXISTS (SELECT 1 FROM collection_batch b JOIN source_snapshot s
        ON s.snapshot_id = NEW.snapshot_id WHERE b.batch_id = NEW.batch_id
          AND b.mission_id = s.mission_id AND b.query_version_id = s.query_version_id) THEN
        RAISE EXCEPTION 'snapshot and collection must belong to the same mission/query';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER collection_membership_protect BEFORE INSERT OR UPDATE OR DELETE ON collection_batch_snapshot
    FOR EACH ROW EXECUTE FUNCTION protect_collection_membership();

CREATE FUNCTION protect_source_snapshot() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'source snapshot metadata are immutable'; END $$;
CREATE TRIGGER source_snapshot_no_update BEFORE UPDATE ON source_snapshot
    FOR EACH ROW EXECUTE FUNCTION protect_source_snapshot();

CREATE FUNCTION protect_collected_query() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM collection_batch WHERE query_version_id = OLD.query_version_id) THEN
        RAISE EXCEPTION 'collected query version is immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER collected_query_no_update BEFORE UPDATE ON query_version
    FOR EACH ROW EXECUTE FUNCTION protect_collected_query();

CREATE FUNCTION protect_sealed_raw_append() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM collection_batch_snapshot bs JOIN collection_batch b USING (batch_id)
        WHERE bs.snapshot_id = NEW.snapshot_id AND b.seal_status <> 'open') THEN
        RAISE EXCEPTION 'cannot append raw records to a sealed collection snapshot';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER sealed_raw_no_append BEFORE INSERT ON raw_record
    FOR EACH ROW EXECUTE FUNCTION protect_sealed_raw_append();
INSERT INTO schema_migrations (version) VALUES ('023_collection_seals');
