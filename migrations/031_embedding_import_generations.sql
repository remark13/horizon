CREATE TABLE embedding_import_generation (
    embedding_generation_id BIGSERIAL PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES mission ON DELETE CASCADE,
    normalize_run_id BIGINT NOT NULL REFERENCES analysis_run(run_id) ON DELETE RESTRICT,
    quality_generation_id BIGINT NOT NULL REFERENCES quality_generation(generation_id) ON DELETE RESTRICT,
    model TEXT NOT NULL,
    dimension INT NOT NULL CHECK (dimension > 0),
    row_count INT NOT NULL CHECK (row_count > 0),
    observation_report_sha256 TEXT NOT NULL CHECK (length(observation_report_sha256) = 64),
    vectors_file_sha256 TEXT NOT NULL CHECK (length(vectors_file_sha256) = 64),
    vectors_manifest_payload_sha256 TEXT NOT NULL CHECK (length(vectors_manifest_payload_sha256) = 64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (normalize_run_id, model)
);

CREATE TABLE embedding_import_member (
    embedding_generation_id BIGINT NOT NULL REFERENCES embedding_import_generation ON DELETE RESTRICT,
    work_id BIGINT NOT NULL,
    model TEXT NOT NULL,
    input_payload_sha256 TEXT NOT NULL CHECK (length(input_payload_sha256) = 64),
    PRIMARY KEY (embedding_generation_id, work_id),
    FOREIGN KEY (work_id, model) REFERENCES work_embedding(work_id, model) ON DELETE RESTRICT
);

CREATE FUNCTION protect_embedding_import_generation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'file embedding generation provenance is immutable';
END;
$$;

CREATE TRIGGER embedding_import_generation_immutable
    BEFORE UPDATE OR DELETE ON embedding_import_generation
    FOR EACH ROW EXECUTE FUNCTION protect_embedding_import_generation();
CREATE TRIGGER embedding_import_member_immutable
    BEFORE UPDATE OR DELETE ON embedding_import_member
    FOR EACH ROW EXECUTE FUNCTION protect_embedding_import_generation();

INSERT INTO schema_migrations (version) VALUES ('031_embedding_import_generations');
