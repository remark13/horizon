-- Preserve the more specific provenance used by frozen parent-area missions.
-- It is still manual expansion, but collapsing it to ``manual`` at ingestion
-- would make the database disagree with the immutable mission snapshot.
ALTER TABLE query_version DROP CONSTRAINT query_version_expansion_source_check;
ALTER TABLE query_version ADD CONSTRAINT query_version_expansion_source_check
    CHECK (expansion_source IN (
        'manual', 'manual_frozen_parent_area', 'dictionary', 'llm', 'mixed'
    ));

INSERT INTO schema_migrations (version)
VALUES ('030_frozen_parent_expansion_source');
