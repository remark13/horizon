-- A development retrieval profile records a mechanically selected expansion
-- used for evaluation.  It must remain distinguishable from a user-approved
-- controlled source profile and from an LLM-generated query.
ALTER TABLE query_version DROP CONSTRAINT query_version_expansion_source_check;
ALTER TABLE query_version ADD CONSTRAINT query_version_expansion_source_check
    CHECK (expansion_source IN (
        'manual', 'manual_frozen_parent_area', 'dictionary', 'llm', 'mixed',
        'controlled_source_profile', 'development_retrieval_experiment'
    ));

INSERT INTO schema_migrations (version)
VALUES ('037_development_retrieval_profiles');
