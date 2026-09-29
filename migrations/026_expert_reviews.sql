CREATE TABLE expert_review (
    review_id UUID PRIMARY KEY,
    snapshot_id UUID NOT NULL REFERENCES hybrid_snapshot ON DELETE RESTRICT,
    candidate_id TEXT NOT NULL,
    snapshot_content_sha256 TEXT NOT NULL,
    reviewed_by TEXT NOT NULL CHECK (length(btrim(reviewed_by)) BETWEEN 1 AND 120),
    decision TEXT NOT NULL CHECK (decision IN ('needs_review', 'research_line_supported',
                                             'noise', 'possible_duplicate', 'insufficient_evidence')),
    rationale TEXT NOT NULL CHECK (length(btrim(rationale)) BETWEEN 20 AND 5000),
    sources JSONB NOT NULL CHECK (jsonb_typeof(sources) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX expert_review_candidate_idx ON expert_review (snapshot_id, candidate_id, created_at);
CREATE FUNCTION expert_review_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM hybrid_snapshot h,
             LATERAL jsonb_array_elements(h.payload->'candidates') c
        WHERE h.snapshot_id = NEW.snapshot_id AND h.content_sha256 = NEW.snapshot_content_sha256
              AND c->>'candidate_id' = NEW.candidate_id
    ) THEN
        RAISE EXCEPTION 'expert review must reference a candidate in the exact frozen snapshot';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER expert_review_input_guard BEFORE INSERT ON expert_review
    FOR EACH ROW EXECUTE FUNCTION expert_review_validate();
CREATE FUNCTION expert_review_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'expert review history is append-only; add a new opinion';
END;
$$;
CREATE TRIGGER expert_review_no_change BEFORE UPDATE OR DELETE ON expert_review
    FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();
INSERT INTO schema_migrations (version) VALUES ('026_expert_reviews');
