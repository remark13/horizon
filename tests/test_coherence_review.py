from fastapi.testclient import TestClient
import pytest

from saia.api import app
from saia.coherence_review import read_packet, validate_submission


def _complete_submission() -> dict:
    packet = read_packet()
    return {
        "packet_sha256": packet["packet_sha256"],
        "reviewer_id": "independent-reviewer-1",
        "independent_review_declared": True,
        "answers": [{
            "case_id": case["case_id"],
            "same_research_problem": "unclear",
            "same_technical_mechanism": "unclear",
            "one_signal_line": "unclear",
            "evidence_quote_a": case["paper_a"]["title"][:24],
            "evidence_quote_b": case["paper_b"]["title"][:24],
            "rationale": "По представленным аннотациям недостаточно оснований для вывода.",
        } for case in packet["cases"]],
    }


def test_blind_packet_has_30_pairs_and_no_scores_or_developer_labels():
    packet = read_packet()
    assert len(packet["cases"]) == 30
    assert len({case["case_id"] for case in packet["cases"]}) == 30
    assert all("review_fields" not in case for case in packet["cases"])
    assert all(case["paper_a"]["source_url"].startswith("https://")
               and case["paper_b"]["source_url"].startswith("https://")
               for case in packet["cases"])


def test_complete_submission_is_validated_without_claiming_gold():
    result = validate_submission(_complete_submission())
    assert len(result["answers"]) == 30
    assert len(result["submission_sha256"]) == 64
    assert "не gold" in result["interpretation"]


def test_submission_rejects_wrong_packet_and_invented_quotes():
    submission = _complete_submission()
    submission["packet_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Пакет изменился"):
        validate_submission(submission)
    submission = _complete_submission()
    submission["answers"][0]["evidence_quote_a"] = "несуществующая цитата статьи"
    with pytest.raises(ValueError, match="дословно"):
        validate_submission(submission)


def test_review_screen_and_api_are_separate_from_scout():
    client = TestClient(app)
    page = client.get("/coherence-review")
    assert page.status_code == 200
    assert "Обычная выдача кандидатов не зависит" in page.text
    packet = client.get("/coherence-review/packet")
    assert packet.status_code == 200
    assert len(packet.json()["cases"]) == 30
    response = client.post("/coherence-review/validate", json=_complete_submission())
    assert response.status_code == 200
    assert len(response.json()["answers"]) == 30
