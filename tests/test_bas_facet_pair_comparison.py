from scripts.probe_bas_facet_pair_comparison import score_pairs


def _fixture():
    pairs, facets = [], {}
    identifier = 1
    for partition in ("development", "holdout"):
        for index in range(10):
            same = index < 5
            a, b = identifier, identifier + 1
            identifier += 2
            pairs.append({"partition": partition, "a": a, "b": b,
                          "same_task": same})
            facets[a] = {"status": "valid_source_ids",
                         "facets": {"task_family": "uav_self_localization"}}
            facets[b] = {"status": "valid_source_ids",
                         "facets": {"task_family": (
                             "uav_self_localization" if same
                             else "uav_navigation_path_planning")}}
    return pairs, facets


def test_task_facet_pair_rule_scores_full_balanced_set():
    pairs, facets = _fixture()
    result = score_pairs(pairs, facets)
    assert result["metrics"]["holdout"]["balanced_accuracy_abstentions_as_errors"] == 1
    assert result["metrics"]["holdout"]["covered"] == 10
    assert result["metrics"]["holdout"]["false_same"] == 0


def test_abstention_is_not_counted_as_correct_different():
    pairs, facets = _fixture()
    a = next(p["a"] for p in pairs if p["partition"] == "holdout"
             and not p["same_task"])
    facets[a] = {"status": "invalid"}
    result = score_pairs(pairs, facets)
    held = result["metrics"]["holdout"]
    assert held["covered"] == 9
    assert held["true_different"] == 4
    assert held["balanced_accuracy_abstentions_as_errors"] == 0.9
    assert next(row for row in result["rows"] if row["a"] == a)[
        "predicted_same_task"] is None
