-- Add exactly the versioned publisher allowlist. Preserve observations and all guards.
ALTER TABLE external_evidence_observation
  DROP CONSTRAINT external_evidence_observation_source_check;
ALTER TABLE external_evidence_observation
  ADD CONSTRAINT external_evidence_observation_source_check CHECK (
    source IN ('gdelt_doc_2_0','epo_ops','nih_reporter','deps_dev','semantic_scholar',
      'ukri_gtr','eu_funding_tenders','huggingface_hub','clinicaltrials_gov','europe_pmc','nasa_ntrs',
      'usaspending','mit_news_rss','nasa_news_rss','jpl_news_rss','nsf_awards','datacite',
      'openaire_projects','osti_gov','nist_news_rss','aist_press_rss',
      'dealroom_marketmaps','dealroom_public_rounds','event_registry','mediacloud_news','lens_patents','google_news_rss',
      'publisher_news_phys_org','publisher_news_sciencedaily_com','publisher_news_eurekalert_org','publisher_news_sciencenews_org','publisher_news_scientificamerican_com','publisher_news_newscientist_com','publisher_news_nature_com','publisher_news_science_org','publisher_news_technologyreview_com','publisher_news_scitechdaily_com','publisher_news_livescience_com','publisher_news_technologynetworks_com','publisher_news_chemistryworld_com','publisher_news_chemistryviews_org','publisher_news_physicsworld_com','publisher_news_optics_org','publisher_news_spie_org','publisher_news_medicalxpress_com','publisher_news_news_medical_net','publisher_news_bioengineer_org','publisher_news_nanowerk_com','publisher_news_azonano_com','publisher_news_azom_com','publisher_news_azocleantech_com','publisher_news_spectrum_ieee_org','publisher_news_arstechnica_com','publisher_news_wired_com','publisher_news_techcrunch_com','publisher_news_venturebeat_com','publisher_news_theverge_com','publisher_news_zdnet_com','publisher_news_theregister_com','publisher_news_techxplore_com','publisher_news_therobotreport_com','publisher_news_roboticsbusinessreview_com','publisher_news_roboticsandautomationnews_com','publisher_news_automationworld_com','publisher_news_controleng_com','publisher_news_designnews_com','publisher_news_engineering_com','publisher_news_eetimes_com','publisher_news_semiengineering_com','publisher_news_semiconductor_digest_com','publisher_news_electronicsweekly_com','publisher_news_electronicdesign_com','publisher_news_photonics_com','publisher_news_manufacturingdive_com','publisher_news_3dprintingindustry_com','publisher_news_additivemanufacturing_media','publisher_news_3dprint_com','publisher_news_compositesworld_com','publisher_news_plasticsnews_com','publisher_news_chemicalprocessing_com','publisher_news_cen_acs_org','publisher_news_pv_magazine_com','publisher_news_renewableenergyworld_com','publisher_news_energy_storage_news','publisher_news_energyglobal_com','publisher_news_world_nuclear_news_org','publisher_news_nucnet_org','publisher_news_neimagazine_com','publisher_news_power_technology_com','publisher_news_offshore_energy_biz','publisher_news_hydrogeninsight_com','publisher_news_h2_view_com','publisher_news_renewablesnow_com','publisher_news_rechargenews_com','publisher_news_smart_energy_com','publisher_news_utilitydive_com','publisher_news_energycentral_com','publisher_news_batteriesnews_com','publisher_news_carbonherald_com','publisher_news_aviationweek_com','publisher_news_aerospacetestinginternational_com','publisher_news_flightglobal_com','publisher_news_aviationtoday_com','publisher_news_ainonline_com','publisher_news_spacenews_com','publisher_news_space_com','publisher_news_esa_int','publisher_news_spacewatch_global','publisher_news_commercialuavnews_com','publisher_news_suasnews_com','publisher_news_dronelife_com','publisher_news_unmannedsystemstechnology_com','publisher_news_marinelink_com','publisher_news_maritime_executive_com','publisher_news_automotiveworld_com','publisher_news_electrive_com','publisher_news_chargedevs_com','publisher_news_fiercebiotech_com','publisher_news_fiercepharma_com','publisher_news_biopharmadive_com','publisher_news_medtechdive_com','publisher_news_medtechintelligence_com','publisher_news_genengnews_com','publisher_news_statnews_com','publisher_news_pharmaceutical_technology_com','publisher_news_agfundernews_com','publisher_news_agri_tech_e_co_uk','publisher_news_foodnavigator_com','publisher_news_foodnavigator_asia_com','publisher_news_biopharma_reporter_com','publisher_news_feednavigator_com','publisher_news_asia_nikkei_com','publisher_news_techinasia_com','publisher_news_scmp_com','publisher_news_technode_com','publisher_news_kr_asia_com','publisher_news_koreaherald_com','publisher_news_koreatimes_co_kr','publisher_news_taipeitimes_com','publisher_news_digitimes_com','publisher_news_tass_ru','publisher_news_ria_ru','publisher_news_interfax_ru','publisher_news_rbc_ru','publisher_news_kommersant_ru','publisher_news_vedomosti_ru','publisher_news_cnews_ru','publisher_news_tadviser_ru','publisher_news_habr_com','publisher_news_indicator_ru','publisher_news_nplus1_ru','publisher_news_scientificrussia_ru','publisher_news_hightech_fm','publisher_news_atomic_energy_ru','publisher_news_nvidia_com','publisher_news_intel_com','publisher_news_amd_com','publisher_news_ibm_com','publisher_news_microsoft_com','publisher_news_siemens_com','publisher_news_abb_com','publisher_news_se_com','publisher_news_basf_com','publisher_news_dow_com','publisher_news_dupont_com','publisher_news_sabic_com','publisher_news_boeing_com','publisher_news_airbus_com','publisher_news_gevernova_com','publisher_news_global_toyota','publisher_news_hyundai_com','publisher_news_honda_com','publisher_news_qualcomm_com','publisher_news_tsmc_com','publisher_news_asml_com','publisher_news_samsung_com','publisher_news_huawei_com','publisher_news_dji_com','publisher_news_bostondynamics_com','publisher_news_agilityrobotics_com','publisher_news_figure_ai','publisher_news_fraunhofer_de','publisher_news_mpg_de','publisher_news_helmholtz_de','publisher_news_cea_fr','publisher_news_csiro_au','publisher_news_kaist_ac_kr','publisher_news_cern_ch','publisher_news_anl_gov','publisher_news_ornl_gov','publisher_news_pnnl_gov','publisher_news_lanl_gov','publisher_news_inl_gov','publisher_news_llnl_gov','publisher_news_sandia_gov','publisher_news_bnl_gov'));
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
        (NEW.source IN ('gdelt_doc_2_0','mit_news_rss','nasa_news_rss','jpl_news_rss','nist_news_rss','aist_press_rss','event_registry','mediacloud_news','google_news_rss',
          'publisher_news_phys_org','publisher_news_sciencedaily_com','publisher_news_eurekalert_org','publisher_news_sciencenews_org','publisher_news_scientificamerican_com','publisher_news_newscientist_com','publisher_news_nature_com','publisher_news_science_org','publisher_news_technologyreview_com','publisher_news_scitechdaily_com','publisher_news_livescience_com','publisher_news_technologynetworks_com','publisher_news_chemistryworld_com','publisher_news_chemistryviews_org','publisher_news_physicsworld_com','publisher_news_optics_org','publisher_news_spie_org','publisher_news_medicalxpress_com','publisher_news_news_medical_net','publisher_news_bioengineer_org','publisher_news_nanowerk_com','publisher_news_azonano_com','publisher_news_azom_com','publisher_news_azocleantech_com','publisher_news_spectrum_ieee_org','publisher_news_arstechnica_com','publisher_news_wired_com','publisher_news_techcrunch_com','publisher_news_venturebeat_com','publisher_news_theverge_com','publisher_news_zdnet_com','publisher_news_theregister_com','publisher_news_techxplore_com','publisher_news_therobotreport_com','publisher_news_roboticsbusinessreview_com','publisher_news_roboticsandautomationnews_com','publisher_news_automationworld_com','publisher_news_controleng_com','publisher_news_designnews_com','publisher_news_engineering_com','publisher_news_eetimes_com','publisher_news_semiengineering_com','publisher_news_semiconductor_digest_com','publisher_news_electronicsweekly_com','publisher_news_electronicdesign_com','publisher_news_photonics_com','publisher_news_manufacturingdive_com','publisher_news_3dprintingindustry_com','publisher_news_additivemanufacturing_media','publisher_news_3dprint_com','publisher_news_compositesworld_com','publisher_news_plasticsnews_com','publisher_news_chemicalprocessing_com','publisher_news_cen_acs_org','publisher_news_pv_magazine_com','publisher_news_renewableenergyworld_com','publisher_news_energy_storage_news','publisher_news_energyglobal_com','publisher_news_world_nuclear_news_org','publisher_news_nucnet_org','publisher_news_neimagazine_com','publisher_news_power_technology_com','publisher_news_offshore_energy_biz','publisher_news_hydrogeninsight_com','publisher_news_h2_view_com','publisher_news_renewablesnow_com','publisher_news_rechargenews_com','publisher_news_smart_energy_com','publisher_news_utilitydive_com','publisher_news_energycentral_com','publisher_news_batteriesnews_com','publisher_news_carbonherald_com','publisher_news_aviationweek_com','publisher_news_aerospacetestinginternational_com','publisher_news_flightglobal_com','publisher_news_aviationtoday_com','publisher_news_ainonline_com','publisher_news_spacenews_com','publisher_news_space_com','publisher_news_esa_int','publisher_news_spacewatch_global','publisher_news_commercialuavnews_com','publisher_news_suasnews_com','publisher_news_dronelife_com','publisher_news_unmannedsystemstechnology_com','publisher_news_marinelink_com','publisher_news_maritime_executive_com','publisher_news_automotiveworld_com','publisher_news_electrive_com','publisher_news_chargedevs_com','publisher_news_fiercebiotech_com','publisher_news_fiercepharma_com','publisher_news_biopharmadive_com','publisher_news_medtechdive_com','publisher_news_medtechintelligence_com','publisher_news_genengnews_com','publisher_news_statnews_com','publisher_news_pharmaceutical_technology_com','publisher_news_agfundernews_com','publisher_news_agri_tech_e_co_uk','publisher_news_foodnavigator_com','publisher_news_foodnavigator_asia_com','publisher_news_biopharma_reporter_com','publisher_news_feednavigator_com','publisher_news_asia_nikkei_com','publisher_news_techinasia_com','publisher_news_scmp_com','publisher_news_technode_com','publisher_news_kr_asia_com','publisher_news_koreaherald_com','publisher_news_koreatimes_co_kr','publisher_news_taipeitimes_com','publisher_news_digitimes_com','publisher_news_tass_ru','publisher_news_ria_ru','publisher_news_interfax_ru','publisher_news_rbc_ru','publisher_news_kommersant_ru','publisher_news_vedomosti_ru','publisher_news_cnews_ru','publisher_news_tadviser_ru','publisher_news_habr_com','publisher_news_indicator_ru','publisher_news_nplus1_ru','publisher_news_scientificrussia_ru','publisher_news_hightech_fm','publisher_news_atomic_energy_ru','publisher_news_nvidia_com','publisher_news_intel_com','publisher_news_amd_com','publisher_news_ibm_com','publisher_news_microsoft_com','publisher_news_siemens_com','publisher_news_abb_com','publisher_news_se_com','publisher_news_basf_com','publisher_news_dow_com','publisher_news_dupont_com','publisher_news_sabic_com','publisher_news_boeing_com','publisher_news_airbus_com','publisher_news_gevernova_com','publisher_news_global_toyota','publisher_news_hyundai_com','publisher_news_honda_com','publisher_news_qualcomm_com','publisher_news_tsmc_com','publisher_news_asml_com','publisher_news_samsung_com','publisher_news_huawei_com','publisher_news_dji_com','publisher_news_bostondynamics_com','publisher_news_agilityrobotics_com','publisher_news_figure_ai','publisher_news_fraunhofer_de','publisher_news_mpg_de','publisher_news_helmholtz_de','publisher_news_cea_fr','publisher_news_csiro_au','publisher_news_kaist_ac_kr','publisher_news_cern_ch','publisher_news_anl_gov','publisher_news_ornl_gov','publisher_news_pnnl_gov','publisher_news_lanl_gov','publisher_news_inl_gov','publisher_news_llnl_gov','publisher_news_sandia_gov','publisher_news_bnl_gov') AND NEW.role='news_attention_only') OR
        (NEW.source IN ('epo_ops','lens_patents') AND NEW.role='patent_landscape_only') OR
        (NEW.source IN ('nih_reporter','ukri_gtr','nsf_awards','openaire_projects') AND NEW.role='research_funding_only') OR
        (NEW.source='deps_dev' AND NEW.role='software_diffusion_only') OR
        (NEW.source IN ('semantic_scholar','europe_pmc','nasa_ntrs','osti_gov') AND NEW.role='bibliographic_enrichment_only') OR
        (NEW.source='eu_funding_tenders' AND NEW.role='research_programme_only') OR
        (NEW.source='huggingface_hub' AND NEW.role='ai_artifact_diffusion_only') OR
        (NEW.source='clinicaltrials_gov' AND NEW.role='clinical_translation_only') OR
        (NEW.source='usaspending' AND NEW.role='public_procurement_only') OR
        (NEW.source='datacite' AND NEW.role='research_artifact_only') OR
        (NEW.source='dealroom_marketmaps' AND NEW.role='company_landscape_only') OR
        (NEW.source='dealroom_public_rounds' AND NEW.role='investment_lead_only')
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
    IF left(NEW.source,15)='publisher_news_' AND (
        NEW.payload->>'publisher_filter_domain' IS DISTINCT FROM (CASE NEW.source
            WHEN 'publisher_news_phys_org' THEN 'phys.org'
            WHEN 'publisher_news_sciencedaily_com' THEN 'sciencedaily.com'
            WHEN 'publisher_news_eurekalert_org' THEN 'eurekalert.org'
            WHEN 'publisher_news_sciencenews_org' THEN 'sciencenews.org'
            WHEN 'publisher_news_scientificamerican_com' THEN 'scientificamerican.com'
            WHEN 'publisher_news_newscientist_com' THEN 'newscientist.com'
            WHEN 'publisher_news_nature_com' THEN 'nature.com'
            WHEN 'publisher_news_science_org' THEN 'science.org'
            WHEN 'publisher_news_technologyreview_com' THEN 'technologyreview.com'
            WHEN 'publisher_news_scitechdaily_com' THEN 'scitechdaily.com'
            WHEN 'publisher_news_livescience_com' THEN 'livescience.com'
            WHEN 'publisher_news_technologynetworks_com' THEN 'technologynetworks.com'
            WHEN 'publisher_news_chemistryworld_com' THEN 'chemistryworld.com'
            WHEN 'publisher_news_chemistryviews_org' THEN 'chemistryviews.org'
            WHEN 'publisher_news_physicsworld_com' THEN 'physicsworld.com'
            WHEN 'publisher_news_optics_org' THEN 'optics.org'
            WHEN 'publisher_news_spie_org' THEN 'spie.org'
            WHEN 'publisher_news_medicalxpress_com' THEN 'medicalxpress.com'
            WHEN 'publisher_news_news_medical_net' THEN 'news-medical.net'
            WHEN 'publisher_news_bioengineer_org' THEN 'bioengineer.org'
            WHEN 'publisher_news_nanowerk_com' THEN 'nanowerk.com'
            WHEN 'publisher_news_azonano_com' THEN 'azonano.com'
            WHEN 'publisher_news_azom_com' THEN 'azom.com'
            WHEN 'publisher_news_azocleantech_com' THEN 'azocleantech.com'
            WHEN 'publisher_news_spectrum_ieee_org' THEN 'spectrum.ieee.org'
            WHEN 'publisher_news_arstechnica_com' THEN 'arstechnica.com'
            WHEN 'publisher_news_wired_com' THEN 'wired.com'
            WHEN 'publisher_news_techcrunch_com' THEN 'techcrunch.com'
            WHEN 'publisher_news_venturebeat_com' THEN 'venturebeat.com'
            WHEN 'publisher_news_theverge_com' THEN 'theverge.com'
            WHEN 'publisher_news_zdnet_com' THEN 'zdnet.com'
            WHEN 'publisher_news_theregister_com' THEN 'theregister.com'
            WHEN 'publisher_news_techxplore_com' THEN 'techxplore.com'
            WHEN 'publisher_news_therobotreport_com' THEN 'therobotreport.com'
            WHEN 'publisher_news_roboticsbusinessreview_com' THEN 'roboticsbusinessreview.com'
            WHEN 'publisher_news_roboticsandautomationnews_com' THEN 'roboticsandautomationnews.com'
            WHEN 'publisher_news_automationworld_com' THEN 'automationworld.com'
            WHEN 'publisher_news_controleng_com' THEN 'controleng.com'
            WHEN 'publisher_news_designnews_com' THEN 'designnews.com'
            WHEN 'publisher_news_engineering_com' THEN 'engineering.com'
            WHEN 'publisher_news_eetimes_com' THEN 'eetimes.com'
            WHEN 'publisher_news_semiengineering_com' THEN 'semiengineering.com'
            WHEN 'publisher_news_semiconductor_digest_com' THEN 'semiconductor-digest.com'
            WHEN 'publisher_news_electronicsweekly_com' THEN 'electronicsweekly.com'
            WHEN 'publisher_news_electronicdesign_com' THEN 'electronicdesign.com'
            WHEN 'publisher_news_photonics_com' THEN 'photonics.com'
            WHEN 'publisher_news_manufacturingdive_com' THEN 'manufacturingdive.com'
            WHEN 'publisher_news_3dprintingindustry_com' THEN '3dprintingindustry.com'
            WHEN 'publisher_news_additivemanufacturing_media' THEN 'additivemanufacturing.media'
            WHEN 'publisher_news_3dprint_com' THEN '3dprint.com'
            WHEN 'publisher_news_compositesworld_com' THEN 'compositesworld.com'
            WHEN 'publisher_news_plasticsnews_com' THEN 'plasticsnews.com'
            WHEN 'publisher_news_chemicalprocessing_com' THEN 'chemicalprocessing.com'
            WHEN 'publisher_news_cen_acs_org' THEN 'cen.acs.org'
            WHEN 'publisher_news_pv_magazine_com' THEN 'pv-magazine.com'
            WHEN 'publisher_news_renewableenergyworld_com' THEN 'renewableenergyworld.com'
            WHEN 'publisher_news_energy_storage_news' THEN 'energy-storage.news'
            WHEN 'publisher_news_energyglobal_com' THEN 'energyglobal.com'
            WHEN 'publisher_news_world_nuclear_news_org' THEN 'world-nuclear-news.org'
            WHEN 'publisher_news_nucnet_org' THEN 'nucnet.org'
            WHEN 'publisher_news_neimagazine_com' THEN 'neimagazine.com'
            WHEN 'publisher_news_power_technology_com' THEN 'power-technology.com'
            WHEN 'publisher_news_offshore_energy_biz' THEN 'offshore-energy.biz'
            WHEN 'publisher_news_hydrogeninsight_com' THEN 'hydrogeninsight.com'
            WHEN 'publisher_news_h2_view_com' THEN 'h2-view.com'
            WHEN 'publisher_news_renewablesnow_com' THEN 'renewablesnow.com'
            WHEN 'publisher_news_rechargenews_com' THEN 'rechargenews.com'
            WHEN 'publisher_news_smart_energy_com' THEN 'smart-energy.com'
            WHEN 'publisher_news_utilitydive_com' THEN 'utilitydive.com'
            WHEN 'publisher_news_energycentral_com' THEN 'energycentral.com'
            WHEN 'publisher_news_batteriesnews_com' THEN 'batteriesnews.com'
            WHEN 'publisher_news_carbonherald_com' THEN 'carbonherald.com'
            WHEN 'publisher_news_aviationweek_com' THEN 'aviationweek.com'
            WHEN 'publisher_news_aerospacetestinginternational_com' THEN 'aerospacetestinginternational.com'
            WHEN 'publisher_news_flightglobal_com' THEN 'flightglobal.com'
            WHEN 'publisher_news_aviationtoday_com' THEN 'aviationtoday.com'
            WHEN 'publisher_news_ainonline_com' THEN 'ainonline.com'
            WHEN 'publisher_news_spacenews_com' THEN 'spacenews.com'
            WHEN 'publisher_news_space_com' THEN 'space.com'
            WHEN 'publisher_news_esa_int' THEN 'esa.int'
            WHEN 'publisher_news_spacewatch_global' THEN 'spacewatch.global'
            WHEN 'publisher_news_commercialuavnews_com' THEN 'commercialuavnews.com'
            WHEN 'publisher_news_suasnews_com' THEN 'suasnews.com'
            WHEN 'publisher_news_dronelife_com' THEN 'dronelife.com'
            WHEN 'publisher_news_unmannedsystemstechnology_com' THEN 'unmannedsystemstechnology.com'
            WHEN 'publisher_news_marinelink_com' THEN 'marinelink.com'
            WHEN 'publisher_news_maritime_executive_com' THEN 'maritime-executive.com'
            WHEN 'publisher_news_automotiveworld_com' THEN 'automotiveworld.com'
            WHEN 'publisher_news_electrive_com' THEN 'electrive.com'
            WHEN 'publisher_news_chargedevs_com' THEN 'chargedevs.com'
            WHEN 'publisher_news_fiercebiotech_com' THEN 'fiercebiotech.com'
            WHEN 'publisher_news_fiercepharma_com' THEN 'fiercepharma.com'
            WHEN 'publisher_news_biopharmadive_com' THEN 'biopharmadive.com'
            WHEN 'publisher_news_medtechdive_com' THEN 'medtechdive.com'
            WHEN 'publisher_news_medtechintelligence_com' THEN 'medtechintelligence.com'
            WHEN 'publisher_news_genengnews_com' THEN 'genengnews.com'
            WHEN 'publisher_news_statnews_com' THEN 'statnews.com'
            WHEN 'publisher_news_pharmaceutical_technology_com' THEN 'pharmaceutical-technology.com'
            WHEN 'publisher_news_agfundernews_com' THEN 'agfundernews.com'
            WHEN 'publisher_news_agri_tech_e_co_uk' THEN 'agri-tech-e.co.uk'
            WHEN 'publisher_news_foodnavigator_com' THEN 'foodnavigator.com'
            WHEN 'publisher_news_foodnavigator_asia_com' THEN 'foodnavigator-asia.com'
            WHEN 'publisher_news_biopharma_reporter_com' THEN 'biopharma-reporter.com'
            WHEN 'publisher_news_feednavigator_com' THEN 'feednavigator.com'
            WHEN 'publisher_news_asia_nikkei_com' THEN 'asia.nikkei.com'
            WHEN 'publisher_news_techinasia_com' THEN 'techinasia.com'
            WHEN 'publisher_news_scmp_com' THEN 'scmp.com'
            WHEN 'publisher_news_technode_com' THEN 'technode.com'
            WHEN 'publisher_news_kr_asia_com' THEN 'kr-asia.com'
            WHEN 'publisher_news_koreaherald_com' THEN 'koreaherald.com'
            WHEN 'publisher_news_koreatimes_co_kr' THEN 'koreatimes.co.kr'
            WHEN 'publisher_news_taipeitimes_com' THEN 'taipeitimes.com'
            WHEN 'publisher_news_digitimes_com' THEN 'digitimes.com'
            WHEN 'publisher_news_tass_ru' THEN 'tass.ru'
            WHEN 'publisher_news_ria_ru' THEN 'ria.ru'
            WHEN 'publisher_news_interfax_ru' THEN 'interfax.ru'
            WHEN 'publisher_news_rbc_ru' THEN 'rbc.ru'
            WHEN 'publisher_news_kommersant_ru' THEN 'kommersant.ru'
            WHEN 'publisher_news_vedomosti_ru' THEN 'vedomosti.ru'
            WHEN 'publisher_news_cnews_ru' THEN 'cnews.ru'
            WHEN 'publisher_news_tadviser_ru' THEN 'tadviser.ru'
            WHEN 'publisher_news_habr_com' THEN 'habr.com'
            WHEN 'publisher_news_indicator_ru' THEN 'indicator.ru'
            WHEN 'publisher_news_nplus1_ru' THEN 'nplus1.ru'
            WHEN 'publisher_news_scientificrussia_ru' THEN 'scientificrussia.ru'
            WHEN 'publisher_news_hightech_fm' THEN 'hightech.fm'
            WHEN 'publisher_news_atomic_energy_ru' THEN 'atomic-energy.ru'
            WHEN 'publisher_news_nvidia_com' THEN 'nvidia.com'
            WHEN 'publisher_news_intel_com' THEN 'intel.com'
            WHEN 'publisher_news_amd_com' THEN 'amd.com'
            WHEN 'publisher_news_ibm_com' THEN 'ibm.com'
            WHEN 'publisher_news_microsoft_com' THEN 'microsoft.com'
            WHEN 'publisher_news_siemens_com' THEN 'siemens.com'
            WHEN 'publisher_news_abb_com' THEN 'abb.com'
            WHEN 'publisher_news_se_com' THEN 'se.com'
            WHEN 'publisher_news_basf_com' THEN 'basf.com'
            WHEN 'publisher_news_dow_com' THEN 'dow.com'
            WHEN 'publisher_news_dupont_com' THEN 'dupont.com'
            WHEN 'publisher_news_sabic_com' THEN 'sabic.com'
            WHEN 'publisher_news_boeing_com' THEN 'boeing.com'
            WHEN 'publisher_news_airbus_com' THEN 'airbus.com'
            WHEN 'publisher_news_gevernova_com' THEN 'gevernova.com'
            WHEN 'publisher_news_global_toyota' THEN 'global.toyota'
            WHEN 'publisher_news_hyundai_com' THEN 'hyundai.com'
            WHEN 'publisher_news_honda_com' THEN 'honda.com'
            WHEN 'publisher_news_qualcomm_com' THEN 'qualcomm.com'
            WHEN 'publisher_news_tsmc_com' THEN 'tsmc.com'
            WHEN 'publisher_news_asml_com' THEN 'asml.com'
            WHEN 'publisher_news_samsung_com' THEN 'samsung.com'
            WHEN 'publisher_news_huawei_com' THEN 'huawei.com'
            WHEN 'publisher_news_dji_com' THEN 'dji.com'
            WHEN 'publisher_news_bostondynamics_com' THEN 'bostondynamics.com'
            WHEN 'publisher_news_agilityrobotics_com' THEN 'agilityrobotics.com'
            WHEN 'publisher_news_figure_ai' THEN 'figure.ai'
            WHEN 'publisher_news_fraunhofer_de' THEN 'fraunhofer.de'
            WHEN 'publisher_news_mpg_de' THEN 'mpg.de'
            WHEN 'publisher_news_helmholtz_de' THEN 'helmholtz.de'
            WHEN 'publisher_news_cea_fr' THEN 'cea.fr'
            WHEN 'publisher_news_csiro_au' THEN 'csiro.au'
            WHEN 'publisher_news_kaist_ac_kr' THEN 'kaist.ac.kr'
            WHEN 'publisher_news_cern_ch' THEN 'cern.ch'
            WHEN 'publisher_news_anl_gov' THEN 'anl.gov'
            WHEN 'publisher_news_ornl_gov' THEN 'ornl.gov'
            WHEN 'publisher_news_pnnl_gov' THEN 'pnnl.gov'
            WHEN 'publisher_news_lanl_gov' THEN 'lanl.gov'
            WHEN 'publisher_news_inl_gov' THEN 'inl.gov'
            WHEN 'publisher_news_llnl_gov' THEN 'llnl.gov'
            WHEN 'publisher_news_sandia_gov' THEN 'sandia.gov'
            WHEN 'publisher_news_bnl_gov' THEN 'bnl.gov'
        END)
        OR NEW.payload->>'publisher_backend' IS DISTINCT FROM 'google_news_rss'
        OR NEW.payload->>'publisher_directory_version' IS DISTINCT FROM 'news-publisher-directory-v1'
        OR NEW.payload->>'usage_scope' IS DISTINCT FROM 'personal_research'
        OR NEW.payload->'public_republication_allowed' IS DISTINCT FROM 'false'::jsonb
        OR NEW.payload->'model_input_allowed' IS DISTINCT FROM 'false'::jsonb
        OR NEW.payload->'model_training_allowed' IS DISTINCT FROM 'false'::jsonb
        OR NEW.payload->'bulk_reuse_approved' IS DISTINCT FROM 'false'::jsonb
        OR NEW.payload->'stores_full_text' IS DISTINCT FROM 'false'::jsonb
        OR NEW.payload->'stores_images' IS DISTINCT FROM 'false'::jsonb
    ) THEN
        RAISE EXCEPTION 'Publisher channel is a fixed-domain personal metadata reader';
    END IF;
    RETURN NEW;
END;
$$;
INSERT INTO schema_migrations (version) VALUES ('067_news_publisher_channels');
