CREATE FUNCTION forbid_quality_snapshot_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'quality_snapshot is immutable; create a new generation';
END;
$$;
CREATE TRIGGER quality_snapshot_no_update BEFORE UPDATE ON quality_snapshot
    FOR EACH ROW EXECUTE FUNCTION forbid_quality_snapshot_update();
CREATE FUNCTION protect_quality_generation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'done' OR
       NEW.normalize_run_id IS DISTINCT FROM OLD.normalize_run_id OR
       NEW.policy_version IS DISTINCT FROM OLD.policy_version OR
       NEW.methodology_hash IS DISTINCT FROM OLD.methodology_hash OR
       NEW.effective_config IS DISTINCT FROM OLD.effective_config THEN
        RAISE EXCEPTION 'quality generation inputs are immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER quality_generation_protect BEFORE UPDATE ON quality_generation
    FOR EACH ROW EXECUTE FUNCTION protect_quality_generation();
INSERT INTO schema_migrations (version) VALUES ('015_quality_snapshot_guards');
