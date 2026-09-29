from scripts import auto_followup_broad_phrase_groups as followup


def test_complete_september_windows() -> None:
    assert followup._window("2025-08-31") == 2024
    assert followup._window("2025-09-01") == 2025


def test_group_search_unions_all_members_without_double_count(monkeypatch) -> None:
    calls = []

    def fake_search(_index, plan):
        phrase = plan["included_terms"][0]
        calls.append(phrase)
        return {"arxiv_ids": ["a", "b"] if phrase == "world action models"
                else ["b", "c"], "audit": {"phrase": phrase}}

    monkeypatch.setattr(followup, "exact_search", fake_search)
    group = {"members": [{"phrase_en": "world action models"},
                         {"phrase_en": "world action model"}]}
    cache = {}
    ids, audits = followup._group_hits(group, None, "2024-09-01", "2026-09-01", cache)
    assert ids == {"a", "b", "c"}
    assert [item["phrase_en"] for item in audits] == [
        "world action models", "world action model"]
    assert calls == ["world action models", "world action model"]
    followup._group_hits(group, None, "2024-09-01", "2026-09-01", cache)
    assert calls == ["world action models", "world action model"]
