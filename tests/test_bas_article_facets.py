import json

import pytest

from scripts.probe_bas_article_facets import frozen_works, schema, validate


def test_selected_works_are_frozen_and_bounded():
    config = json.loads(open("config/bas-article-facets-pilot-v1.json", encoding="utf-8").read())
    works = frozen_works(config)
    assert len(works) == 13
    assert len({work["work_id"] for work in works}) == 13
    assert all(work["abstract"] for work in works)


def test_schema_requires_source_ids_and_validation_resolves_text():
    spans = {"T": "Drone paper", "A1": "We propose a UAV planner."}
    data = {"uav_role": "core_uav_research", "uav_evidence_id": "A1",
            "contribution_type": "method", "contribution_id": "A1",
            "research_object": "UAV", "main_task": "plan route",
            "technical_method": "planner", "task_evidence_id": "A1",
            "method_evidence_id": "A1"}
    assert schema(list(spans))["properties"]["task_evidence_id"]["enum"] == ["T", "A1"]
    result = validate({"response": json.dumps(data)}, spans)
    assert result["source_evidence"]["contribution_id"] == spans["A1"]
    data["contribution_id"] = "A99"
    with pytest.raises(ValueError, match="outside source"):
        validate({"response": json.dumps(data)}, spans)
