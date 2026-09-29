CREATE TRIGGER composition_assessment_no_delete BEFORE DELETE ON composition_assessment_snapshot
    FOR EACH ROW EXECUTE FUNCTION query_expansion_no_update();
INSERT INTO schema_migrations(version) VALUES ('029_assessment_delete_guard');
