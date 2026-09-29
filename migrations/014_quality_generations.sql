-- Решения качества, используемые кластером, сохраняются поколением.
-- Старую work_quality оставляем для совместимости; она не паспорт нового входа.
CREATE TABLE quality_generation (
    generation_id BIGSERIAL PRIMARY KEY,
    normalize_run_id BIGINT NOT NULL REFERENCES analysis_run ON DELETE CASCADE,
    policy_version TEXT NOT NULL,
    methodology_hash TEXT NOT NULL,
    effective_config JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('created', 'done'))
);
CREATE TABLE quality_snapshot (
    generation_id BIGINT NOT NULL REFERENCES quality_generation ON DELETE CASCADE,
    work_id BIGINT NOT NULL REFERENCES work ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK (decision IN ('include', 'exclude', 'quarantine')),
    relevance_score DOUBLE PRECISION NOT NULL,
    source_quality DOUBLE PRECISION NOT NULL,
    matched_terms TEXT[] NOT NULL DEFAULT '{}',
    flags JSONB NOT NULL DEFAULT '{}',
    reasons TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (generation_id, work_id)
);
CREATE INDEX quality_generation_input_idx ON quality_generation (normalize_run_id, generation_id DESC);
INSERT INTO schema_migrations (version) VALUES ('014_quality_generations');
