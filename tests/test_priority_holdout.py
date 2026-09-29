from pathlib import Path

import pytest

from saia.priority_holdout import freeze


def test_frozen_external_reference_is_separate_from_customer_seed(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "holdout.json"
    result = freeze(root / "data/reference/public_signals/catalog.v5.json",
                    root / "data/reference/priority_catalog/v1/catalog.json", output)
    assert result["counts"]["total"] == 13
    assert sum(item["role"] == "out_of_scientific_scope_control"
               for item in result["items"]) == 1
    assert len({item["id"] for item in result["items"]}) == 13
    assert result["policy"]["not_a_ground_truth_of_future_success"] is True
    with pytest.raises(FileExistsError):
        freeze(root / "data/reference/public_signals/catalog.v5.json",
               root / "data/reference/priority_catalog/v1/catalog.json", output)
