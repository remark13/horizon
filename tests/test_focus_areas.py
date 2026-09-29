from saia.focus_areas import EXPECTED_IDS, catalog, evaluation_matrix, profile


def test_focus_area_catalog_contains_nine_controlled_profiles():
    payload = catalog()
    assert payload["version"] == "focus-areas-0.4.32"
    assert payload["count"] == 9
    assert {item["id"] for item in payload["profiles"]} == EXPECTED_IDS
    assert payload["policy"]["profile_is_gold_label"] is False
    assert payload["policy"]["ui_exposed_as_fixed_choices"] is False
    assert payload["policy"]["user_query_may_be_narrowed_automatically"] is False
    assert payload["policy"]["manual_query_approval_required"] is True
    assert payload["policy"]["external_evidence_modifies_scientific_score"] is False


def test_each_focus_area_is_a_nonempty_search_profile():
    for item in catalog()["profiles"]:
        assert len(item["subareas"]) >= 5
        assert item["starter_query_en"]
        assert item["aliases_ru"] and item["aliases_en"]
        assert item["enrichment_sources"]
        assert len({subarea["id"] for subarea in item["subareas"]}) == len(item["subareas"])
        assert all(subarea["query_en"] for subarea in item["subareas"])


def test_ai_is_both_primary_and_cross_cutting():
    assert profile("artificial-intelligence")["role"] == "primary_and_cross_cutting"


def test_unknown_focus_area_is_rejected():
    try:
        profile("unknown")
    except ValueError as error:
        assert str(error) == "Unknown focus-area profile"
    else:
        raise AssertionError("Unknown focus area must not be accepted")


def test_internal_evaluation_matrix_covers_every_profile_without_fake_gold():
    cases = evaluation_matrix()
    assert len(cases) == 81
    assert len({case["case_id"] for case in cases}) == 81
    assert {case["profile_id"] for case in cases} == EXPECTED_IDS
    assert all(case["ground_truth_status"] == "unlabelled" for case in cases)
    assert all(case["manual_review_required"] is True for case in cases)
