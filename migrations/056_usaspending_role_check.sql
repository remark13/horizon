-- Complete the source/role registration after the append-only source expansion.

ALTER TABLE external_evidence_observation
    DROP CONSTRAINT external_evidence_observation_role_check;

ALTER TABLE external_evidence_observation
    ADD CONSTRAINT external_evidence_observation_role_check CHECK (
        role IN ('news_attention_only','patent_landscape_only','research_funding_only',
                 'software_diffusion_only','bibliographic_enrichment_only',
                 'research_programme_only','ai_artifact_diffusion_only',
                 'clinical_translation_only','public_procurement_only')
    );

INSERT INTO schema_migrations (version) VALUES ('056_usaspending_role_check');
