import json
import sqlite3

import pytest

from saia.controlled_collection import sha256_file
from saia.cross_domain_relevance_packet import build, json_bytes


def _write(path, value):
    path.write_bytes(json_bytes(value))


def _inputs(tmp_path):
    plan_path = tmp_path / "plan.json"
    catalog_path = tmp_path / "catalog.json"
    arxiv_path = tmp_path / "arxiv.json"
    openalex_path = tmp_path / "openalex.json"
    followup_path = tmp_path / "followup.json"
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    manifest_path = index_dir / "manifest.json"
    _write(manifest_path, {"version": "pinned-test"})
    cases = [{"case_id": f"case-{number:02d}", "query_ru": f"Поиск {number}"}
             for number in range(22)]
    _write(plan_path, {"version": "goal-cross-domain-compound-pilot-v1",
                       "cases": cases})
    _write(catalog_path, {"version": "priority-catalog-v1",
                          "customer_examples": []})
    period = {"from": "2021-09-01", "as_of_exclusive": "2026-09-01"}
    rows = [{"case_id": case["case_id"],
             "status": "exact_index_probe_complete",
             "arxiv_ids": ["2401.00001"] if number == 0 else [],
             "match_count": 1 if number == 0 else 0}
            for number, case in enumerate(cases)]
    _write(arxiv_path, {"version": "goal-cross-domain-compound-arxiv-probe-v1",
                        "config_sha256": sha256_file(plan_path),
                        "catalog_sha256": sha256_file(catalog_path),
                        "index_manifest_sha256": sha256_file(manifest_path),
                        "period": period, "rows": rows})
    _write(followup_path, {
        "version": "goal-cross-domain-openalex-followup-v1",
        "frozen_compound_plan_sha256": sha256_file(plan_path),
        "date_from": "2021-09-01", "as_of_date": "2026-09-01",
        "cases": [{"case_id": "case-00"}]})
    works = [{"source_ids": [f"https://openalex.org/W{number}"],
              "urls": [f"https://doi.org/10.1234/{number}"],
              "title": f"Test work {number}", "abstract": "A relevant abstract",
              "published_at": "2024-01-01",
              "strict_compound_match": number == 1}
             for number in (1, 2)]
    _write(openalex_path, {"version": "goal-cross-domain-openalex-followup-v1",
                          "case_plan_sha256": sha256_file(plan_path),
                          "live_config_sha256": sha256_file(followup_path),
                          "query_mode": "boolean", "period": period,
                          "rows": [{"case_id": "case-00", "works": works,
                                    "returned_count": 2, "strict_compound_count": 1,
                                    "errors": {}}]})
    with sqlite3.connect(index_dir / "index.sqlite3") as connection:
        connection.execute("CREATE TABLE works (arxiv_id TEXT, title TEXT, "
                           "abstract TEXT, first_submission_date TEXT)")
        connection.execute("INSERT INTO works VALUES (?,?,?,?)",
                           ("2401.00001", "A technical result", "Abstract text",
                            "2024-01-01"))
    return dict(plan_path=plan_path, catalog_path=catalog_path,
                arxiv_report_path=arxiv_path, openalex_report_path=openalex_path,
                followup_path=followup_path, index_dir=index_dir)


def test_packet_is_deterministic_blind_and_audited(tmp_path):
    inputs = _inputs(tmp_path)
    packet, audit = build(**inputs)
    assert len(packet["items"]) == 3
    assert {row["document"]["source"] for row in packet["items"]} == \
        {"arxiv", "openalex"}
    assert all(row["review"]["topical_relevance"] is None
               for row in packet["items"])
    assert "strict_compound_match" not in json.dumps(packet["items"])
    assert "source_rank" not in json.dumps(packet["items"])
    assert {row.get("stratum") for row in audit["rows"]
            if "stratum" in row} == {"strict_index_match",
                                     "strict_title_abstract_match",
                                     "api_only_rejected_by_title_abstract"}
    assert (packet, audit) == build(**inputs)


def test_packet_rejects_changed_followup_config(tmp_path):
    inputs = _inputs(tmp_path)
    followup = inputs["followup_path"]
    changed = json.loads(followup.read_text(encoding="utf-8"))
    changed["cases"][0]["case_id"] = "case-01"
    _write(followup, changed)
    with pytest.raises(ValueError, match="frozen pilot inputs"):
        build(**inputs)
