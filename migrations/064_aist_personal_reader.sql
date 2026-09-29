-- Add a scoped local RSS reader without rewriting existing observations.
ALTER TABLE external_evidence_observation
  DROP CONSTRAINT external_evidence_observation_source_check;
ALTER TABLE external_evidence_observation
  ADD CONSTRAINT external_evidence_observation_source_check CHECK (
    source IN ('gdelt_doc_2_0','epo_ops','nih_reporter','deps_dev','semantic_scholar',
               'ukri_gtr','eu_funding_tenders','huggingface_hub','clinicaltrials_gov',
               'europe_pmc','nasa_ntrs','usaspending','mit_news_rss','nasa_news_rss','jpl_news_rss',
               'nsf_awards','datacite','openaire_projects','osti_gov','nist_news_rss','aist_press_rss')
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
        (NEW.source IN ('gdelt_doc_2_0','mit_news_rss','nasa_news_rss','jpl_news_rss','nist_news_rss','aist_press_rss') AND NEW.role='news_attention_only') OR
        (NEW.source='epo_ops' AND NEW.role='patent_landscape_only') OR
        (NEW.source IN ('nih_reporter','ukri_gtr','nsf_awards','openaire_projects') AND NEW.role='research_funding_only') OR
        (NEW.source='deps_dev' AND NEW.role='software_diffusion_only') OR
        (NEW.source IN ('semantic_scholar','europe_pmc','nasa_ntrs','osti_gov') AND NEW.role='bibliographic_enrichment_only') OR
        (NEW.source='eu_funding_tenders' AND NEW.role='research_programme_only') OR
        (NEW.source='huggingface_hub' AND NEW.role='ai_artifact_diffusion_only') OR
        (NEW.source='clinicaltrials_gov' AND NEW.role='clinical_translation_only') OR
        (NEW.source='usaspending' AND NEW.role='public_procurement_only') OR
        (NEW.source='datacite' AND NEW.role='research_artifact_only')
    ) THEN
        RAISE EXCEPTION 'external evidence source and role do not match';
    END IF;
    IF NEW.source='osti_gov' AND (
        NEW.payload->>'model_input_allowed' IS DISTINCT FROM 'false'
        OR NEW.payload->>'model_training_allowed' IS DISTINCT FROM 'false'
        OR NEW.payload->>'bulk_reuse_approved' IS DISTINCT FROM 'false'
    ) THEN
        RAISE EXCEPTION 'OSTI bibliography is not approved for model input, training or bulk reuse';
    END IF;
    IF NEW.source='aist_press_rss' AND (
        NEW.payload->>'usage_scope' IS DISTINCT FROM 'personal_research'
        OR NEW.payload->'public_republication_allowed' IS DISTINCT FROM 'false'::jsonb
        OR NEW.payload->'model_input_allowed' IS DISTINCT FROM 'false'::jsonb
        OR NEW.payload->'model_training_allowed' IS DISTINCT FROM 'false'::jsonb
        OR NEW.payload->'bulk_reuse_approved' IS DISTINCT FROM 'false'::jsonb
        OR NEW.payload->'model_inputs_modified' IS DISTINCT FROM 'false'::jsonb
    ) THEN
        RAISE EXCEPTION 'AIST RSS is scoped to a personal local reader';
    END IF;
    RETURN NEW;
END;
$$;
INSERT INTO schema_migrations (version) VALUES ('064_aist_personal_reader');
