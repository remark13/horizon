CREATE TABLE query_expansion_proposal (
    proposal_id UUID PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    base_query_version_id TEXT NOT NULL REFERENCES query_version ON DELETE CASCADE,
    normalize_run_id BIGINT NOT NULL REFERENCES analysis_run ON DELETE RESTRICT,
    quality_generation_id BIGINT NOT NULL REFERENCES quality_generation ON DELETE RESTRICT,
    payload JSONB NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE query_expansion_decision (
    proposal_id UUID PRIMARY KEY REFERENCES query_expansion_proposal ON DELETE CASCADE,
    query_version_id TEXT NOT NULL UNIQUE REFERENCES query_version ON DELETE CASCADE,
    selection_sha256 TEXT NOT NULL,
    selected_ids TEXT[] NOT NULL,
    exclusions TEXT[] NOT NULL,
    reviewed_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION query_expansion_no_update() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Предложение/решение расширения неизменяемо; создайте новое предложение.';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER query_proposal_no_update BEFORE UPDATE ON query_expansion_proposal
    FOR EACH ROW EXECUTE FUNCTION query_expansion_no_update();
CREATE TRIGGER query_decision_no_update BEFORE UPDATE ON query_expansion_decision
    FOR EACH ROW EXECUTE FUNCTION query_expansion_no_update();
CREATE OR REPLACE FUNCTION used_query_no_update() RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM analysis_run WHERE query_version_id = OLD.query_version_id)
       OR EXISTS (SELECT 1 FROM query_expansion_decision WHERE query_version_id = OLD.query_version_id)
       OR EXISTS (SELECT 1 FROM query_expansion_proposal WHERE base_query_version_id = OLD.query_version_id) THEN
        RAISE EXCEPTION 'Использованную версию запроса нельзя изменить; создайте новую версию.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER used_query_no_update BEFORE UPDATE ON query_version
    FOR EACH ROW EXECUTE FUNCTION used_query_no_update();
INSERT INTO schema_migrations (version) VALUES ('020_controlled_query_expansion');
