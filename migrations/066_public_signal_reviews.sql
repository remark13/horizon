-- Public foresight references are not synthetic scientific candidates.
-- Opinions bind to the immutable reference snapshot stored in a dispatch.
CREATE TABLE public_signal_review (
    review_id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES expert_validation_request(request_id) ON DELETE RESTRICT,
    public_signal_id TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    reference_content_sha256 TEXT NOT NULL CHECK (length(reference_content_sha256) = 64),
    reviewed_by TEXT NOT NULL CHECK (length(btrim(reviewed_by)) BETWEEN 1 AND 120),
    decision TEXT NOT NULL CHECK (decision IN ('needs_review','signal_supported','noise','possible_duplicate','insufficient_evidence')),
    rationale TEXT NOT NULL CHECK (length(btrim(rationale)) BETWEEN 20 AND 5000),
    sources JSONB NOT NULL CHECK (jsonb_typeof(sources) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX public_signal_review_identity_idx
    ON public_signal_review(reference_content_sha256, created_at DESC);
CREATE TRIGGER public_signal_review_no_change
    BEFORE UPDATE OR DELETE ON public_signal_review
    FOR EACH ROW EXECUTE FUNCTION expert_review_append_only();
INSERT INTO schema_migrations(version) VALUES ('066_public_signal_reviews');
