-- Complete blinded-review submissions. Individual opinions remain non-gold.

CREATE TABLE annotation_submission (
    submission_id UUID PRIMARY KEY,
    package_id UUID NOT NULL,
    packet_payload_sha256 TEXT NOT NULL CHECK (length(packet_payload_sha256)=64),
    reviewer_id TEXT NOT NULL CHECK (length(btrim(reviewer_id)) BETWEEN 1 AND 120),
    submission_payload_sha256 TEXT NOT NULL CHECK (length(submission_payload_sha256)=64),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX annotation_submission_content_unique
    ON annotation_submission(package_id,lower(btrim(reviewer_id)),submission_payload_sha256);
CREATE INDEX annotation_submission_history_idx
    ON annotation_submission(package_id,created_at,submission_id);

CREATE FUNCTION annotation_submission_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload->>'package_id' <> NEW.package_id::text
       OR NEW.payload->>'packet_payload_sha256' <> NEW.packet_payload_sha256
       OR NEW.payload->>'reviewer_id' <> NEW.reviewer_id
       OR NEW.payload->>'submission_payload_sha256' <> NEW.submission_payload_sha256
       OR NEW.payload->>'complete' <> 'true'
       OR NEW.payload->>'individual_opinion_not_gold' <> 'true'
       OR NEW.payload->>'calibration_eligible' <> 'false' THEN
        RAISE EXCEPTION 'annotation submission columns and validated payload must agree';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER annotation_submission_input_guard
    BEFORE INSERT ON annotation_submission
    FOR EACH ROW EXECUTE FUNCTION annotation_submission_validate();
CREATE TRIGGER annotation_submission_no_change
    BEFORE UPDATE OR DELETE ON annotation_submission
    FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();

INSERT INTO schema_migrations (version) VALUES ('038_annotation_submissions');
