CREATE FUNCTION protect_work_embedding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'embedding inputs are immutable; use a new model revision or corpus';
    END IF;
    IF EXISTS (
        SELECT 1 FROM analysis_run r JOIN work w ON w.run_id = r.upstream_run_id
        WHERE w.work_id = OLD.work_id AND r.kind = 'cluster' AND r.status = 'done'
            AND r.embedding_model = OLD.model
    ) THEN
        RAISE EXCEPTION 'embedding is an input of a completed cluster';
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER work_embedding_protect BEFORE UPDATE OR DELETE ON work_embedding
    FOR EACH ROW EXECUTE FUNCTION protect_work_embedding();
INSERT INTO schema_migrations (version) VALUES ('017_embedding_guards');
