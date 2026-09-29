-- Append-only patent/news observations. These channels never modify scientific score.

CREATE TABLE external_evidence_observation (
    observation_id UUID PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('gdelt_doc_2_0','epo_ops')),
    role TEXT NOT NULL CHECK (role IN ('news_attention_only','patent_landscape_only')),
    topic_id TEXT NOT NULL CHECK (length(btrim(topic_id)) BETWEEN 1 AND 200),
    status TEXT NOT NULL CHECK (length(btrim(status)) BETWEEN 1 AND 80),
    report_payload_sha256 TEXT NOT NULL CHECK (length(report_payload_sha256)=64),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX external_evidence_content_unique
    ON external_evidence_observation(source,report_payload_sha256);
CREATE INDEX external_evidence_history_idx
    ON external_evidence_observation(topic_id,source,created_at,observation_id);

CREATE FUNCTION external_evidence_observation_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload->>'source' IS DISTINCT FROM NEW.source
       OR NEW.payload->>'role' IS DISTINCT FROM NEW.role
       OR NEW.payload->>'topic_id' IS DISTINCT FROM NEW.topic_id
       OR NEW.payload->>'status' IS DISTINCT FROM NEW.status
       OR NEW.payload->>'report_payload_sha256' IS DISTINCT FROM NEW.report_payload_sha256
       OR NEW.payload->>'scientific_score_modified' IS DISTINCT FROM 'false'
       OR NEW.payload->>'missing_is_zero' IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'external evidence columns and guarded payload must agree';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER external_evidence_input_guard
    BEFORE INSERT ON external_evidence_observation
    FOR EACH ROW EXECUTE FUNCTION external_evidence_observation_validate();
CREATE TRIGGER external_evidence_no_change
    BEFORE UPDATE OR DELETE ON external_evidence_observation
    FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();

INSERT INTO schema_migrations (version) VALUES ('042_external_evidence_observations');
