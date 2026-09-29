-- Append-only expert opinions for immutable completed score candidates.

CREATE FUNCTION protect_completed_signal_candidate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM analysis_run WHERE run_id = OLD.run_id AND status = 'done') THEN
        RAISE EXCEPTION 'completed signal candidate is immutable; create a new score run';
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER completed_signal_candidate_no_change
    BEFORE UPDATE OR DELETE ON signal_candidate
    FOR EACH ROW EXECUTE FUNCTION protect_completed_signal_candidate();

CREATE FUNCTION protect_completed_evidence_item() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target_candidate BIGINT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        target_candidate := NEW.candidate_id;
    ELSE
        target_candidate := OLD.candidate_id;
    END IF;
    IF EXISTS (
        SELECT 1 FROM signal_candidate s JOIN analysis_run r ON r.run_id=s.run_id
        WHERE s.candidate_id=target_candidate AND r.status='done'
    ) THEN
        RAISE EXCEPTION 'evidence of a completed score run is immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER completed_evidence_item_no_change
    BEFORE INSERT OR UPDATE OR DELETE ON evidence_item
    FOR EACH ROW EXECUTE FUNCTION protect_completed_evidence_item();

CREATE TABLE score_candidate_review (
    review_id UUID PRIMARY KEY,
    score_run_id BIGINT NOT NULL REFERENCES analysis_run(run_id) ON DELETE RESTRICT,
    candidate_id BIGINT NOT NULL REFERENCES signal_candidate(candidate_id) ON DELETE RESTRICT,
    candidate_content_sha256 TEXT NOT NULL CHECK (length(candidate_content_sha256)=64),
    reviewed_by TEXT NOT NULL CHECK (length(btrim(reviewed_by)) BETWEEN 1 AND 120),
    decision TEXT NOT NULL CHECK (decision IN ('needs_review', 'research_line_supported',
                                               'noise', 'possible_duplicate', 'insufficient_evidence')),
    rationale TEXT NOT NULL CHECK (length(btrim(rationale)) BETWEEN 20 AND 5000),
    sources JSONB NOT NULL CHECK (jsonb_typeof(sources)='array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX score_candidate_review_history_idx
    ON score_candidate_review(score_run_id,candidate_id,created_at,review_id);

CREATE FUNCTION score_candidate_review_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM signal_candidate s JOIN analysis_run r ON r.run_id=s.run_id
        WHERE s.candidate_id=NEW.candidate_id AND s.run_id=NEW.score_run_id
              AND r.kind='score' AND r.status='done'
    ) THEN
        RAISE EXCEPTION 'review must reference a candidate in the exact completed score run';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER score_candidate_review_input_guard
    BEFORE INSERT ON score_candidate_review
    FOR EACH ROW EXECUTE FUNCTION score_candidate_review_validate();

CREATE TRIGGER score_candidate_review_no_change
    BEFORE UPDATE OR DELETE ON score_candidate_review
    FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();

INSERT INTO schema_migrations (version) VALUES ('033_score_candidate_reviews');
