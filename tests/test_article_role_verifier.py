import pytest

from scripts.diagnose_article_role_verifier import _validate_response


def test_article_role_verifier_requires_grounded_quote():
    title = "Quantum sensor for archaeology"
    abstract = "A field study measured a Roman tunnel in Lisbon."
    assert _validate_response({"response": (
        '{"decision":"direct","evidence_quote":"field study measured a Roman tunnel",'
        '"reason_ru":"Полевое измерение."}')}, title, abstract)["decision"] == "direct"
    with pytest.raises(ValueError, match="not in the supplied text"):
        _validate_response({"response": (
            '{"decision":"direct","evidence_quote":"commercial product launched",'
            '"reason_ru":"Нет такой цитаты."}')}, title, abstract)


def test_article_role_verifier_rejects_unsubstantiated_non_uncertain_decision():
    with pytest.raises(ValueError, match="needs an exact source quote"):
        _validate_response({"response": (
            '{"decision":"not_direct","evidence_quote":"",'
            '"reason_ru":"Нет опоры."}')}, "Title", "Abstract")
