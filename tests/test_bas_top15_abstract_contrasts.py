import copy
import json

import pytest

from scripts.audit_bas_top15_abstract_contrasts import build_report


def test_all_top15_contrasts_bind_to_exact_abstract_sentences():
    config = json.load(open("config/bas-top15-abstract-contrast-review-v1.json", encoding="utf-8"))
    source = json.load(open(config["source"], encoding="utf-8"))
    prior = json.load(open(config["title_review"], encoding="utf-8"))
    result = build_report(config, source, prior)
    assert result["counts"] == {"cards": 15, "by_contrast_type": {
        "different_task": 12, "off_domain_object": 2, "parent_family_only": 1}}
    assert all(len(row["works"]) == 2 for row in result["rows"])
    assert all(work["url"].startswith("https://arxiv.org/abs/")
               for row in result["rows"] for work in row["works"])
    tampered = copy.deepcopy(config)
    tampered["decisions"][0]["evidence_ids"][0] = ["A999"]
    with pytest.raises(ValueError, match="Invalid abstract sentence"):
        build_report(tampered, source, prior)
