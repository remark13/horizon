CREATE TABLE collection_coverage_passport (
    coverage_passport_id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL REFERENCES collection_batch(batch_id) ON DELETE RESTRICT,
    comparison_scope TEXT NOT NULL CHECK (comparison_scope IN ('within_frozen_snapshot_same_query_window')),
    temporal_comparable BOOLEAN NOT NULL,
    policy_version TEXT NOT NULL,
    evidence_payload JSONB NOT NULL,
    evidence_content_sha256 TEXT NOT NULL CHECK (length(evidence_content_sha256) = 64),
    evidence_file_sha256 TEXT NOT NULL CHECK (length(evidence_file_sha256) = 64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (batch_id, comparison_scope)
);

CREATE FUNCTION protect_collection_coverage_passport() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'collection coverage passport is immutable';
END;
$$;
CREATE TRIGGER collection_coverage_passport_immutable
    BEFORE UPDATE OR DELETE ON collection_coverage_passport
    FOR EACH ROW EXECUTE FUNCTION protect_collection_coverage_passport();

INSERT INTO schema_migrations (version) VALUES ('032_collection_coverage_passports');
