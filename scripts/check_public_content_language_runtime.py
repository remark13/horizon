"""Read-only local checks of translations, canonical references and score controls."""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from saia.hybrid import digest
from saia.public_signal_i18n import title
from saia.public_signals import load_catalog
from scripts.check_public_signal_results_runtime import BAS, BAS_SCORE, CONTROL, EXPECTED, NEWS_CONTROL

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/public-content-language-0.4.59-2026-09-29"


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8082")
    parser.add_argument("--expected-version", default="0.4.59")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if args.base not in {"http://127.0.0.1:8082", "http://127.0.0.1:8080"}:
        raise ValueError("Only the local SAIA application is in scope")
    OUT = args.output.resolve()
    if not OUT.is_relative_to((ROOT / "outputs").resolve()):
        raise ValueError("Check output must stay inside this project's outputs directory")

    def get(path):
        with urlopen(args.base + path, timeout=55) as response:
            content = response.read(40_000_001)
            if len(content) > 40_000_000:
                raise ValueError("Response exceeds check size guard")
            return content, response.headers

    def get_json(path):
        return json.loads(get(path)[0])

    OUT.mkdir(parents=True, exist_ok=True)
    assert get_json("/health")["version"] == args.expected_version
    canonical = {row["id"]: row for row in load_catalog()["records"]}
    first = get_json("/api/public-signals?limit=100")
    assert first["total"] == 361 and first["archived_records"] == 93
    rows = first["records"]
    for offset in range(100, first["total"], 100):
        rows += get_json(f"/api/public-signals?limit=100&offset={offset}")["records"]
    assert len({row["id"] for row in rows}) == 361
    assert all(re.search("[А-Яа-яЁё]", row["title_ru"]) and row["category_ru"] for row in rows)
    assert all(all(row[key] == canonical[row["id"]][key] for key in
                   ("title", "source_url", "source_date", "source_year", "source_id")) for row in rows)
    russian = get_json("/api/public-signals?" + urlencode({"query": "анионообменной"}))
    english = get_json("/api/public-signals?" + urlencode({"query": "anion exchange"}))
    assert {r["id"] for r in russian["records"]} == {r["id"] for r in english["records"]} == {"jrc-2024-p106-001"}

    control = get_json(f"/signals/{CONTROL}?score_run_id=9509")
    scientific_sha = hashlib.sha256(json.dumps(control, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert scientific_sha == EXPECTED
    packet = get_json(f"/scout-results/{BAS}?score_run_id={BAS_SCORE}")
    assert packet["result_counts"] == {"saia_candidates": 15, "public_signals": 3,
                                       "public_references_attached_to_candidates": 0, "total": 18}
    previous = json.loads((ROOT / "outputs/recent-public-sources-0.4.58-2026-09-29/bas-public-only-export.json").read_text())
    pinned_before = {item["public_signal"]["id"]: item["public_signal"] for item in previous["ranked_result_items"]}
    for reference in packet["public_signals"]["records"]:
        before = pinned_before[reference["id"]]
        assert all(reference[key] == before[key] for key in
                   ("title", "source_url", "source_date", "reference_content_sha256"))
    without = get_json(f"/scout-results/{BAS}?score_run_id={BAS_SCORE}&include_public_signals=false")
    assert without["queue"] == packet["queue"]
    public_ids = [r["id"] for r in packet["public_signals"]["records"]]
    raw, _ = get(f"/results/{BAS}/export?" + urlencode({
        "score_run_id": BAS_SCORE, "include_scientific": "false", "public_signal_ids": ",".join(public_ids)}))
    exported = json.loads(raw)
    assert exported["report_payload_sha256"] == digest({key: value for key, value in exported.items()
                                                        if key != "report_payload_sha256"})
    assert exported["scientific_results_modified"] is False and exported["network_requested"] is False
    (OUT / "bas-public-only-export.json").write_bytes(raw)
    for lang in ("ru", "en"):
        raw, headers = get("/public-signals/jrc-2024-p113-041/brief?lang=" + lang)
        html = raw.decode()
        reference = next(r for r in packet["public_signals"]["records"] if r["id"] == "jrc-2024-p113-041")
        assert f'<h1 lang="{lang}">{title(reference, lang)}</h1>' in html
        assert "#page=113" in html and "Из публичного источника" in html and "<script" not in html
        assert headers["Content-Disposition"].endswith(f'-{lang}.html"')
        (OUT / f"edge-computing-{lang}.html").write_bytes(raw)
    try:
        get("/public-signals/jrc-2024-p113-041/brief?lang=de")
    except HTTPError as error:
        assert error.code == 422
    else:
        raise AssertionError("Unsupported language was accepted")
    browser_file = OUT / "browser-downloaded-en.html"
    browser_sha = None
    if browser_file.exists():
        downloaded, _ = get("/public-signals/jrc-2024-p114-046/brief?lang=en")
        assert browser_file.read_bytes() == downloaded
        browser_sha = hashlib.sha256(downloaded).hexdigest()
    news = get_json(f"/signals/{NEWS_CONTROL}/7264/assessment?score_run_id=10384")
    assert (news["scientific_baseline"], news["external_contribution"], news["overall_score"]) == (56.03, 4.8, 60.83)
    summary = {"checked_at": datetime.now(timezone.utc).isoformat(), "app_version": args.expected_version,
               "active_records_with_russian_titles": len(rows), "archived_records": 93,
               "bilingual_search_same_ids": True, "canonical_titles_urls_dates_unchanged": True,
               "scientific_control_unchanged": True, "scientific_sha256": scientific_sha,
               "bas_counts": packet["result_counts"], "public_source_hashes_unchanged": True,
               "scientific_ranking_unchanged": True, "news_score_control_unchanged": True,
               "selected_language_html_download_verified": True, "json_export_checksum_verified": True,
               "browser_download_verified": browser_sha is not None, "browser_download_sha256": browser_sha,
               "expert_requests_or_opinions_created": False, "external_network_requested": False}
    (OUT / "runtime-checks.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
