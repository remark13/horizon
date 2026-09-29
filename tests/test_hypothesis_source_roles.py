from pathlib import Path

from saia.hypothesis_source_roles import audit


ROOT = Path(__file__).resolve().parents[1]


def test_fixed_pilot_roles_cover_ten_directions_and_six_customer_areas():
    result = audit(
        ROOT / "config/goal-pilot-hypothesis-source-roles.v1.json",
        ROOT / "data/reference/priority_catalog/v1/catalog.json",
        ROOT / "config/goal-cross-domain-compound-pilot.v1.json",
    )
    assert result["ten_directions"] == 10
    assert result["six_customer_areas"] == 6
    assert result["counts"]["national_search_area"] == 10
    assert result["counts"]["customer_supplied_example"] == 6
    assert result["role_of_map"] == (
        "expected_evidence_not_source_availability_or_signal_confirmation")
