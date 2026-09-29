from saia.catalog_recovery import summarize_case


def test_summarize_case_separates_collection_quality_and_topic_recovery():
    case = {"case_id": "dev", "family_id": "x", "title": "X",
            "kind": "research_line_positive_proposal",
            "arxiv_ids": ["1601.00001", "1601.00002", "1601.00003"]}
    rows = [
        {"arxiv_id": "1601.00001", "found_in_corpus": True,
         "quality_decision": "include", "topic_id": 7,
         "topic_label": "line", "candidate_status": "watch"},
        {"arxiv_id": "1601.00002", "found_in_corpus": True,
         "quality_decision": "include", "topic_id": 7,
         "topic_label": "line", "candidate_status": "watch"},
        {"arxiv_id": "1601.00003", "found_in_corpus": True,
         "quality_decision": "quarantine", "topic_id": None},
    ]
    result = summarize_case(case, rows, {7: 10})
    assert result["collection_recall"] == 1.0
    assert result["quality_survival"] == 0.6667
    assert result["topic_assignment_recall"] == 0.6667
    assert result["max_in_one_topic"] == 2
    assert result["dominant_target_share"] == 0.2
    assert result["multi_reference_topic_recovered"] is True
