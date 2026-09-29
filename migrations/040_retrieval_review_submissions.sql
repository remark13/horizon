-- Complete retrieval-relevance reviews. Individual opinions remain non-gold.

CREATE TABLE retrieval_review_submission (
    submission_id UUID PRIMARY KEY,
    package_id UUID NOT NULL,
    packet_payload_sha256 TEXT NOT NULL CHECK (length(packet_payload_sha256)=64),
    reviewer_id TEXT NOT NULL CHECK (length(btrim(reviewer_id)) BETWEEN 1 AND 120),
    submission_payload_sha256 TEXT NOT NULL CHECK (length(submission_payload_sha256)=64),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX retrieval_review_submission_content_unique
    ON retrieval_review_submission(
        package_id,lower(btrim(reviewer_id)),submission_payload_sha256
    );
CREATE INDEX retrieval_review_submission_history_idx
    ON retrieval_review_submission(package_id,created_at,submission_id);

CREATE FUNCTION retrieval_review_submission_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload->>'package_id' IS DISTINCT FROM NEW.package_id::text
       OR NEW.payload->>'packet_payload_sha256' IS DISTINCT FROM NEW.packet_payload_sha256
       OR NEW.payload->>'reviewer_id' IS DISTINCT FROM NEW.reviewer_id
       OR NEW.payload->>'submission_payload_sha256' IS DISTINCT FROM NEW.submission_payload_sha256
       OR NEW.payload->>'complete' IS DISTINCT FROM 'true'
       OR NEW.payload->>'individual_opinion_not_gold' IS DISTINCT FROM 'true'
       OR NEW.payload->>'precision_available' IS DISTINCT FROM 'false'
       OR NEW.payload->>'production_change_allowed' IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'retrieval review columns and validated payload must agree';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER retrieval_review_submission_input_guard
    BEFORE INSERT ON retrieval_review_submission
    FOR EACH ROW EXECUTE FUNCTION retrieval_review_submission_validate();
CREATE TRIGGER retrieval_review_submission_no_change
    BEFORE UPDATE OR DELETE ON retrieval_review_submission
    FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();

INSERT INTO schema_migrations (version) VALUES ('040_retrieval_review_submissions');
