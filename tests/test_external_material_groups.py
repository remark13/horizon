from copy import deepcopy
from itertools import permutations

import pytest

from saia import external_material_groups as grouping
from saia import source_context


def artifact(identifier, relations=(), *, record_type="research_dataset", title="Same visible title", url=None):
    source_record = {"doi": identifier, "related_dois": [{"doi": target, "relation": relation} for target, relation in relations]}
    return {"source": "datacite", "source_name": "DataCite", "group": "software", "record_type": record_type,
            "url": url or "https://doi.org/" + identifier.lower(), "title_original": title,
            "observation_id": "data-observation", "record_date": "2025", "date_precision": "year",
            "date_kind": "resource_publication", "retrieved_at": "2026-09-28T00:00:00Z",
            "identity_metadata": grouping.identity_metadata("datacite", source_record),
            "match_status": "automatic_search_match", "expert_validated": False,
            "scientific_score_modified": False}


def project(source, identifier, reference, *, funders=None, amount=100, url=None):
    raw = {"project_id": identifier, "grant_reference": reference, "funders": funders,
           "award_id": reference}
    return {"source": source, "source_name": source, "group": "funding", "record_type": "funded_research_project",
            "url": url or f"https://example.org/{source}/{identifier}", "title_original": "Same project title",
            "observation_id": source + "-observation", "record_date": "2025-01-01",
            "date_kind": "project_start", "retrieved_at": "2026-09-28T00:00:00Z",
            "funding_amount": amount, "funding_currency": "USD",
            "identity_metadata": grouping.identity_metadata(source, raw)}


def funder(short="NSF", country="US"):
    return [{"shortName": short, "jurisdiction": country, "name": "Declared source funder"}]


@pytest.mark.parametrize("relation", sorted(grouping.DOI_RELATIONS))
def test_declared_relations_join_observed_endpoints_and_preserve_all_records(relation):
    rows = [artifact("10.1234/resource.v1", [("10.1234/resource", relation)]), artifact("10.1234/resource")]
    original = deepcopy(rows)
    packet = grouping.group_materials(rows)
    assert packet["raw_record_count"] == 2 and packet["display_group_count"] == 1
    family = packet["groups"][0]
    assert family["record_count"] == 2 and family["source_count"] == 1
    assert family["merge_evidence"][0]["relation"] == relation
    assert {r["url"] for r in family["records"]} == {r["url"] for r in rows}
    assert family["independent_confirmation_count"] is None
    assert family["scientific_score_modified"] is False
    assert rows == original
    family["records"][0]["title_original"] = "changed output"
    assert rows == original


@pytest.mark.parametrize("relation", ["Cites", "IsCitedBy", "IsPartOf", "HasPart", "IsDerivedFrom", "IsSupplementTo", "IsReviewedBy", "Unknown", None, [], {}])
def test_citation_part_derivative_or_malformed_relation_is_not_identity(relation):
    rows = [artifact("10.1234/a", [("10.1234/b", relation)]), artifact("10.1234/b")]
    assert grouping.group_materials(rows)["display_group_count"] == 2


def test_title_doi_suffix_and_shared_unobserved_parent_are_not_enough_to_merge():
    rows = [artifact("10.1234/a"), artifact("10.1234/a.v1"),
            artifact("10.1234/v2", [("10.1234/absent", "IsVersionOf")]),
            artifact("10.1234/v3", [("10.1234/absent", "IsVersionOf")])]
    packet = grouping.group_materials(rows)
    assert packet["display_group_count"] == 4
    assert packet["unobserved_related_records_fetched"] is False
    assert packet["title_similarity_used"] is False


def test_same_exact_doi_is_case_insensitive_but_dates_not_imputed():
    rows = [artifact("10.1234/ABC"), artifact("10.1234/abc")]
    rows[1]["record_date"] = "2026-01-02"
    rows[1]["date_precision"] = "day"
    family = grouping.group_materials(rows)["groups"][0]
    assert family["record_count"] == 2
    assert {r["record_date"] for r in family["records"]} == {"2025", "2026-01-02"}
    assert "family_birth_date" not in family


def test_versions_form_one_component_deterministically_even_with_cycles():
    rows = [artifact("10.1234/a", [("10.1234/b", "HasVersion")]),
            artifact("10.1234/b", [("10.1234/c", "IsPreviousVersionOf")]),
            artifact("10.1234/c", [("10.1234/a", "IsVersionOf")])]
    expected = grouping.group_materials(rows)
    assert expected["display_group_count"] == 1
    for reordered in permutations(rows):
        assert grouping.group_materials(list(reordered)) == expected


def test_conflicting_dataset_software_classification_stays_separate():
    rows = [artifact("10.1234/a", [("10.1234/b", "HasVersion")]),
            artifact("10.1234/b", record_type="research_software")]
    packet = grouping.group_materials(rows)
    assert packet["display_group_count"] == 2
    assert packet["conflicts"][0]["reasons"] == ["conflicting_artifact_type"]
    assert all(g["status"] == "identity_conflict_not_merged" for g in packet["groups"])


def test_common_landing_page_cannot_identify_different_unrelated_dois():
    packet = grouping.group_materials([artifact("10.1234/a", url="https://example.org/page"),
                                      artifact("10.1234/b", url="https://example.org/page")])
    assert packet["display_group_count"] == 2
    assert packet["conflicts"][0]["reasons"] == ["conflicting_doi_identity"]
    assert len({g["group_id"] for g in packet["groups"]}) == 2


