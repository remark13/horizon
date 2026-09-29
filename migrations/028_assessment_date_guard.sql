CREATE FUNCTION composition_assessment_date_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload->>'as_of_date' IS DISTINCT FROM (
        SELECT h.payload->>'as_of_date' FROM hybrid_snapshot h WHERE h.snapshot_id = NEW.snapshot_id
    ) THEN
        RAISE EXCEPTION 'Дата оценки должна совпадать с датой точного гибридного снимка.';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER composition_assessment_date_guard BEFORE INSERT ON composition_assessment_snapshot
    FOR EACH ROW EXECUTE FUNCTION composition_assessment_date_validate();
INSERT INTO schema_migrations(version) VALUES ('028_assessment_date_guard');
