from scripts.propose_bas_contribution_phrases import propose, sentence_phrases


def _cfg():
    return {"version": "bas-contribution-phrase-proposals-v1",
            "production_use": False, "date_from": "2016-09-01",
            "as_of_date_exclusive": "2026-09-01",
            "prior_from": "2021-09-01", "recent_from": "2024-09-01",
            "phrase_lengths": [2, 3], "min_distinct_works": 2,
            "min_recent_works": 1, "max_parent_share": 1.0,
            "top_limit": 10, "max_unique_phrases": 1000,
            "exclude_review_hints": True,
            "extra_stopwords": ["we", "propose", "a"],
            "limitations": []}


def test_proposal_uses_claim_sentence_not_background_title():
    works = {"1": "2022-01-01", "2": "2025-01-01", "3": "2025-02-01"}
    claims = [{"claim_id": "1:A1", "arxiv_id": "1",
               "text": "We propose visual-inertial odometry for UAVs.",
               "format_hint": "unclassified"},
              {"claim_id": "2:A2", "arxiv_id": "2",
               "text": "We propose visual-inertial odometry for aerial robots.",
               "format_hint": "unclassified"},
              {"claim_id": "3:A1", "arxiv_id": "3",
               "text": "We propose wireless spectrum sharing.",
               "format_hint": "review_or_synthesis"}]
    result = propose(works, claims, _cfg())
    by_phrase = {row["phrase_en"]: row for row in result["rows"]}
    assert by_phrase["visual inertial"]["distinct_parent_works"] == 2
    assert by_phrase["visual inertial"]["recent_works"] == 1
    assert "wireless spectrum" not in by_phrase
    assert result["review_hint_claim_sentences_excluded"] == 1
    assert result["weak_signal_accuracy_measured"] is False


def test_phrase_extraction_drops_discourse_and_generic_only_phrases():
    phrases = sentence_phrases("We propose novel UAV drone visual-inertial odometry",
                               stop={"we", "propose", "novel"},
                               generic={"uav", "drone"}, lengths=[2, 3])
    assert "visual inertial" in phrases
    assert "uav drone" not in phrases
