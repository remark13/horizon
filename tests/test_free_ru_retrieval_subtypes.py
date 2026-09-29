import json
from pathlib import Path

from scripts.propose_free_ru_retrieval_subtypes import _validate, run


def test_subtype_spans_must_come_from_original_query():
    value = {"translation_en": "Quantum sensors in archaeology",
             "required_concepts": [{"source_span_ru": "Квантовые сенсоры",
                                    "term_en": "quantum sensors"}],
             "subtype_hypotheses": [{"source_span_ru": "gravimeter",
                                     "subtype_en": "quantum gravimeter",
                                     "why_search_ru": "подтип сенсора"}],
             "uncertainties_ru": []}
    assert "subtype_hypotheses_1_span_not_in_source" in _validate(
        value, "Квантовые сенсоры для археологических исследований")


def test_bounded_model_proposal_never_executed_as_search():
    root = Path(__file__).resolve().parents[1]
    config = root / "config/free-ru-subtype-retrieval-pilot-2026-09-27-v1.json"
    prompts = []

    def fake_post(_url, payload, timeout):
        prompts.append(payload["prompt"])
        return {"response": json.dumps({"translation_en": "technology example",
            "required_concepts": [{"source_span_ru": payload["prompt"].split("Russian query: ")[-1].split()[0],
                                   "term_en": "technology"}],
            "subtype_hypotheses": [], "uncertainties_ru": []})}

    report = run(config, post=fake_post, model_digest=lambda *_: "pinned-digest")
    assert len(prompts) == 4
    assert all(row["status"] == "parsed" for row in report["rows"])
    assert all(row["structural_issues"] == [] for row in report["rows"])
    assert report["limits"]["not_executed_in_user_route"] is True
