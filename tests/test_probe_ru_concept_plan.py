from scripts.probe_ru_concept_plan import single_group_legacy_allowed


def test_single_group_route_requires_only_shape_issue_and_opt_in():
    issue = ["concept_search_shape_unsupported_or_single_group"]
    assert single_group_legacy_allowed([["cell therapy"]], issue, True)
    assert not single_group_legacy_allowed([["cell therapy"]], issue, False)
    assert not single_group_legacy_allowed([["cell therapy"]],
                                           issue + ["group_1_source_span_not_exact_query_substring"],
                                           True)
    assert not single_group_legacy_allowed([["cell therapy"], ["AI"]], issue, True)
    assert not single_group_legacy_allowed([["AI"]], issue, True)
