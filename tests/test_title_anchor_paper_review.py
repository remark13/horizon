from scripts.build_title_anchor_paper_review import build_packets, select_cases


def _audit():
    phrases = ["alpha beta", "beta gamma", "gamma delta", "delta epsilon",
               "epsilon zeta", "task allocation", "visual inertial"]
    rows = []
    for index, phrase in enumerate(phrases):
        ids = [f"{index}-{number}" for number in range(4)]
        rows.append({"phrase_en": phrase, "expanded_ids": ids,
                     "title_anchored_ids": ids[:2]})
    return {"version": "title-anchor-cohort-audit-v1",
            "production_changed": False, "expansion_sha256": "fixture",
            "top15_display_groups": [
                {"representative_phrase_en": phrase} for phrase in phrases[:5]],
            "rows": rows}


def test_review_selection_is_reproducible_balanced_and_unique():
    audit = _audit()
    selected = select_cases(audit)
    assert selected == select_cases(audit)
    assert len(selected) == 28
    assert len({row["arxiv_id"] for row in selected}) == 28
    assert sum(row["selection_stratum"] == "title" for row in selected) == 14
    assert sum(row["selection_role"] == "posthoc_known_line_control"
               for row in selected) == 8


def test_blind_packet_has_no_selection_stratum_or_prefilled_labels():
    audit = _audit()
    works = {identifier: {"title": "An Example", "abstract": "Abstract"}
             for row in audit["rows"] for identifier in row["expanded_ids"]}
    blind, manifest = build_packets(audit, works, audit_sha256="sha")
    assert blind["case_count"] == 28
    assert len(manifest["rows"]) == 28
    assert all("selection_stratum" not in case for case in blind["cases"])
    assert all(all(value is None for value in case["review_fields"].values())
               for case in blind["cases"])
