"""The outside-map route must preserve Russian input and approved EN terms."""

import uuid

from saia.compiled_query_plan_store import validate_compilation
from saia.query_plan_store import validate_approval
from saia.query_planning import preview


def test_outside_map_russian_query_can_keep_original_and_add_approved_english_phrase():
    query = "акустическая левитация лекарственных микрокапсул"
    shown = preview(query, 12)
    assert shown["suggestions"] == []
    approved = validate_approval(
        query, 12, shown["plan_payload_sha256"], [], "scout-ui", str(uuid.uuid4()))
    compiled = validate_compilation(
        approved,
        [{"branch_id": "original-query",
          "included_phrases": [query, "acoustic levitation of drug microcapsules"],
          "excluded_phrases": []}],
        "scout-ui", str(uuid.uuid4()),
    )
    assert compiled["branch_specs"][0]["source_query"] == query
    assert compiled["branch_specs"][0]["included_phrases"][1] == (
        "acoustic levitation of drug microcapsules")
    assert compiled["automatic_translation"] is False
