CREATE FUNCTION protect_completed_work() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM analysis_run WHERE run_id = OLD.run_id AND status = 'done') THEN
        RAISE EXCEPTION 'completed normalized corpus is immutable; create a new generation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER work_snapshot_protect BEFORE UPDATE ON work
    FOR EACH ROW EXECUTE FUNCTION protect_completed_work();
INSERT INTO schema_migrations (version) VALUES ('019_work_snapshot_guard');
