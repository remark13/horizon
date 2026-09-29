-- Старые поколения не получают выдуманный отпечаток нового кода.
ALTER TABLE quality_generation ADD COLUMN code_version TEXT;
ALTER TABLE quality_generation ADD COLUMN runtime_provenance JSONB;
CREATE OR REPLACE FUNCTION protect_quality_generation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'done' OR
       NEW.normalize_run_id IS DISTINCT FROM OLD.normalize_run_id OR
       NEW.policy_version IS DISTINCT FROM OLD.policy_version OR
       NEW.methodology_hash IS DISTINCT FROM OLD.methodology_hash OR
       NEW.effective_config IS DISTINCT FROM OLD.effective_config OR
       NEW.code_version IS DISTINCT FROM OLD.code_version OR
       NEW.runtime_provenance IS DISTINCT FROM OLD.runtime_provenance THEN
        RAISE EXCEPTION 'quality generation inputs are immutable';
    END IF;
    RETURN NEW;
END;
$$;
INSERT INTO schema_migrations (version) VALUES ('018_quality_code_provenance');
