from datetime import date, datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import httpx

from saia.cross_domain_pilot import (
    build_openalex_review_artifacts, build_review_artifacts, collect_openalex_pilot,
    load_pilot, scan_local_arxiv,
)
from saia.local_arxiv_search import validate_inventory


def row(identifier: str, title: str, abstract: str, created: date) -> dict:
    stamp = datetime.combine(created, datetime.min.time(), tzinfo=timezone.utc)
    return {
        "id": identifier, "title": title, "abstract": abstract,
        "categories": "cs.AI", "versions": [{
            "version": "v1", "created": stamp.strftime("%a, %d %b %Y %H:%M:%S %Z")
        }],
        "authors_parsed": [["Doe", "Jane", ""]], "doi": None,
        "journal-ref": None, "comments": None, "update_date": created,
        "authors": "Doe, Jane", "license": None,
    }


def test_frozen_pilot_has_three_unlabelled_cases_per_internal_profile():
    pilot = load_pilot()
    assert len(pilot["profiles"]) == 9
    assert sum(len(profile["cases"]) for profile in pilot["profiles"]) == 27
    assert pilot["interpretation"]["weak_signal_labels_present"] is False
    assert pilot["interpretation"]["profile_names_exposed_in_main_ui"] is False


def test_one_pass_scan_counts_by_year_and_packet_hides_counts(tmp_path):
    path = tmp_path / "train-00000-of-00001.parquet"
    pq.write_table(pa.Table.from_pylist([
        row("2201.00001", "Useful AI agents", "Agentic artificial intelligence.", date(2022, 1, 1)),
        row("2301.00001", "Another AI agents paper", "AI agents cooperate.", date(2023, 1, 1)),
        row("2301.00002", "Noise", "Unrelated material.", date(2023, 1, 2)),
        row("2701.00001", "Future AI agents", "AI agents.", date(2027, 1, 1)),
    ]), path)
    validate_inventory.cache_clear()
    pilot = {
        "version": "fixture",
        "period": {"date_from": "2021-01-01", "as_of_date": "2026-01-01"},
        "sampling": {"local_arxiv": "deterministic_min_hash_per_year"},
        "profiles": [{"profile_id": "ai", "cases": [{
            "case_id": "ai/agents", "scope": "subarea", "label_ru": "ИИ-агенты",
            "included_terms": ["AI agents", "agentic artificial intelligence"],
            "exclusions": [],
        }]}],
    }
    report = scan_local_arxiv(tmp_path, pilot, expected_files=1, expected_rows=4)
    case = report["cases"][0]
    assert case["eligible_matches"] == 2
    assert case["year_counts"]["2022"] == 1
    assert case["year_counts"]["2023"] == 1
    assert len(case["review_sample"]) == 2
    assert report["interpretation"]["weak_signal_assessment_performed"] is False
    packet, template = build_review_artifacts(report)
    assert len(packet["items"]) == len(template["annotations"]) == 2
    assert "eligible_matches" not in str(packet)
    assert "year_counts" not in str(packet)
    assert packet["blinding"]["gold_labels_present"] is False


def test_openalex_pilot_stops_after_bounded_rate_limit_retries():
    seen = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(429, request=request)

    pilot = {
        "period": {"date_from": "2021-01-01", "as_of_date": "2026-01-01"},
        "profiles": [{"profile_id": "ai", "cases": [
            {"case_id": "ai/one", "scope": "broad", "label_ru": "Один",
             "included_terms": ["artificial intelligence"], "exclusions": []},
            {"case_id": "ai/two", "scope": "subarea", "label_ru": "Два",
             "included_terms": ["AI agents"], "exclusions": []},
        ]}],
    }
    client = httpx.Client(transport=httpx.MockTransport(transport))
    report = collect_openalex_pilot(client, pilot, pause_seconds=0)
    assert len(seen) == 3
    assert report["cases"][0]["request_status"] == "rate_limited"
    assert report["cases"][1]["request_status"] == "not_attempted_after_rate_limit"
    assert report["interpretation"]["missing_or_unattempted_is_zero"] is False
    packet, template = build_openalex_review_artifacts(report)
    assert packet["items"] == []
    assert template["annotations"] == []
