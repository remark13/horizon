from pathlib import Path

import numpy as np
import pytest
import yaml

from saia.composition_features import (
    POLICY_PATH,
    collapse_research_families,
    distributional_features,
    evaluate_features,
)


def test_distributional_features_detect_balanced_two_group_geometry():
    vectors = [
        [1.0, 0.0], [0.99, 0.10], [0.99, -0.10],
        [-1.0, 0.0], [-0.99, 0.10], [-0.99, -0.10],
    ]
    result = distributional_features(vectors)
    assert result["n_vectors"] == 6
    assert result["two_cluster_balance"] == pytest.approx(0.5)
    assert result["two_cluster_gap"] > 1.9
    assert result["two_cluster_gain"] > 1.0
    assert result["pairwise_min"] < -0.99
    assert result["centroid_mean"] is None
    assert result["centroid_q10"] is None
    assert result["centroid_min"] is None
    assert result["robust_centroid_outlier_fraction"] is None
    assert result["robust_centroid_outlier_cutoff"] is None


def test_distributional_features_are_finite_for_identical_vectors():
    result = distributional_features([[1, 0], [2, 0], [3, 0], [4, 0]])
    assert result["mean_pairwise"] == pytest.approx(1.0)
    assert result["centroid_min"] == pytest.approx(1.0)
    assert result["robust_centroid_outlier_fraction"] == 0.0
    assert all(value is None or not isinstance(value, float) or np.isfinite(value)
               for value in result.values())


@pytest.mark.parametrize("vectors", [[], [[1, 0]], [[1, 0], [0, 1]], [[0, 0], [1, 0], [0, 1]]])
def test_distributional_features_reject_missing_or_invalid_geometry(vectors):
    with pytest.raises(ValueError):
        distributional_features(vectors)


def test_declared_research_family_is_collapsed_without_dropping_other_works():
    ids, vectors = collapse_research_families(
        [1, 2, 3, 4],
        [[1, 0], [0.98, 0.02], [0, 1], [0, 0.9]],
        [[1, 2]],
    )
    assert ids == [1, 3, 4]
    assert len(vectors) == 3
    assert np.linalg.norm(vectors[0]) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        collapse_research_families([1, 2, 3], [[1, 0], [0, 1], [1, 1]], [[1, 2], [2, 3]])


def test_feature_comparison_is_diagnostic_and_never_returns_production_threshold():
    policy = yaml.safe_load(Path(POLICY_PATH).read_text())
    rows = []
    for index in range(10):
        positive = index < 5
        rows.append({
            "composition_sha256": f"{index:064x}",
            "target": positive,
            **{
                f"raw_{feature}": (0.9 + index / 1000 if positive else 0.2 + index / 1000)
                if cfg["orientation"] == "higher_supports_coherent_line"
                else (0.1 + index / 1000 if positive else 0.8 + index / 1000)
                for feature, cfg in policy["features"].items()
            },
        })
    result = evaluate_features(rows, policy, "raw_")
    assert result["production_threshold"] is None
    assert result["production_calibration_allowed"] is False
    assert set(result["top_leave_one_out_features"]) == set(policy["features"])
    assert all(item["leave_one_out"]["balanced_accuracy"] == 1.0
               for item in result["features"].values())
