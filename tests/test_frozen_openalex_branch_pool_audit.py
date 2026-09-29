import json
from pathlib import Path

from scripts.audit_frozen_openalex_branch_pool import audit


def test_frozen_branch_pool_dedup_and_mention_audit():
    root = Path(__file__).resolve().parents[1]
    result = audit(root / "outputs/frozen-openalex-synonym-branches-2026-09-27-v2-abstracts.json",
                   root / "config/free-ru-no-suggestions-compound-pilot.v1.json")
    rows = {row["case_id"]: row for row in result["cases"]}
    assert len(rows) == 3
    assert rows["outside-quantum-archaeology"]["unique_openalex_ids"] == 83
    assert rows["outside-quantum-archaeology"]["all_groups_in_title_or_abstract_count"] == 3
    assert rows["negative-graphene-perpetual-motion"]["all_groups_in_title_or_abstract_count"] == 0
    ids = {item["openalex_id"] for item in
           rows["outside-quantum-archaeology"]["all_groups_in_title_or_abstract"]}
    assert "https://openalex.org/W4408486675" in ids
    assert result["limits"]["text_cooccurrence_not_relevance_or_primary_role"] is True


def test_branch_pool_audit_rejects_mismatched_query_config(tmp_path):
    root = Path(__file__).resolve().parents[1]
    original = json.loads((root / "config/free-ru-no-suggestions-compound-pilot.v1.json")
                          .read_text(encoding="utf-8"))
    original["cases"][0]["concept_groups"][0][0] = "changed"
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="do not match"):
        audit(root / "outputs/frozen-openalex-synonym-branches-2026-09-27-v2-abstracts.json",
              path)
