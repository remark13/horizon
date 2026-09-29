#!/usr/bin/env python3
"""Read-only checks of real saved results; never creates test expert opinions."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from saia.hybrid import digest

OUT = Path(__file__).resolve().parents[1] / "outputs/recent-public-sources-0.4.58-2026-09-29"
CONTROL = "universal-monthly-5177517d-5c5b-4fb8-b987-a66589771915"
EXPECTED = "f9d9aa446a22d742bbc4f8d09d15b8ef11812f7633388daed1647ef94e5b992b"
BAS = "universal-monthly-v2-23e50442-faa5-431d-b0d9-a95d82dceaa5"
BAS_SCORE = 10413
NEWS_CONTROL = "universal-monthly-full-arxiv-v1-fab72bd5-1b32-4e94-8529-3af45d69aac4"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8082")
    args = parser.parse_args()
    if args.base not in {"http://127.0.0.1:8080", "http://127.0.0.1:8082"}:
        raise ValueError("This check is restricted to the local SAIA application")

    def get(path):
        with urlopen(args.base + path, timeout=55) as response:
            body = response.read(40_000_001)
            if len(body) > 40_000_000:
                raise ValueError("Check exceeds size guard")
            return body, {key.casefold(): value for key, value in response.headers.items()}

    def get_json(path):
        return json.loads(get(path)[0])

    def checksum(value):
        assert value["report_payload_sha256"] == digest({k: v for k, v in value.items() if k != "report_payload_sha256"})

    OUT.mkdir(parents=True, exist_ok=True)
    health = get_json("/health")
    assert health["version"] == "0.4.58"
    catalog = get_json("/api/public-signals?limit=100")
    assert catalog["version"] == "public-signals-catalog-v6"
    assert catalog["total"] == 361 and catalog["archived_records"] == 93
    assert all(source["edition_year"] >= 2023 for source in catalog["sources"])
    assert all(row["source_year"] >= 2023 and row["type_label"] for row in catalog["records"])
    assert get_json("/api/public-signals?source_id=wef_emerging_technologies_2026")["total"] == 10
    assert get_json("/api/public-signals?source_id=msit_kribb_biotechnologies_2025")["total"] == 10
    assert get_json("/api/public-signals?year=2024&source_type=horizon_scan")["total"] == 25
    try:
        get("/api/public-signals?source_id=jrc_weak_signals_2021")
    except HTTPError as error:
        assert error.code == 422
    else:
        raise AssertionError("An archived source appeared as a current collection")
    control = get_json(f"/signals/{CONTROL}?score_run_id=9509")
    control_sha = hashlib.sha256(json.dumps(control, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert control_sha == EXPECTED, "Frozen scientific control changed"

    result = get_json(f"/scout-results/{BAS}?score_run_id={BAS_SCORE}")
    assert result["result_counts"] == {"saia_candidates": 15, "public_signals": 3,
                                      "public_references_attached_to_candidates": 0, "total": 18}
    public = result["public_signals"]["records"]
    assert {r["title"] for r in public} == {"truck drone", "edge computing for UAV", "Bio-inspired flapping-wing drones"}
    assert all(r["source_badge"] == "Из публичного источника" and r["scientific_score"] is None
               and r["current_weak_signal_verified"] is False and r["source_year"] >= 2023 for r in public)
    without_public = get_json(f"/scout-results/{BAS}?score_run_id={BAS_SCORE}&include_public_signals=false")
    assert result["queue"] == without_public["queue"], "Presentation join changed scientific rankings or scores"
    public_ids = [r["id"] for r in public]
    first = result["queue"][0]["card"]["candidate_id"]
    export_path = f"/results/{BAS}/export?"

    raw, _ = get(export_path + urlencode({"score_run_id": BAS_SCORE, "include_scientific": "false",
                                         "public_signal_ids": ",".join(public_ids)}))
    public_only = json.loads(raw)
    checksum(public_only)
    assert public_only["version"] == "saia-result-export-v3"
    assert public_only["scientific_results"]["cards"] == [] and public_only["ranked_candidates"] == []
    assert public_only["scope"]["exported_public_signal_ids"] == public_ids
    assert all(q["item_kind"] == "public_signal" for q in public_only["ranked_result_items"])
    assert public_only["scientific_results_modified"] is False and public_only["network_requested"] is False
    (OUT / "bas-public-only-export.json").write_bytes(raw)

    raw, _ = get(export_path + urlencode({"score_run_id": BAS_SCORE, "candidate_ids": str(first),
                                         "public_signal_ids": public_ids[0]}))
    mixed = json.loads(raw)
    checksum(mixed)
    assert mixed["scope"]["exported_candidate_ids"] == [first]
    assert mixed["scope"]["exported_public_signal_ids"] == [public_ids[0]]
    assert [q["item_kind"] for q in mixed["ranked_result_items"]] == ["saia_candidate", "public_signal"]
    (OUT / "bas-mixed-export.json").write_bytes(raw)
    try:
        get(export_path + urlencode({"score_run_id": BAS_SCORE, "include_scientific": "false",
                                     "public_signal_ids": "jrc-2024-p122-081"}))
    except HTTPError as error:
        assert error.code == 422
    else:
        raise AssertionError("An unrelated published reference was accepted")

    brief, headers = get("/public-signals/jrc-2024-p113-041/brief")
    html = brief.decode()
    assert "#page=113" in html and "Из публичного источника" in html and "<script" not in html
    assert "attachment" in headers["content-disposition"]
    (OUT / "edge-computing-for-uav.html").write_bytes(brief)
    for identifier in ("wef-2026-09", "kribb-2025-07", "eea-2024-16"):
        raw, _ = get(f"/public-signals/{identifier}/brief")
        value = raw.decode()
        assert "Год подборки" in value and "<script" not in value
        assert "страница None" not in value and "2025-01-01" not in value
        (OUT / (identifier + ".html")).write_bytes(raw)
    scientific_brief, _ = get(f"/signals/{BAS}/{first}/brief?score_run_id={BAS_SCORE}")
    assert b"<svg" in scientific_brief and b"<script" not in scientific_brief
    (OUT / "bas-scientific-card.html").write_bytes(scientific_brief)

    news = get_json(f"/signals/{NEWS_CONTROL}/7264/assessment?score_run_id=10384")
    assert news["axes"]["market"]["label"] == "Новости"
    assert news["scientific_baseline"] == 56.03 and news["external_contribution"] == 4.8
    assert news["overall_score"] == 60.83
    summary = {"checked_at": datetime.now(timezone.utc).isoformat(), "health": health,
               "catalog_active_records": catalog["total"], "old_records_archived": catalog["archived_records"],
               "current_year_type_source_filters_verified": True,
               "frozen_control_unchanged": True, "frozen_scientific_sha256": control_sha,
               "bas_query": result["public_signals"]["query"], "bas_counts": result["result_counts"],
               "public_ids": public_ids, "public_titles": [r["title"] for r in public],
               "scientific_rank_and_score_unchanged": True,
               "public_only_export_checksum_verified": True, "mixed_export_checksum_verified": True,
               "out_of_scope_reference_rejected": True, "public_html_citation_verified": True,
               "scientific_html_still_renders": True, "news_score_control_unchanged": True,
               "expert_requests_or_opinions_created": False}
    (OUT / "runtime-checks.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
