import copy
import uuid

import pytest

from saia.compiled_query_plan_store import (CONCEPT_MATCHING, CONCEPT_VERSION,
                                            validate_compilation, verify_payload)
from saia.query_plan_store import validate_approval
from saia.query_planning import preview


def _approved():
    shown = preview("тканевая инженерия", 12)
    return validate_approval("тканевая инженерия", 12, shown["plan_payload_sha256"],
                             [], "analyst", str(uuid.uuid4()))


def test_compound_plan_is_explicit_append_only_and_legacy_compatible():
    spec = [{"branch_id": "original-query",
             "included_phrases": ["тканевая инженерия"],
             "concept_groups": [["neuromorphic chips"], ["edge devices"]],
             "excluded_phrases": []}]
    approved = _approved()
    compound = validate_compilation(approved, spec, "analyst", str(uuid.uuid4()))
    assert compound["version"] == CONCEPT_VERSION
    assert compound["branch_specs"][0]["matching"] == CONCEPT_MATCHING
    assert compound["automatic_translation"] is False
    assert verify_payload(compound) == compound

    old = copy.deepcopy(spec)
    old[0].pop("concept_groups")
    legacy = validate_compilation(approved, old, "analyst", str(uuid.uuid4()))
    assert legacy["version"] != CONCEPT_VERSION
    assert verify_payload(legacy) == legacy

    invalid = copy.deepcopy(spec)
    invalid[0]["concept_groups"] = [["same"], ["same"]]
    with pytest.raises(ValueError, match="повторяться"):
        validate_compilation(approved, invalid, "analyst", str(uuid.uuid4()))
