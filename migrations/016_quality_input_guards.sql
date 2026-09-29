-- Нельзя добавить решение после закрытия поколения или подмешать работу
-- другого корпуса. UPDATE уже запрещён миграцией 015.
CREATE FUNCTION validate_quality_snapshot_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE generation_run BIGINT;
DECLARE generation_status TEXT;
DECLARE work_run BIGINT;
BEGIN
    SELECT normalize_run_id, status INTO generation_run, generation_status
        FROM quality_generation WHERE generation_id = NEW.generation_id FOR UPDATE;
    SELECT run_id INTO work_run FROM work WHERE work_id = NEW.work_id;
    IF generation_status IS DISTINCT FROM 'created' OR
       work_run IS DISTINCT FROM generation_run THEN
        RAISE EXCEPTION 'quality snapshot requires an open generation and its own corpus';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER quality_snapshot_validate BEFORE INSERT ON quality_snapshot
    FOR EACH ROW EXECUTE FUNCTION validate_quality_snapshot_input();
CREATE FUNCTION validate_quality_generation_input() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status <> 'created' OR NOT EXISTS (
        SELECT 1 FROM analysis_run WHERE run_id = NEW.normalize_run_id
            AND kind = 'normalize' AND status = 'done'
    ) THEN
        RAISE EXCEPTION 'quality generation requires a completed normalization';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER quality_generation_validate BEFORE INSERT ON quality_generation
    FOR EACH ROW EXECUTE FUNCTION validate_quality_generation_input();
INSERT INTO schema_migrations (version) VALUES ('016_quality_input_guards');
