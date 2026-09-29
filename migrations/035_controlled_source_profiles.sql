-- A source-profile version preserves the approved terms and cutoff while
-- explicitly narrowing execution to a source that can be collected fully.
-- It is neither a new semantic expansion nor a hidden manual rewrite.
ALTER TABLE query_version DROP CONSTRAINT query_version_expansion_source_check;
ALTER TABLE query_version ADD CONSTRAINT query_version_expansion_source_check
    CHECK (expansion_source IN (
        'manual', 'manual_frozen_parent_area', 'dictionary', 'llm', 'mixed',
        'controlled_source_profile'
    ));

INSERT INTO schema_migrations (version)
VALUES ('035_controlled_source_profiles');
