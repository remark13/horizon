-- Explicit adjudication of two independent retrieval reviews.

CREATE TABLE retrieval_review_adjudication (
    adjudication_id UUID PRIMARY KEY,
    package_id UUID NOT NULL,
    packet_payload_sha256 TEXT NOT NULL CHECK (length(packet_payload_sha256)=64),
    left_submission_id UUID NOT NULL
        REFERENCES retrieval_review_submission(submission_id) ON DELETE RESTRICT,
    right_submission_id UUID NOT NULL
        REFERENCES retrieval_review_submission(submission_id) ON DELETE RESTRICT,
    adjudicator_id TEXT NOT NULL CHECK (length(btrim(adjudicator_id)) BETWEEN 1 AND 120),
    adjudication_payload_sha256 TEXT NOT NULL CHECK (length(adjudication_payload_sha256)=64),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (left_submission_id <> right_submission_id)
);
CREATE UNIQUE INDEX retrieval_review_adjudication_content_unique
    ON retrieval_review_adjudication(
        package_id,left_submission_id,right_submission_id,adjudication_payload_sha256
    );
CREATE INDEX retrieval_review_adjudication_history_idx
    ON retrieval_review_adjudication(package_id,created_at,adjudication_id);

CREATE FUNCTION retrieval_review_adjudication_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload->>'package_id' IS DISTINCT FROM NEW.package_id::text
       OR NEW.payload->>'packet_payload_sha256' IS DISTINCT FROM NEW.packet_payload_sha256
       OR NEW.payload->'stored_submission_ids'->>0 IS DISTINCT FROM NEW.left_submission_id::text
       OR NEW.payload->'stored_submission_ids'->>1 IS DISTINCT FROM NEW.right_submission_id::text
       OR NEW.payload->>'adjudicator_id' IS DISTINCT FROM NEW.adjudicator_id
       OR NEW.payload->>'adjudication_payload_sha256' IS DISTINCT FROM NEW.adjudication_payload_sha256
       OR NEW.payload->>'complete' IS DISTINCT FROM 'true'
       OR NEW.payload->>'precision_available' IS DISTINCT FROM 'true'
       OR NEW.payload->>'weak_signal_accuracy_measured' IS DISTINCT FROM 'false'
       OR NEW.payload->>'production_change_allowed' IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'retrieval adjudication columns and guarded payload must agree';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER retrieval_review_adjudication_input_guard
    BEFORE INSERT ON retrieval_review_adjudication
    FOR EACH ROW EXECUTE FUNCTION retrieval_review_adjudication_validate();
CREATE TRIGGER retrieval_review_adjudication_no_change
    BEFORE UPDATE OR DELETE ON retrieval_review_adjudication
    FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();

INSERT INTO schema_migrations (version) VALUES ('048_retrieval_review_adjudications');
