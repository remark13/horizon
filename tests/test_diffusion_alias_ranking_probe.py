from copy import deepcopy

import pytest

from scripts.probe_diffusion_alias_ranking import evaluate_family, verify_source_spans


def test_control_recovery_requires_technical_name_and_does_not_claim_precision():
    documents = [{"id": "a", "identifiers": [{"kind": "arxiv", "value": "old.0001"}]}]
    family = {"id": "example", "control_arxiv_ids": ["old.0001", "old.0002"],
              "technical_phrases": ["photonic transducer"]}
    broad = {"phrase": "data method", "cooccurring_phrases_same_works": [], "evidence": [{"work_id": "a"}]}
    result = evaluate_family({"rows": [broad]}, family, documents, 1, 15)
    assert result["technical_control_recovery_in_top15"] is False
    narrow = dict(broad, phrase="photonic transducers")
    result = evaluate_family({"rows": [narrow]}, family, documents, 1, 15)
    assert result["technical_control_recovery_in_top15"] is True
    assert result["independent_precision"] is None
    assert result["coherent_line_verified"] is None
    assert result["control_ids_not_in_frozen_eligible_input"] == ["old.0002"]


def test_source_span_validation_checks_same_work_and_rejects_wrong_quote():
    payload = {"documents": [{"id": "a", "title": "PT", "abstract": "photonic transducer (PT)"}]}
    span = {"field": "title", "start": 0, "end": 2, "quote": "PT",
            "definition": {"field": "abstract", "start": 0, "end": len(payload["documents"][0]["abstract"]),
                           "quote": "photonic transducer (PT)"}}
    result = {"rows": [{"evidence": [{"work_id": "a", "source_phrase_spans": [span]}]}]}
    assert verify_source_spans(result, payload) == 1
    changed = deepcopy(result)
    changed["rows"][0]["evidence"][0]["source_phrase_spans"][0]["definition"]["quote"] = "different work"
    with pytest.raises(ValueError, match="same work"):
        verify_source_spans(changed, payload)
