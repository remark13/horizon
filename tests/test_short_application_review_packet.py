import json
from pathlib import Path

import pytest

from scripts.build_short_application_review_packet import build
from scripts.check_short_application_metadata_review import check
from scripts.probe_free_ru_direct_baseline import run as baseline_run


def test_frozen_short_application_review_and_label_coverage():
    root = Path(__file__).resolve().parents[1]
    packet_path = root / "outputs/free-ru-short-application-first15-review-packet-2026-09-27-v1.json"
    review_path = root / "config/free-ru-short-application-metadata-review-2026-09-27-v1.json"
    result = check(packet_path, review_path)
    assert [x["metadata_full_observed"] for x in result["cases"]] == [3, 1, 0]
    assert all(x["metadata_full_observed"] + x["metadata_not_observed"] == 15
               for x in result["cases"])


def test_packet_deduplicates_with_frozen_tie_rule(tmp_path: Path):
    work = {"openalex_id": "https://openalex.org/W1", "doi": None,
            "title": "A paper", "abstract": "Evidence", "type": "article",
            "publication_date": "2024-01-01", "within_exact_period": True,
            "rank_in_first_page": 1}
    rows = []
    for case_id in ("national-area-032", "national-area-039", "customer-signal-017"):
        for branch in (1, 2):
            rows.append({"case_id": case_id, "status": "succeeded",
                         "query_ru": "Запрос", "search_query": f"branch {branch}",
                         "results": [{**work, "openalex_id": f"https://openalex.org/{case_id}-W1"}]})
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"version": "free-ru-short-application-openalex-v2",
                                  "rows": rows}), encoding="utf-8")
    packet = build(source)
    assert len(packet["records"]) == 3
    assert len(packet["records"][0]["branch_appearances"]) == 2


def test_review_rejects_incomplete_labels(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    packet_path = root / "outputs/free-ru-short-application-first15-review-packet-2026-09-27-v1.json"
    review = json.loads((root / "config/free-ru-short-application-metadata-review-2026-09-27-v1.json").read_text(encoding="utf-8"))
    review["cases"][0]["not_observed_ranks"].pop()
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="Incomplete"):
        check(packet_path, path)


def test_direct_baseline_uses_frozen_queries_and_exact_dates(monkeypatch, tmp_path: Path):
    import scripts.probe_free_ru_direct_baseline as baseline

    calls = []
    monkeypatch.setattr(baseline, "_one", lambda _client, **kwargs: (
        calls.append(kwargs) or {"status": "succeeded", "results": []}))
    cases = [
        {"case_id": key, "query_ru": f"Запрос {i}"}
        for i, key in enumerate(("national-area-032", "national-area-039",
                                  "customer-signal-017", "other"), 1)]
    source = tmp_path / "cases.json"
    source.write_text(json.dumps({"version": "free-ru-short-application-pilot-v1",
                                  "cases": cases}), encoding="utf-8")
    result = baseline_run(source, client=object(), sleep=lambda _seconds: None)
    assert len(result["rows"]) == 3
    assert [x["query"] for x in calls] == ["Запрос 1", "Запрос 2", "Запрос 3"]
    assert all(x["exact_date_filter"] and x["per_page"] == 25 for x in calls)
