from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi.testclient import TestClient

from saia import expert_requests as requests, public_signal_reviews as reviews, scout_public_signals as public
from saia.api import app

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)
ROW = public.lookup("jrc-2024-p113-041")


def snapshot():
    return {"item_kind": "public_signal", "public_signal_id": ROW["id"], "label": ROW["title"],
            "reference_content_sha256": ROW["reference_content_sha256"], "public_reference": ROW}


def database(monkeypatch, module, *, one=None, all_rows=None):
    calls = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, args): calls.append((query, args))
        def fetchone(self): return one.pop(0)
        def fetchall(self): return all_rows.pop(0)
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()
    monkeypatch.setattr(module.db, "connect", Connection)
    return calls


def prepare(monkeypatch):
    monkeypatch.setattr(requests, "export_cards", lambda *args: {"score_run_id": 12, "cards": [{"candidate_id": 7, "label": "Scientific", "composition_sha256": "a"*64}]})
    monkeypatch.setattr(public, "select", lambda packet, ids: ({}, [ROW]))


def test_public_only_dispatch_has_real_public_identity_no_fake_candidate(monkeypatch):
    prepare(monkeypatch)
    calls = database(monkeypatch, requests, one=[(NOW,)])
    value = requests.create("m", 12, [], "Scout", "Experts", public_signal_ids=[ROW["id"]])
    assert value["candidate_ids"] == []
    assert value["public_signal_ids"] == [ROW["id"]]
    assert value["candidate_snapshot"][0]["item_kind"] == "public_signal"
    assert "candidate_id" not in value["candidate_snapshot"][0]
    assert value["status"] == "awaiting_expert_review"
    assert len(calls) == 1


def test_mixed_dispatch_keeps_each_kind_and_rejects_over_total_limit(monkeypatch):
    prepare(monkeypatch)
    database(monkeypatch, requests, one=[(NOW,)])
    value = requests.create("m", 12, [7], "Scout", "Experts", public_signal_ids=[ROW["id"]])
    assert len(value["candidate_snapshot"]) == 2
    assert value["candidate_snapshot"][0]["composition_sha256"] == "a"*64
    with pytest.raises(ValueError):
        requests.create("m", 12, list(range(1, 16)), "Scout", "Experts", public_signal_ids=[ROW["id"]])


@pytest.mark.parametrize("ids,public_ids", [([], []), ([True], []), ([7, 7], []), ([], [ROW["id"], ROW["id"]]), ([], [True]), ([{}], [])])
def test_invalid_selection_fails_before_database_calls(ids, public_ids):
    with pytest.raises(ValueError):
        requests.create("m", 12, ids, "Scout", "Experts", public_signal_ids=public_ids)


def test_mixed_history_checks_public_reviews_separately_not_scientific_truth(monkeypatch):
    identifier = uuid.uuid4()
    scientific = {"candidate_id": 7, "composition_sha256": "a"*64, "label": "Scientific"}
    row = (identifier, "m", 12, [7], [scientific, snapshot()], "Scout", "Experts", "", NOW)
    calls = database(monkeypatch, requests, all_rows=[[row], [], [(str(identifier), ROW["reference_content_sha256"], NOW+timedelta(minutes=1))]])
    value = requests.history()["requests"][0]
    assert value["status"] == "partially_reviewed" and value["reviewed_count"] == 1 and value["total_count"] == 2
    assert "public_signal_review" in calls[2][0]
    assert calls[1][1] == (["a"*64],)


def test_a_public_opinion_for_another_request_does_not_validate_this_dispatch(monkeypatch):
    first, second = uuid.uuid4(), uuid.uuid4()
    rows = [(identifier, "m", 12, [], [snapshot()], "Scout", "Experts", "", NOW)
            for identifier in (first, second)]
    database(monkeypatch, requests, all_rows=[rows, [(str(first), ROW["reference_content_sha256"], NOW+timedelta(minutes=1))]])
    found = {row["request_id"]: row for row in requests.history()["requests"]}
    assert found[str(first)]["status"] == "reviewed"
    assert found[str(second)]["status"] == "awaiting_expert_review"


def test_public_expert_history_is_scoped_to_its_dispatch(monkeypatch):
    identifier = str(uuid.uuid4())
    calls = database(monkeypatch, reviews, all_rows=[[]])
    result = reviews.history_for_reference(ROW["reference_content_sha256"], request_id=identifier)
    assert "AND request_id=%s" in calls[0][0]
    assert calls[0][1] == (ROW["reference_content_sha256"], identifier, 100)
    assert result["history_scope"] == "exact_pinned_public_reference_and_request"


def test_review_binds_to_request_snapshot_not_current_catalog(monkeypatch):
    database(monkeypatch, reviews, one=[([snapshot()],)])
    assert reviews.request_snapshot(str(uuid.uuid4()), ROW["id"])["reference_content_sha256"] == ROW["reference_content_sha256"]
    database(monkeypatch, reviews, one=[([], )])
    with pytest.raises(ValueError, match="отсутствует"):
        reviews.request_snapshot(str(uuid.uuid4()), ROW["id"])


def test_public_review_does_not_create_scientific_review(monkeypatch):
    monkeypatch.setattr(reviews, "request_snapshot", lambda *args: snapshot())
    calls = database(monkeypatch, reviews, one=[(NOW,)])
    value = reviews.record(str(uuid.uuid4()), ROW["id"], "signal_supported", "Expert", "Это тест мнения, не реальная валидация технологии.", [ROW["source_url"]])
    assert value["scientific_score_modified"] is False
    assert "public_signal_review" in calls[0][0] and "score_candidate_review" not in calls[0][0]
    with pytest.raises(ValueError):
        reviews.record(str(uuid.uuid4()), ROW["id"], "research_line_supported", "Expert", "Это тест мнения, не реальная валидация технологии.", [])


def test_public_dispatch_and_review_api_keep_explicit_external_ids(monkeypatch):
    observed = {}
    def create(*args, **kwargs):
        observed.update(kwargs)
        return {"public_signal_ids": kwargs["public_signal_ids"], "candidate_ids": []}
    monkeypatch.setattr(requests, "create", create)
    client = TestClient(app)
    value = client.post("/expert-requests", json={"mission_id": "m", "score_run_id": 12,
        "public_signal_ids": [ROW["id"]], "requested_by": "Scout", "recipient": "Experts"})
    assert value.status_code == 201 and observed["public_signal_ids"] == [ROW["id"]]
    assert client.post("/expert-requests", json={"mission_id": "m", "score_run_id": 12,
        "candidate_ids": [True], "requested_by": "Scout", "recipient": "Experts"}).status_code == 422
    monkeypatch.setattr(reviews, "record", lambda *args: {"scientific_score_modified": False, "public_signal_id": args[1]})
    value = client.post(f"/public-signals/{ROW['id']}/reviews?request_id={uuid.uuid4()}", json={
        "decision": "signal_supported", "reviewed_by": "Expert", "rationale": "Достаточно длинный комментарий для проверки API.", "sources": []})
    assert value.status_code == 201 and value.json()["scientific_score_modified"] is False
