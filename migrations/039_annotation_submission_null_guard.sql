-- Fix the 038 guard: ordinary <> does not reject NULL/missing JSON fields.

CREATE OR REPLACE FUNCTION annotation_submission_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload->>'package_id' IS DISTINCT FROM NEW.package_id::text
       OR NEW.payload->>'packet_payload_sha256' IS DISTINCT FROM NEW.packet_payload_sha256
       OR NEW.payload->>'reviewer_id' IS DISTINCT FROM NEW.reviewer_id
       OR NEW.payload->>'submission_payload_sha256' IS DISTINCT FROM NEW.submission_payload_sha256
       OR NEW.payload->>'complete' IS DISTINCT FROM 'true'
       OR NEW.payload->>'individual_opinion_not_gold' IS DISTINCT FROM 'true'
       OR NEW.payload->>'calibration_eligible' IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'annotation submission columns and validated payload must agree';
    END IF;
    RETURN NEW;
END;
$$;

INSERT INTO schema_migrations (version) VALUES ('039_annotation_submission_null_guard');
