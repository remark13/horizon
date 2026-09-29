from scripts.probe_title_anchor_own_claim_rule import predict


def _case(title, abstract, phrase="task allocation"):
    return {"proposed_line_en": phrase,
            "paper": {"title": title, "abstract": abstract}}


def test_review_does_not_count_as_new_primary_result():
    result = predict(_case("A Survey of Task Allocation",
                           "We review many UAV methods."))
    assert result["own_result"] == "no"
    assert result["basis"] == "review_title"


def test_phrase_in_background_without_own_claim_does_not_become_positive():
    result = predict(_case("Drone systems",
                           "Task allocation is important. We propose a radio spectrum method."))
    assert result["own_result"] == "no"
    assert result["source_span_ids"] == ["A2"]


def test_phrase_in_own_claim_sentence_is_only_a_lexical_hint():
    result = predict(_case("New drone method",
                           "We propose task-allocation for UAVs."))
    assert result["own_result"] == "yes"
    assert result["basis"] == "own_claim_sentence_contains_phrase"
