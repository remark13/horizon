import pytest

from scripts.probe_bas_cross_encoder_pairs import fit_threshold


def test_threshold_uses_only_balanced_development_examples():
    rows = [{"same_task": True, "score": score}
            for score in [0.9, 0.8, 0.7, 0.6, 0.5]]
    rows += [{"same_task": False, "score": score}
             for score in [0.4, 0.3, 0.2, 0.1, 0.0]]
    fitted = fit_threshold(rows)
    assert fitted["development_balanced_accuracy"] == 1.0
    assert 0.4 < fitted["threshold"] < 0.5
    with pytest.raises(ValueError, match="five examples"):
        fit_threshold(rows[:-1])
