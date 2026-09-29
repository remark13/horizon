-- Explicit exact-phrase compilation for a previously approved query plan.

CREATE TABLE compiled_query_plan (
    compilation_id UUID PRIMARY KEY,
    approved_query_plan_id UUID NOT NULL
        REFERENCES approved_query_plan(plan_id) ON DELETE RESTRICT,
    approved_plan_payload_sha256 TEXT NOT NULL CHECK (length(approved_plan_payload_sha256)=64),
    compiled_by TEXT NOT NULL CHECK (length(btrim(compiled_by)) BETWEEN 1 AND 120),
    branch_specs JSONB NOT NULL CHECK (jsonb_typeof(branch_specs)='array'),
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256)=64),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX compiled_query_plan_history_idx
    ON compiled_query_plan(approved_query_plan_id,created_at,compilation_id);

CREATE FUNCTION compiled_query_plan_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload->>'compilation_id' IS DISTINCT FROM NEW.compilation_id::text
       OR NEW.payload->>'approved_query_plan_id' IS DISTINCT FROM NEW.approved_query_plan_id::text
       OR NEW.payload->>'approved_plan_payload_sha256' IS DISTINCT FROM NEW.approved_plan_payload_sha256
       OR NEW.payload->>'compiled_by' IS DISTINCT FROM NEW.compiled_by
       OR NEW.payload->'branch_specs' IS DISTINCT FROM NEW.branch_specs
       OR NEW.payload->>'payload_sha256' IS DISTINCT FROM NEW.payload_sha256
       OR NEW.payload->>'complete' IS DISTINCT FROM 'true'
       OR NEW.payload->>'append_only' IS DISTINCT FROM 'true'
       OR NEW.payload->>'automatic_translation' IS DISTINCT FROM 'false'
       OR NEW.payload->>'scientific_result' IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'compiled query plan columns and guarded payload must agree';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER compiled_query_plan_input_guard
    BEFORE INSERT ON compiled_query_plan
    FOR EACH ROW EXECUTE FUNCTION compiled_query_plan_validate();
CREATE TRIGGER compiled_query_plan_no_change
    BEFORE UPDATE OR DELETE ON compiled_query_plan
    FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();

INSERT INTO schema_migrations (version) VALUES ('047_compiled_query_plans');
