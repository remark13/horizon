-- SAIA 012 — объяснимая карточка тематического кандидата.

CREATE TABLE signal_candidate (
    candidate_id        BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES analysis_run ON DELETE CASCADE,
    topic_id            BIGINT NOT NULL REFERENCES topic ON DELETE CASCADE,
    status              TEXT NOT NULL,
    label               TEXT NOT NULL,
    label_basis         TEXT NOT NULL,
    first_found         DATE,
    research_birth      DATE,
    signal_detected     DATE,
    emergence_score     DOUBLE PRECISION,
    evidence_confidence DOUBLE PRECISION NOT NULL,
    metrics             JSONB NOT NULL,
    gates               JSONB NOT NULL,
    limitations         TEXT[] NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, topic_id)
);

CREATE TABLE evidence_item (
    evidence_id    BIGSERIAL PRIMARY KEY,
    candidate_id   BIGINT NOT NULL REFERENCES signal_candidate ON DELETE CASCADE,
    work_id        BIGINT NOT NULL REFERENCES work ON DELETE RESTRICT,
    evidence_rank  INT NOT NULL,
    role           TEXT NOT NULL,
    rationale      TEXT NOT NULL,
    sources        JSONB NOT NULL,
    UNIQUE (candidate_id, work_id)
);

CREATE INDEX signal_candidate_run_status_idx ON signal_candidate (run_id, status);
CREATE INDEX evidence_item_candidate_rank_idx ON evidence_item (candidate_id, evidence_rank);

INSERT INTO schema_migrations (version) VALUES ('012_signal_candidates');
