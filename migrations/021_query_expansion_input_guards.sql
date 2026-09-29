CREATE OR REPLACE FUNCTION query_proposal_input_guard() RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM quality_generation q
        JOIN analysis_run r ON r.run_id = q.normalize_run_id
        JOIN query_version b ON b.query_version_id = NEW.base_query_version_id
        WHERE q.generation_id = NEW.quality_generation_id AND q.status = 'done'
          AND r.run_id = NEW.normalize_run_id AND r.status = 'done' AND r.kind = 'normalize'
          AND r.mission_id = NEW.mission_id AND b.mission_id = NEW.mission_id
          AND NEW.payload->>'base_query_version_id' = NEW.base_query_version_id
          AND NEW.payload#>>'{provenance,query_version_id}' = r.query_version_id
          AND NEW.payload#>>'{provenance,normalize_run_id}' = r.run_id::text
          AND NEW.payload#>>'{provenance,quality_generation_id}' = q.generation_id::text
    ) THEN
        RAISE EXCEPTION 'Предложение должно ссылаться на согласованные завершённые входы своей миссии.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER query_proposal_input_guard BEFORE INSERT ON query_expansion_proposal
    FOR EACH ROW EXECUTE FUNCTION query_proposal_input_guard();

CREATE OR REPLACE FUNCTION query_decision_input_guard() RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM query_expansion_proposal p
        JOIN query_version q ON q.query_version_id = NEW.query_version_id
        WHERE p.proposal_id = NEW.proposal_id AND q.mission_id = p.mission_id
          AND q.payload#>>'{query_expansion,proposal_id}' = p.proposal_id::text
          AND q.payload#>>'{query_expansion,selection_sha256}' = NEW.selection_sha256
          AND q.payload#>'{query_expansion,selected_ids}' = to_jsonb(NEW.selected_ids)
    ) OR EXISTS (
        SELECT 1 FROM unnest(NEW.selected_ids) selected_id
        WHERE NOT EXISTS (
            SELECT 1 FROM query_expansion_proposal p,
                          jsonb_array_elements(p.payload->'suggestions') item
            WHERE p.proposal_id = NEW.proposal_id AND item->>'suggestion_id' = selected_id
        )
    ) THEN
        RAISE EXCEPTION 'Решение должно соответствовать сохранённому предложению и новой версии запроса.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER query_decision_input_guard BEFORE INSERT ON query_expansion_decision
    FOR EACH ROW EXECUTE FUNCTION query_decision_input_guard();
INSERT INTO schema_migrations (version) VALUES ('021_query_expansion_input_guards');
