-- New keyless metadata channels keep the old source/role and append-only guards.
ALTER TABLE external_evidence_observation
  DROP CONSTRAINT external_evidence_observation_source_check,
  DROP CONSTRAINT external_evidence_observation_role_check;
ALTER TABLE external_evidence_observation
  ADD CONSTRAINT external_evidence_observation_source_check CHECK (
    source IN ('gdelt_doc_2_0','epo_ops','nih_reporter','deps_dev','semantic_scholar',
               'ukri_gtr','eu_funding_tenders','huggingface_hub','clinicaltrials_gov',
               'europe_pmc','nasa_ntrs','usaspending','mit_news_rss','nasa_news_rss','jpl_news_rss',
               'nsf_awards','datacite','openaire_projects')
  ),
  ADD CONSTRAINT external_evidence_observation_role_check CHECK (
    role IN ('news_attention_only','patent_landscape_only','research_funding_only',
             'software_diffusion_only','bibliographic_enrichment_only','research_programme_only',
             'ai_artifact_diffusion_only','clinical_translation_only','public_procurement_only',
             'research_artifact_only')
  );
CREATE OR REPLACE FUNCTION external_evidence_observation_validate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.payload->>'source' IS DISTINCT FROM NEW.source
       OR NEW.payload->>'role' IS DISTINCT FROM NEW.role
       OR NEW.payload->>'topic_id' IS DISTINCT FROM NEW.topic_id
       OR NEW.payload->>'status' IS DISTINCT FROM NEW.status
       OR NEW.payload->>'report_payload_sha256' IS DISTINCT FROM NEW.report_payload_sha256
       OR NEW.payload->>'scientific_score_modified' IS DISTINCT FROM 'false'
       OR NEW.payload->>'missing_is_zero' IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'external evidence columns and guarded payload must agree';
    END IF;
    IF NOT (
        (NEW.source IN ('gdelt_doc_2_0','mit_news_rss','nasa_news_rss','jpl_news_rss') AND NEW.role='news_attention_only') OR
        (NEW.source='epo_ops' AND NEW.role='patent_landscape_only') OR
        (NEW.source IN ('nih_reporter','ukri_gtr','nsf_awards','openaire_projects') AND NEW.role='research_funding_only') OR
        (NEW.source='deps_dev' AND NEW.role='software_diffusion_only') OR
        (NEW.source IN ('semantic_scholar','europe_pmc','nasa_ntrs') AND NEW.role='bibliographic_enrichment_only') OR
        (NEW.source='eu_funding_tenders' AND NEW.role='research_programme_only') OR
        (NEW.source='huggingface_hub' AND NEW.role='ai_artifact_diffusion_only') OR
        (NEW.source='clinicaltrials_gov' AND NEW.role='clinical_translation_only') OR
        (NEW.source='usaspending' AND NEW.role='public_procurement_only') OR
        (NEW.source='datacite' AND NEW.role='research_artifact_only')
    ) THEN
        RAISE EXCEPTION 'external evidence source and role do not match';
    END IF;
    RETURN NEW;
END;
$$;
INSERT INTO schema_migrations (version) VALUES ('062_public_research_metadata_sources');
