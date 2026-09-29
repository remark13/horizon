import json

import pytest

from scripts.probe_bas_article_facets_v2 import own_claim_ids, validate


def test_own_claim_cues_do_not_fallback_to_title_or_motivation():
    spans = {"T": "UAV research", "A1": "Drones could use this system.",
             "A2": "Here, we explore V-formations of non-lifting objects.",
             "A3": "The article proposes a UAV inspection method."}
    assert own_claim_ids(spans) == ["A2", "A3"]


def test_core_role_without_own_uav_claim_is_warned():
    spans = {"T": "Flow past non-lifting objects",
             "A1": "Drones may use the result.",
             "A2": "Here, we explore V-formations of non-lifting objects."}
    data = {"uav_role": "core_uav_research", "uav_evidence_id": "A1",
            "contribution_id": "A2", "research_object": "non-lifting objects",
            "main_task": "study V-formations", "technical_method": "unknown",
            "task_evidence_id": "A2", "method_evidence_ids": [],
            "data_setting": "not_stated", "data_setting_evidence_id": "none"}
    result = validate({"response": json.dumps(data)}, spans, ["A2"])
    assert result["core_without_uav_in_own_claim_warning"] is True
    data["task_evidence_id"] = "A1"
    with pytest.raises(ValueError, match="own-claim"):
        validate({"response": json.dumps(data)}, spans, ["A2"])
