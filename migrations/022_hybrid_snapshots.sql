CREATE TABLE hybrid_snapshot (
    snapshot_id UUID PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    normalize_run_id BIGINT NOT NULL REFERENCES analysis_run ON DELETE RESTRICT,
    quality_generation_id BIGINT NOT NULL REFERENCES quality_generation ON DELETE RESTRICT,
    cluster_run_id BIGINT NOT NULL REFERENCES analysis_run ON DELETE RESTRICT,
    payload JSONB NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE FUNCTION hybrid_snapshot_validate() RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM analysis_run c JOIN analysis_run n ON n.run_id = c.upstream_run_id
        JOIN quality_generation q ON q.normalize_run_id = n.run_id
        WHERE c.run_id = NEW.cluster_run_id AND n.run_id = NEW.normalize_run_id
          AND q.generation_id = NEW.quality_generation_id
          AND c.mission_id = NEW.mission_id AND n.mission_id = NEW.mission_id
          AND c.kind = 'cluster' AND n.kind = 'normalize'
          AND c.status = 'done' AND n.status = 'done' AND q.status = 'done'
          AND (c.notes->>'quality_generation_id')::bigint = q.generation_id
          AND c.as_of_date = n.as_of_date AND c.query_version_id = n.query_version_id
          AND (NEW.payload->'provenance'->>'normalize_run_id')::bigint = n.run_id
          AND (NEW.payload->'provenance'->>'quality_generation_id')::bigint = q.generation_id
          AND (NEW.payload->'provenance'->>'cluster_run_id')::bigint = c.run_id
          AND NEW.payload->'provenance'->>'query_version_id' = n.query_version_id
    ) THEN
        RAISE EXCEPTION 'Гибрид требует согласованные завершённые входы корпуса, качества и кластеризации.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER hybrid_snapshot_input_guard BEFORE INSERT ON hybrid_snapshot
    FOR EACH ROW EXECUTE FUNCTION hybrid_snapshot_validate();
CREATE TRIGGER hybrid_snapshot_no_update BEFORE UPDATE ON hybrid_snapshot
    FOR EACH ROW EXECUTE FUNCTION query_expansion_no_update();
INSERT INTO schema_migrations (version) VALUES ('022_hybrid_snapshots');
