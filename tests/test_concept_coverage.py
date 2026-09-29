import pytest

from saia.compiled_phrase_matching import concept_coverage, matches_spec


def test_required_coverage_is_not_bypassed_by_retrieval_literal():
    paper = {"title": "Hybrid event-based vision sensors",
             "abstract": "A laboratory camera study without automotive deployment."}
    groups = [["event-based vision"], ["automotive production", "serial automotive"]]
    assert matches_spec(paper, {"included_phrases": ["event-based vision"],
                                "concept_groups": groups})
    coverage = concept_coverage(paper, groups)
    assert coverage["status"] == "incomplete_available_metadata"
    assert coverage["matched_groups"] == [1]
    assert coverage["unobserved_groups"] == [2]
    assert coverage["not_a_relevance_or_primary_result_judgement"]


def test_required_coverage_can_use_distinct_fields_and_missing_abstract_is_unknown():
    groups = [["UAV"], ["GPS-denied", "GNSS-denied"]]
    complete = concept_coverage({"title": "Autonomous UAV navigation",
                                 "abstract": "Experiments in GPS-denied environments."}, groups)
    assert complete["status"] == "all_groups_observed"
    assert complete["evidence"][0][0]["field"] == "title"
    assert complete["evidence"][1][0]["field"] == "abstract"
    incomplete = concept_coverage({"title": "Autonomous UAV navigation", "abstract": None}, groups)
    assert incomplete["status"] == "incomplete_available_metadata"
    assert not incomplete["abstract_available"]
    assert incomplete["unobserved_groups"] == [2]


def test_required_coverage_rejects_empty_or_malformed_groups():
    for groups in ([], [[]], [[""]]):
        with pytest.raises(ValueError):
            concept_coverage({"title": "A paper"}, groups)