@pytest.mark.parametrize("short,country,reference", [("NSF", "US", "2554349"), ("UKRI", "GB", "EP/W000123/1")])
def test_aggregated_grant_and_direct_source_need_exact_funder_and_number(short, country, reference):
    direct_source = "nsf_awards" if short == "NSF" else "ukri_gtr"
    rows = [project(direct_source, "direct-id", reference, amount=100),
            project("openaire_projects", "aggregate-id", reference, funders=funder(short, country), amount=200)]
    original = deepcopy(rows)
    packet = grouping.group_materials(rows)
    assert packet["display_group_count"] == 1
    family = packet["groups"][0]
    assert family["primary"]["source"] == direct_source and family["source_count"] == 2
    assert {m["funding_amount"] for m in family["records"]} == {100, 200}
    assert family["funding_amounts_combined"] is False and "funding_total" not in family
    assert rows == original
    assert grouping.group_materials(list(reversed(rows))) == packet


@pytest.mark.parametrize("funders,reference", [(funder("SNSF", "CH"), "2554349"), (funder("NSF", "CH"), "2554349"),
    (funder("NSF", "US"), "02554349"), (funder("NSF", "US"), "2554350"), (None, "2554349"),
    (funder("NSF", "US") * 2, "2554349"), ([{"shortName": [], "jurisdiction": "US"}], "2554349")])
def test_unknown_other_or_ambiguous_funder_and_different_exact_code_do_not_merge(funders, reference):
    rows = [project("nsf_awards", "direct-id", "2554349"),
            project("openaire_projects", "aggregate-id", reference, funders=funders)]
    assert grouping.group_materials(rows)["display_group_count"] == 2


def test_numeric_openaire_ukri_code_does_not_become_gateway_grant_reference():
    rows = [project("ukri_gtr", "gateway-id", "2741203"),
            project("openaire_projects", "ukri::aggregate", "2741203", funders=funder("UKRI", "GB"))]
    packet = grouping.group_materials(rows)
    assert packet["display_group_count"] == 2
    assert not any(k["scheme"].startswith("grant:") for m in rows for k in m["identity_metadata"]["keys"])


def test_conflicting_grants_on_one_landing_page_are_not_partially_merged():
    rows = [project("nsf_awards", "a", "2554349", url="https://example.org/page"),
            project("nsf_awards", "b", "2554350", url="https://example.org/page"),
            project("openaire_projects", "c", "2554349", funders=funder())]
    packet = grouping.group_materials(rows)
    assert packet["display_group_count"] == 3
    assert packet["conflicts"][0]["reasons"] == ["conflicting_grant_identity"]
    assert len({g["group_id"] for g in packet["groups"]}) == 3


def test_programme_or_contract_link_cannot_become_the_same_grant():
    award = project("nsf_awards", "a", "2554349", url="https://example.org/project")
    programme = {**award, "source": "eu_funding_tenders", "identity_metadata": {"keys": [], "relations": []}}
    assert grouping.group_materials([award, programme])["display_group_count"] == 2


def test_same_openaire_id_can_group_only_its_record_without_claiming_upstream_match():
    rows = [project("openaire_projects", "ukri::same", "2741203", funders=funder("UKRI", "GB")),
            project("openaire_projects", "ukri::same", "2741203", funders=funder("UKRI", "GB"))]
    packet = grouping.group_materials(rows)
    assert packet["display_group_count"] == 1
    assert packet["groups"][0]["source_count"] == 1
    assert packet["global_deduplication_complete"] is False


def test_exact_urls_do_not_strip_query_case_or_query_and_never_cross_evidence_groups():
    first = {"source": "mit_news_rss", "group": "commercial", "url": "https://example.org/a?ref=A", "observation_id": "a"}
    rows = [first, {**first, "observation_id": "b"}, {**first, "url": "https://example.org/a?ref=B"},
            {**first, "group": "science_extra"}]
    assert grouping.group_materials(rows)["display_group_count"] == 3


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "https://u:secret@example.org/", "not-a-url", "https://example.org/\n"])
def test_unsafe_urls_are_not_grouping_keys(bad):
    rows = [{"source": "mit_news_rss", "group": "commercial", "url": bad, "observation_id": str(i)} for i in range(2)]
    assert grouping.group_materials(rows)["display_group_count"] == 2


def test_empty_and_over_limit_inputs_never_drop_or_synthesize_records(monkeypatch):
    empty = grouping.group_materials([])
    assert empty["raw_record_count"] == empty["display_group_count"] == 0
    monkeypatch.setattr(grouping, "MAX_RECORDS", 1)
    rows = [artifact("10.1234/a"), artifact("10.1234/a")]
    packet = grouping.group_materials(rows)
    assert packet["raw_record_count"] == packet["display_group_count"] == 2
    assert packet["grouping_limit_exceeded"] is True


def test_context_keeps_raw_count_cache_binding_and_unavailable_source_unknown():
    rows = [{"doi": "10.1234/a", "title": "Artifact", "url": "https://doi.org/10.1234/a", "record_type": "research_dataset"},
            {"doi": "10.1234/b", "title": "Artifact v2", "url": "https://doi.org/10.1234/b", "record_type": "research_dataset",
             "related_dois": [{"doi": "10.1234/a", "relation": "IsNewVersionOf"}]}]
    saved = {"observation_id": "source-observation", "status": "complete", "created_at": "2026-09-28T00:00:00+00:00",
             "payload": {"observations": rows, "retrieved_at": "2026-09-28T00:00:00Z"}}
    original = deepcopy(saved)
    scope = {"composition_sha256": "a" * 64}
    packet = source_context._packet(scope, ["datacite", "epo_ops"], {"datacite": saved})
    assert packet["binding"] == scope
    assert packet["material_count"] == 2 and len(packet["reports"][0]["materials"]) == 2
    assert packet["material_grouping"]["display_group_count"] == 1
    assert packet["reports"][1]["observed_count"] is None
    assert packet["scientific_score_modified"] is False and packet["missing_is_zero"] is False
    assert saved == original
