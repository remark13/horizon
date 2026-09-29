import json
from datetime import date
from pathlib import Path

import pytest

from saia.broad_title_phrases import (
    _display_groups, _matches_parent, _parent_regex, _validate_config,
    _year_window, propose,
)


CONFIG = Path(__file__).resolve().parents[1] / "config/broad-title-phrase-pilot-bas-v1.json"


def test_frozen_broad_plan_does_not_seed_known_narrow_lines():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    parent = " ".join(config["parent_terms"] + config["coarse_substrings"])
    assert "odometry" not in parent
    assert "task allocation" not in parent
    assert "inertial" not in parent


def test_complete_september_year_windows_and_title_only_phrase_proposals():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert _year_window("2016-09-01", date(2016, 9, 1), date(2026, 9, 1)) == 0
    assert _year_window("2025-08-31", date(2016, 9, 1), date(2026, 9, 1)) == 8
    rows = [
        {"arxiv_id": f"{i:04d}.00001", "first_submission_date": f"{year}-10-01",
         "title": "Drone visual inertial odometry method",
         "abstract": "Unrelated abstract says task allocation"}
        for i, year in enumerate([2020, 2021, 2022, 2024, 2025, 2025])
    ]
    rows.extend({"arxiv_id": f"{i:04d}.00002",
                 "first_submission_date": f"{[2020, 2023, 2025][i % 3]}-10-01",
                 "title": "Drone generic mapping", "abstract": "Other topic"}
                for i in range(120))
    result = propose(rows, config, collection_audit={"selected_unique_ids": len(rows)})
    by_phrase = {row["phrase_en"]: row for row in result["proposals"]}
    assert "visual inertial odometry" in by_phrase
    assert "task allocation" not in by_phrase
    assert by_phrase["visual inertial odometry"]["title_work_count"] == 6
    assert result["weak_signal_verified"] is False
    assert result["relevance_reviewed"] is False


def test_parent_corpus_never_silently_truncates_or_counts_duplicate_ids():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    rows = [{"arxiv_id": "1", "title": "Drone navigation",
             "first_submission_date": "2025-01-01"}] * 2
    with pytest.raises(ValueError, match="duplicate IDs"):
        propose(rows, config, collection_audit={})


def test_cross_domain_plan_is_broad_and_coarse_prefilter_covers_parent():
    path = CONFIG.with_name("broad-title-phrase-pilot-robotic-manipulation-v1.json")
    config = json.loads(path.read_text(encoding="utf-8"))
    _validate_config(config)
    terms = " ".join(config["parent_terms"] + config["coarse_substrings"])
    assert "vision language action" not in terms
    assert "diffusion policy" not in terms
    assert "imitation learning" not in terms
    config["coarse_substrings"] = ["robotic manipulation"]
    with pytest.raises(ValueError, match="Coarse prefilter"):
        _validate_config(config)


def test_display_groups_keep_all_phrases_but_skip_near_duplicate_labels():
    proposals = [
        {"phrase_en": "vision language action", "title_work_count": 5},
        {"phrase_en": "language action", "title_work_count": 5},
        {"phrase_en": "flow matching", "title_work_count": 4},
    ]
    members = {
        "vision language action": {"1", "2", "3", "4", "5"},
        "language action": {"1", "2", "3", "4", "5"},
        "flow matching": {"1", "6", "7", "8"},
    }
    groups = _display_groups(proposals, members)
    assert len(groups) == 2
    assert [item["phrase_en"] for item in groups[0]["members"]] == [
        "vision language action", "language action"]
    assert proposals[0]["display_group_id"] == proposals[1]["display_group_id"]
    assert proposals[2]["display_group_id"] != proposals[0]["display_group_id"]
    assert proposals[1]["title_work_ids"] == sorted(members["language action"])


def test_inflection_group_does_not_claim_same_papers_or_merge_other_topics():
    proposals = [
        {"phrase_en": "action models", "title_work_count": 2},
        {"phrase_en": "action model", "title_work_count": 2},
        {"phrase_en": "world model", "title_work_count": 2},
    ]
    members = {"action models": {"1", "2"}, "action model": {"3", "4"},
               "world model": {"5", "6"}}
    groups = _display_groups(proposals, members)
    assert len(groups) == 2
    assert groups[0]["members"][1]["grouping_basis"] == "lexical_inflection"
    assert groups[0]["members"][1]["jaccard_to_representative"] is None
    assert proposals[2]["display_group_id"] != proposals[0]["display_group_id"]


def test_materials_parent_requires_battery_and_material_context():
    config = json.loads(CONFIG.with_name(
        "broad-title-phrase-pilot-battery-materials-development-v2.json"
    ).read_text(encoding="utf-8"))
    _validate_config(config)
    parent = _parent_regex(config["parent_terms"])
    context = _parent_regex(config["required_context_terms"])
    assert _matches_parent("Novel sodium-ion batteries with hard-carbon anodes",
                           parent, context)
    assert not _matches_parent("Battery trading and grid optimization", parent, context)
    assert not _matches_parent("Cathode material without an explicit battery term",
                               _parent_regex(["lithium ion battery"]), context)


def test_domain_noun_is_allowed_in_specific_title_phrase():
    config = json.loads(CONFIG.with_name(
        "broad-title-phrase-pilot-battery-materials-development-v2.json"
    ).read_text(encoding="utf-8"))
    rows = [{
        "arxiv_id": f"specific-{i}",
        "first_submission_date": f"{year}-10-01",
        "title": "Solid state battery electrode engineering",
        "abstract": "An original material study",
    } for i, year in enumerate([2020, 2021, 2022, 2025, 2025])]
    rows.extend({
        "arxiv_id": f"background-{i}",
        "first_submission_date": f"{[2020, 2023, 2025][i % 3]}-10-01",
        "title": "General battery cells study",
        "abstract": "Other topic",
    } for i in range(120))
    report = propose(rows, config, collection_audit={"selected_unique_ids": len(rows)})
    phrases = {item["phrase_en"] for item in report["proposals"]}
    assert "solid state battery" in phrases
    assert "battery cells" not in phrases
    assert report["generic_token_policy"] == "allow_with_specific"


def test_sparse_arxiv_parent_is_source_gap_not_absence_of_signals():
    config = json.loads(CONFIG.with_name(
        "broad-title-phrase-pilot-smr-holdout-v1.json"
    ).read_text(encoding="utf-8"))
    rows = [{"arxiv_id": f"smr-{i}",
             "first_submission_date": f"{2022 if i < 7 else 2025}-10-01",
             "title": "Small modular reactor study", "abstract": "Research"}
            for i in range(14)]
    report = propose(rows, config, collection_audit={"selected_unique_ids": len(rows)})
    assert report["arxiv_only_top15_source_sufficiency"]["status"] == (
        "too_sparse_even_for_fifteen_disjoint_minimum_sized_topics")
    assert report["arxiv_only_top15_source_sufficiency"][
        "minimum_parent_works_if_fifteen_topics_are_disjoint"] == 75
