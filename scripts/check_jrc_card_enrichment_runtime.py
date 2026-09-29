"""Read-only controls for JRC explanations, profiles and portable card exports."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from saia.hybrid import digest
from saia.public_signals import load_catalog
from scripts.check_public_signal_results_runtime import CONTROL, EXPECTED, NEWS_CONTROL

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8082")
    parser.add_argument("--expected-version", default="0.4.61")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/jrc-card-enrichment-0.4.61-2026-09-29")
    args = parser.parse_args()
    if args.base not in {"http://127.0.0.1:8082", "http://127.0.0.1:8080"}:
        raise ValueError("Only the local SAIA application is in scope")
    out = args.output.resolve()
    if not out.is_relative_to((ROOT / "outputs").resolve()):
        raise ValueError("Output must stay inside the project outputs directory")

    def get(path):
        with urlopen(args.base + path, timeout=55) as response:
            raw = response.read(40_000_001)
            if len(raw) > 40_000_000:
                raise ValueError("Response exceeds the check size guard")
            return raw, response.headers

    def get_json(path):
        return json.loads(get(path)[0])

    assert get_json("/health")["version"] == args.expected_version
    rows = []
    for offset in (0, 100, 200):
        packet = get_json(f"/api/public-signals?source_id=jrc_weak_signals_2024&limit=100&offset={offset}")
        assert packet["total"] == 216
        rows.extend(packet["records"])
    assert len(rows) == len({row["id"] for row in rows}) == 216
    canonical = {row["id"]: row for row in load_catalog()["records"]}
    assert all(all(row[key] == canonical[row["id"]][key] for key in
                   ("title", "source_url", "source_date", "source_page", "source_id", "source_year")) for row in rows)
    enriched = [row for row in rows if row.get("source_explanations")]
    assert len(enriched) == 39
    charts = 0
    for row in enriched:
        value = row["source_explanations"][0]
        assert value["title"] == row["title"] and row["source_page"] in value["pages"]
        assert value["explanation_payload_sha256"] == digest({key: data for key, data in value.items()
                                                               if key not in {"explanation_payload_sha256", "html"}})
        assert value["used_for_score"] is False and value["primary_papers_verified"] is False
        assert value["scope"] == "historical_publisher_explanation"
        assert "<script" not in value["html"] and "Что сообщает JRC" in value["html"]
        charts += bool(value.get("historical_radar"))
    assert charts == 7
    out.mkdir(parents=True, exist_ok=True)
    for identifier in ("jrc-2024-p113-041", "jrc-2024-p120-072"):
        for lang in ("ru", "en"):
            raw, headers = get(f"/public-signals/{identifier}/brief?lang={lang}")
            html = raw.decode()
            assert "Что сообщает JRC" in html and "CC BY 4.0" in html and "GPT-4-32K" in html
            assert f'lang="{lang}"' in html and "<script" not in html
            assert headers["Content-Disposition"].endswith(f"-{lang}.html\"")
            if identifier.endswith("072"):
                assert "95,56%" in html and "6 лет" in html and "2021–2024" in html and "2021–2023" in html
            (out / f"{identifier}-{lang}.html").write_bytes(raw)
    control = get_json(f"/signals/{CONTROL}?score_run_id=9509")
    scientific_sha = hashlib.sha256(json.dumps(control, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert scientific_sha == EXPECTED
    news = get_json(f"/signals/{NEWS_CONTROL}/7264/assessment?score_run_id=10384")
    assert (news["scientific_baseline"], news["external_contribution"], news["overall_score"]) == (56.03, 4.8, 60.83)
    assert news["publication_profile"]["used_for_score"] is False
    current = "universal-monthly-v2-645c31dd-7994-4071-91e1-8d225aaae264"
    packet = get_json(f"/scout-results/{current}?score_run_id=10416")
    profiles = [row["assessment"]["publication_profile"] for row in packet["queue"]]
    assert all(profile["scope"] == "retrieved_topic_sample_only" and profile["used_for_score"] is False for profile in profiles)
    (out / "publication-profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    identifier = packet["queue"][0]["card"]["candidate_id"]
    raw, _ = get(f"/results/{current}/export?score_run_id=10416&candidate_ids={identifier}")
    exported = json.loads(raw)
    assert exported["report_payload_sha256"] == digest({key: data for key, data in exported.items() if key != "report_payload_sha256"})
    assert exported["additional_card_data"][0]["multisource_assessment"]["publication_profile"]["used_for_score"] is False
    assert exported["scientific_results_modified"] is False and exported["model_generation_requested"] is False
    (out / "scientific-card-export.json").write_bytes(raw)
    summary = {"checked_at": datetime.now(timezone.utc).isoformat(), "app_version": args.expected_version,
               "jrc_catalogue_records": 216, "jrc_explanations": len(enriched), "jrc_historical_profiles": charts,
               "jrc_descriptions_not_yet_transferred": 216 - len(enriched),
               "canonical_titles_urls_dates_unchanged": True, "pdf_attribution_and_adaptation_present": True,
               "bilingual_offline_cards_verified": True, "historical_period_inconsistency_visible": True,
               "scientific_control_sha256": scientific_sha, "scientific_control_unchanged": True,
               "news_scoring_control_unchanged": True, "own_profiles_do_not_change_score": True,
               "current_query_profiles": len(profiles),
               "profiles_with_recent_share": sum(profile["recent_share_percent"] is not None for profile in profiles),
               "json_export_checksum_verified": True, "expert_reviews_created": False,
               "external_network_requested": False, "model_called": False}
    downloaded = out / "browser-downloaded-asynchronous-en.html"
    if downloaded.exists():
        expected, _ = get("/public-signals/jrc-2024-p120-072/brief?lang=en")
        assert downloaded.read_bytes() == expected
        summary["browser_download_verified"] = True
        summary["browser_download_sha256"] = hashlib.sha256(expected).hexdigest()
    (out / "runtime-checks.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
