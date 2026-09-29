-- Explicitly approved transparent query plans. Approval and execution are separate.

CREATE TABLE approved_query_plan (
    plan_id UUID PRIMARY KEY,
    original_query TEXT NOT NULL CHECK (length(btrim(original_query)) BETWEEN 2 AND 500),
    max_suggestions INTEGER NOT NULL CHECK (max_suggestions BETWEEN 1 AND 20),
    preview_payload_sha256 TEXT NOT NULL CHECK (length(preview_payload_sha256)=64),
    approved_by TEXT NOT NULL CHECK (length(btrim(approved_by)) BETWEEN 1 AND 120),
    selected_branch_ids JSONB NOT NULL CHECK (jsonb_typeof(selected_branch_ids)='array'),
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256)=64),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX approved_query_plan_history_idx
    ON approved_query_plan(created_at,plan_id);

CREATE FUNCTION approved_query_plan_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload->>'plan_id' IS DISTINCT FROM NEW.plan_id::text
       OR NEW.payload->>'original_query' IS DISTINCT FROM NEW.original_query
       OR (NEW.payload->>'max_suggestions')::integer IS DISTINCT FROM NEW.max_suggestions
       OR NEW.payload->>'preview_payload_sha256' IS DISTINCT FROM NEW.preview_payload_sha256
       OR NEW.payload->>'approved_by' IS DISTINCT FROM NEW.approved_by
       OR NEW.payload->'selected_branch_ids' IS DISTINCT FROM NEW.selected_branch_ids
       OR NEW.payload->>'payload_sha256' IS DISTINCT FROM NEW.payload_sha256
       OR NEW.payload->>'complete' IS DISTINCT FROM 'true'
       OR NEW.payload->>'append_only' IS DISTINCT FROM 'true'
       OR NEW.payload->>'original_query_preserved' IS DISTINCT FROM 'true'
       OR NEW.payload->>'automatic_execution' IS DISTINCT FROM 'false'
       OR NEW.payload->>'scientific_result' IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'approved query plan columns and guarded payload must agree';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER approved_query_plan_input_guard
    BEFORE INSERT ON approved_query_plan
    FOR EACH ROW EXECUTE FUNCTION approved_query_plan_validate();
CREATE TRIGGER approved_query_plan_no_change
    BEFORE UPDATE OR DELETE ON approved_query_plan
    FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();

INSERT INTO schema_migrations (version) VALUES ('045_approved_query_plans');
