from saia.quality import decide


SPECS = [{"included_phrases": ["нейроморфные чипы"],
          "concept_groups": [["neuromorphic chips"], ["edge devices"]],
          "excluded_phrases": ["survey"],
          "matching_version": "orthographic-separators-v1"}]


def _decision(title: str, abstract: str):
    return decide(title=title, abstract=abstract, publication_year=2024,
                  date_is_imprecise=False, terms=["нейроморфные чипы"],
                  sources={"arxiv"}, openalex_payloads=[], arxiv_payloads=[],
                  as_of_date="2026-09-01", effective_date="2024-01-01",
                  compiled_branch_specs=SPECS)


def test_quality_accepts_only_full_compound_or_original_literal():
    relevant = _decision("Neuromorphic chips", "Deployed on edge devices")
    assert relevant.decision == "include"
    assert relevant.flags["compound_or_literal_plan_match"] is True
    assert set(relevant.matched_terms) == {"neuromorphic chips", "edge devices"}

    assert _decision("Neuromorphic chips", "Cloud only").decision == "exclude"
    assert _decision("Edge devices", "Conventional processors").decision == "exclude"
    assert _decision("Neuromorphic chips survey", "Edge devices").decision == "exclude"

    original = _decision("Нейроморфные чипы", "Новый результат")
    assert original.decision == "include"
