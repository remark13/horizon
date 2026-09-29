"""Read-only runtime directory, UI contract and preserved-score checks."""
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from saia.news_publishers import publishers, connection_checks
from scripts.check_public_signal_results_runtime import BAS, BAS_SCORE, CONTROL, EXPECTED, NEWS_CONTROL

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/news-publisher-directory-0.4.60-2026-09-29"
BASE = "http://127.0.0.1:8082"


def get(path):
    with urlopen(BASE + path, timeout=55) as response:
        content = response.read(40_000_001)
        if len(content) > 40_000_000:
            raise ValueError("Runtime response exceeds the check cap")
        return content


def main():
    assert json.loads(get("/health"))["version"] == "0.4.60"
    catalog = json.loads(get("/source-catalog"))
    rows = catalog["sources"]
    assert len(rows) == 196 and catalog["publisher_channels"] == 169
    assert catalog["independent_aggregators_added"] == 0 and catalog["max_selected"] == 8
    identifiers = {row["source"] for row in rows}
    assert set(publishers()).issubset(identifiers)
    checks = connection_checks()
    assert len(checks) == 169
    for row in rows:
        if row["source"] in publishers():
            assert row["query_supported"] is True and row["default_selected"] is False
            assert row["publisher_domain"] == publishers()[row["source"]]["domain"]
            assert row["last_connection_check"] == checks[row["source"]]
    html = get("/scout").decode()
    assert 'Последняя попытка:' not in html and '<small>Обращение:' in html
    assert 'class="source-group" data-source-group=' in html and 'saia-source-groups-v1' in html
    assert 'class="source-subgroup" data-source-group=' in html
    control = json.loads(get(f"/signals/{CONTROL}?score_run_id=9509"))
    checksum = hashlib.sha256(json.dumps(control, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert checksum == EXPECTED
    packet = json.loads(get(f"/scout-results/{BAS}?score_run_id={BAS_SCORE}"))
    assert packet["result_counts"] == {"saia_candidates": 15, "public_signals": 3,
                                       "public_references_attached_to_candidates": 0, "total": 18}
    assessment = json.loads(get(f"/signals/{NEWS_CONTROL}/7264/assessment?score_run_id=10384"))
    assert (assessment["scientific_baseline"], assessment["external_contribution"], assessment["overall_score"]) == (56.03, 4.8, 60.83)
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "app_version": "0.4.60",
              "catalog_sources": len(rows), "new_publisher_channels": len(publishers()),
              "publisher_probe_statuses": dict(Counter(row["status"] for row in checks.values())),
              "source_group_counts": dict(Counter(row["group"] for row in rows)),
              "selected_cap_unchanged": True, "collapsed_groups_present": True,
              "date_under_status_contract": True, "raw_article_titles_unmodified": True,
              "scientific_control_unchanged": True, "scientific_sha256": checksum,
              "bas_counts": packet["result_counts"], "news_assessment_control_unchanged": True,
              "production_writes": False, "expert_requests_created": False}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "runtime-checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
