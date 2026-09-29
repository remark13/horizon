import pytest
from saia.public_series_benchmark import features, outcomes, metrics


def test_features_ignore_future():
    s = dict(zip(range(2005, 2016), [1, 1, 1, 2, 3, 4, 100, 100, 100, 100, 100]))
    assert features(s, 2010)["candidate"]
    changed = {y: (0 if y > 2010 else v) for y, v in s.items()}
    assert features(s, 2010) == features(changed, 2010)
    assert outcomes(s, 2010, 3)["grew_mean_50pct"]
    assert not outcomes(changed, 2010, 3)["grew_mean_50pct"]


def test_missing_is_not_zero():
    with pytest.raises(ValueError, match="Missing"):
        features({2010: 5}, 2010)
    with pytest.raises(ValueError, match="Missing"):
        outcomes({2011: 5}, 2010, 1)


def test_zero_previous_does_not_make_infinite_signal():
    s = dict(zip(range(2005, 2011), [0, 0, 0, 2, 3, 4]))
    assert features(s, 2010)["past_growth_ratio"] is None
    assert not features(s, 2010)["candidate"]


def test_invalid_value_rejected():
    s = {y: 1 for y in range(2005, 2011)}
    s[2008] = float("nan")
    with pytest.raises(ValueError, match="Invalid"):
        features(s, 2010)


def test_metrics_and_undefined_precision():
    r = [{"p": True, "y": True}, {"p": True, "y": False},
         {"p": False, "y": True}, {"p": False, "y": False}]
    m = metrics(r, "p", "y")
    assert (m["TP"], m["FP"], m["FN"], m["TN"]) == (1, 1, 1, 1)
    assert m["precision"] == m["recall"] == m["balanced_accuracy"] == .5
    assert metrics([{"p": False, "y": False}], "p", "y")["precision"] is None
