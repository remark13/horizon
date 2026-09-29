-- SAIA 011 — измеримая релевантность и карантин метаданных.

CREATE TABLE work_quality (
    work_id          BIGINT PRIMARY KEY REFERENCES work ON DELETE CASCADE,
    run_id           BIGINT NOT NULL REFERENCES analysis_run ON DELETE CASCADE,
    policy_version   TEXT NOT NULL,
    decision         TEXT NOT NULL CHECK (decision IN ('include', 'exclude', 'quarantine')),
    relevance_score  DOUBLE PRECISION NOT NULL CHECK (relevance_score BETWEEN 0 AND 1),
    source_quality   DOUBLE PRECISION NOT NULL CHECK (source_quality BETWEEN 0 AND 1),
    matched_terms    TEXT[] NOT NULL DEFAULT '{}',
    flags            JSONB NOT NULL DEFAULT '{}'::jsonb,
    reasons          TEXT[] NOT NULL DEFAULT '{}',
    evaluated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX work_quality_run_decision_idx
    ON work_quality (run_id, decision);

INSERT INTO schema_migrations (version) VALUES ('011_work_quality');
