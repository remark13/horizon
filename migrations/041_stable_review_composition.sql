-- Stable publication-composition identity across repeated analysis runs.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE expert_review ADD COLUMN composition_sha256 TEXT;

UPDATE expert_review e
SET composition_sha256 = matched.composition_sha256
FROM hybrid_snapshot h
CROSS JOIN LATERAL jsonb_array_elements(h.payload->'candidates') candidate
CROSS JOIN LATERAL (
    SELECT encode(
        digest(
            convert_to(
                '[' || string_agg(item.value, ', ' ORDER BY item.value::bigint) || ']',
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    ) AS composition_sha256
    FROM jsonb_array_elements_text(candidate->'work_ids') item(value)
) matched
WHERE h.snapshot_id=e.snapshot_id AND candidate->>'candidate_id'=e.candidate_id;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM expert_review WHERE composition_sha256 IS NULL) THEN
        RAISE EXCEPTION 'cannot backfill expert review composition identity';
    END IF;
END;
$$;

ALTER TABLE expert_review
    ALTER COLUMN composition_sha256 SET NOT NULL,
    ADD CONSTRAINT expert_review_composition_sha256_length
        CHECK (length(composition_sha256)=64);
CREATE INDEX expert_review_composition_history_idx
    ON expert_review(composition_sha256,created_at,review_id);

CREATE OR REPLACE FUNCTION expert_review_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM hybrid_snapshot h
        CROSS JOIN LATERAL jsonb_array_elements(h.payload->'candidates') candidate
        CROSS JOIN LATERAL (
            SELECT encode(
                digest(
                    convert_to(
                        '[' || string_agg(item.value, ', ' ORDER BY item.value::bigint) || ']',
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            ) AS composition_sha256
            FROM jsonb_array_elements_text(candidate->'work_ids') item(value)
        ) matched
        WHERE h.snapshot_id=NEW.snapshot_id
          AND h.content_sha256=NEW.snapshot_content_sha256
          AND candidate->>'candidate_id'=NEW.candidate_id
          AND matched.composition_sha256=NEW.composition_sha256
    ) THEN
        RAISE EXCEPTION 'expert review must reference the exact frozen composition';
    END IF;
    RETURN NEW;
END;
$$;

ALTER TABLE score_candidate_review ADD COLUMN composition_sha256 TEXT;

UPDATE score_candidate_review review
SET composition_sha256 = matched.composition_sha256
FROM signal_candidate candidate
CROSS JOIN LATERAL (
    SELECT encode(
        digest(
            convert_to(
                '[' || string_agg(membership.work_id::text, ',' ORDER BY membership.work_id) || ']',
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    ) AS composition_sha256
    FROM topic_membership membership
    WHERE membership.topic_id=candidate.topic_id
) matched
WHERE candidate.candidate_id=review.candidate_id
  AND candidate.run_id=review.score_run_id;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM score_candidate_review WHERE composition_sha256 IS NULL) THEN
        RAISE EXCEPTION 'cannot backfill score review composition identity';
    END IF;
END;
$$;

ALTER TABLE score_candidate_review
    ALTER COLUMN composition_sha256 SET NOT NULL,
    ADD CONSTRAINT score_review_composition_sha256_length
        CHECK (length(composition_sha256)=64);
CREATE INDEX score_review_composition_history_idx
    ON score_candidate_review(composition_sha256,created_at,review_id);

CREATE OR REPLACE FUNCTION score_candidate_review_validate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM signal_candidate candidate
        JOIN analysis_run run ON run.run_id=candidate.run_id
        CROSS JOIN LATERAL (
            SELECT encode(
                digest(
                    convert_to(
                        '[' || string_agg(membership.work_id::text, ',' ORDER BY membership.work_id) || ']',
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            ) AS composition_sha256
            FROM topic_membership membership
            WHERE membership.topic_id=candidate.topic_id
        ) matched
        WHERE candidate.candidate_id=NEW.candidate_id
          AND candidate.run_id=NEW.score_run_id
          AND run.kind='score' AND run.status='done'
          AND matched.composition_sha256=NEW.composition_sha256
    ) THEN
        RAISE EXCEPTION 'review must reference the exact completed score composition';
    END IF;
    RETURN NEW;
END;
$$;

INSERT INTO schema_migrations (version) VALUES ('041_stable_review_composition');
