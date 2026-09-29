from saia.external_sources import load_policy


def test_external_source_policy_keeps_new_channels_out_of_scientific_score():
    policy = load_policy()
    assert policy["scientific_score_modified"] is False
    assert policy["news"]["source"] == "gdelt_doc_2_0"
    assert policy["patents"]["source"] == "epo_ops"
    assert policy["funding"]["source"] == "nih_reporter"
    assert policy["software"]["source"] == "deps_dev"
    assert policy["scholar"]["source"] == "semantic_scholar"
    assert policy["uk_funding"]["source"] == "ukri_gtr"
    assert policy["eu_programmes"]["source"] == "eu_funding_tenders"
    assert policy["ai_artifacts"]["source"] == "huggingface_hub"
    assert policy["clinical_trials"]["source"] == "clinicaltrials_gov"
    assert policy["europe_pmc"]["source"] == "europe_pmc"
    assert policy["nasa_ntrs"]["source"] == "nasa_ntrs"
    assert policy["news"]["max_records"] <= 50
    assert policy["patents"]["max_records"] <= 25
    assert policy["funding"]["max_records"] <= 50
    assert policy["software"]["max_versions"] <= 100
    assert policy["scholar"]["max_records"] <= 50
    assert policy["uk_funding"]["max_records"] <= 50
    assert policy["eu_programmes"]["max_records"] <= 20
    assert policy["ai_artifacts"]["max_records"] <= 20
    assert policy["clinical_trials"]["max_records"] <= 50
    assert policy["europe_pmc"]["max_records"] <= 50
    assert policy["nasa_ntrs"]["max_records"] <= 50
