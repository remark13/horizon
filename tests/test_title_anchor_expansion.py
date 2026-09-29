from scripts.probe_title_anchor_expansion import candidate_phrases


def test_title_anchors_preserve_rare_specific_line_and_deduplicate_works():
    shared = {"work_id": 7, "title": "Visual-Inertial Navigation for UAVs"}
    cards = [{"works": [shared, {"work_id": 8, "title": "Task Allocation for UAVs"}]},
             {"works": [shared]}]
    result = candidate_phrases(cards, stop={"for"}, generic={"uavs"})
    assert result["visual inertial navigation"]["source_work_ids"] == [7]
    assert result["task allocation"]["source_work_ids"] == [8]
    assert "for uavs" not in result


def test_title_anchors_exclude_generic_only_phrases():
    cards = [{"works": [{"work_id": 1, "title": "Drone UAV Flight"}]}]
    assert candidate_phrases(cards, stop=set(), generic={"drone", "uav", "flight"}) == {}
