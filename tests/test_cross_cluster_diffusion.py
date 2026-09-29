from dataclasses import replace
import math

import pytest

from saia.cross_cluster_diffusion import (
    DiffusionPolicy, find_phrase_candidates, lineage_families, text_phrases,
)
from saia import cross_cluster_diffusion as diffusion


POLICY = DiffusionPolicy(recent_windows=1, min_recent_works=2, max_recent_share=0.5)
WINDOWS = ["2020", "2021"]


def docs(*, prior_hits=0, recent_hits=2, prior_areas=(), recent_areas=("a", "b")):
    rows = []
    for year, hits, areas in (("2020", prior_hits, prior_areas),
                             ("2021", recent_hits, recent_areas)):
        for number in range(10):
            rows.append({"id": f"{year}-{number}", "window": year,
                         "title": "photonic transducer" if number < hits else "background material",
                         "abstract": "", "areas": [areas[number % len(areas)]] if number < hits and areas else []})
    return rows


def test_positive_growth_and_expanding_areas_are_observed_not_verified():
    result = find_phrase_candidates(docs(), WINDOWS, policy=POLICY)
    row = result["rows"][0]
    assert row["phrase"] == "photonic transducer"
    assert row["share_change"] == 0.2
    assert row["share_ratio"] is None
    assert row["ratio_undefined_when_prior_zero"] is True
    assert row["recent_active_areas"] == 2
    assert row["weak_signal_verified"] is None
    assert row["independent_diffusion_verified"] is None
    assert result["weak_signal_accuracy_measured"] is False


@pytest.mark.parametrize("prior,recent", [(2, 2), (3, 2), (2, 1)])
def test_flat_or_falling_terms_are_not_growing_candidates(prior, recent):
    result = find_phrase_candidates(docs(prior_hits=prior, recent_hits=recent,
                                        prior_areas=("a", "b")), WINDOWS, policy=POLICY)
    assert result["rows"] == []


def test_raw_count_growth_without_share_growth_is_rejected():
    rows = docs(prior_hits=2, recent_hits=2, prior_areas=("a", "b"))
    recent_copy = [dict(row, id=row["id"] + "-extra") for row in rows if row["window"] == "2021"]
    result = find_phrase_candidates(rows + recent_copy, WINDOWS, policy=POLICY)
    assert result["rows"] == []


def test_one_period_or_missing_parent_period_cannot_establish_growth():
    with pytest.raises(ValueError, match="comparison windows"):
        find_phrase_candidates(docs(), ["2021"], policy=POLICY)
    with pytest.raises(ValueError, match="Empty parent"):
        find_phrase_candidates([row for row in docs() if row["window"] == "2021"],
                               WINDOWS, policy=POLICY)


def test_duplicate_work_ids_do_not_create_support():
    rows = docs(recent_hits=1)
    result = find_phrase_candidates(rows + [rows[10]], WINDOWS, policy=POLICY)
    assert result["duplicate_rows_removed"] == 1
    assert result["unique_works"] == 20
    assert result["rows"] == []


def test_conflicting_duplicate_versions_are_rejected():
    rows = docs()
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        find_phrase_candidates(rows + [dict(rows[0], title="different title")], WINDOWS, policy=POLICY)


def test_one_area_or_topic_split_cannot_create_diffusion():
    assert find_phrase_candidates(docs(recent_areas=("a",)), WINDOWS, policy=POLICY)["rows"] == []
    families = lineage_families({"a", "b"}, [("a", "b")])
    assert find_phrase_candidates(docs(), WINDOWS, policy=POLICY, area_families=families)["rows"] == []


def test_no_area_identity_gain_even_with_relative_growth_is_rejected():
    result = find_phrase_candidates(docs(prior_hits=2, recent_hits=4, prior_areas=("a", "b")),
                                    WINDOWS, policy=POLICY)
    assert result["rows"] == []


def test_unassigned_works_are_kept_but_not_an_independent_area():
    rows = docs()
    rows[12]["title"] = "photonic transducer"
    result = find_phrase_candidates(rows, WINDOWS, policy=POLICY)
    row = result["rows"][0]
    assert row["recent_works"] == 3
    assert row["recent_active_areas"] == 2
    assert any(not work["area_ids"] for work in row["evidence"])


def test_recent_and_cumulative_area_series_are_different():
    rows = docs()
    for row in rows:
        if row["window"] == "2021":
            row["window"] = "2022"
            row["id"] = row["id"].replace("2021", "2022")
    for number in range(10):
        rows.append({"id": f"2021-{number}", "window": "2021",
                     "title": "photonic transducer" if number < 2 else "background material",
                     "abstract": "", "areas": ["c", "d"][number:number + 1] if number < 2 else []})
    # Pool two recent windows so endpoint decline is visible in the series.
    prior = [dict(row, id=row["id"] + "-old", window="2019")
             for row in rows if row["window"] == "2020"]
    result = find_phrase_candidates(prior + rows, ["2019", "2020", "2021", "2022"],
                                    policy=replace(POLICY, recent_windows=2))
    series = result["rows"][0]["series"]
    assert series[-1]["active_areas"] == 2
    assert series[-1]["cumulative_areas"] == 4


def test_technical_terms_survive_and_phrases_do_not_bridge_stopwords():
    assert "graph convolution" in text_phrases("graph convolution", POLICY)
    assert "protein convolution" not in text_phrases("protein and convolution", POLICY)
    assert "protein convolution" not in text_phrases("protein. convolution", POLICY)
    assert "graph convolutional network" in text_phrases("graph-convolutional network", POLICY)


def test_order_and_composition_do_not_depend_on_input_order():
    rows = docs()
    rows[10]["title"] = "photonic transducer optical resonator"
    rows[11]["title"] = "photonic transducer optical resonator"
    result = find_phrase_candidates(rows, WINDOWS, policy=POLICY)
    assert result == find_phrase_candidates(list(reversed(rows)), WINDOWS, policy=POLICY)
    assert result["candidate_compositions"] == 1
    assert result["rows"][0]["cooccurring_phrases_same_works"]
    assert result["rows"][0]["one_technical_line_verified"] is None


def test_common_high_share_term_is_not_a_rare_candidate():
    assert find_phrase_candidates(docs(recent_hits=6), WINDOWS, policy=POLICY)["rows"] == []


def test_priority_serialization_tolerates_libm_last_bit_difference(monkeypatch):
    baseline = find_phrase_candidates(docs(), WINDOWS, policy=POLICY)
    original = math.log1p
    monkeypatch.setattr(diffusion.math, "log1p", lambda value: original(value) + 1e-16)
    assert baseline == find_phrase_candidates(docs(), WINDOWS, policy=POLICY)


def test_limits_bad_windows_and_missing_families_fail_explicitly():
    with pytest.raises(ValueError, match="resource limit"):
        find_phrase_candidates(docs(), WINDOWS, policy=replace(POLICY, max_unique_phrases=1))
    with pytest.raises(ValueError, match="comparison windows"):
        find_phrase_candidates(docs(), list(reversed(WINDOWS)), policy=POLICY)
    with pytest.raises(ValueError, match="Missing area"):
        find_phrase_candidates(docs(), WINDOWS, policy=POLICY, area_families={"a": "a"})
    with pytest.raises(ValueError, match="outside frozen"):
        lineage_families({"a"}, [("a", "b")])
    with pytest.raises(ValueError, match="Positive integer"):
        find_phrase_candidates(docs(), WINDOWS, policy=replace(POLICY, min_recent_works=True))


def test_optional_plural_mode_merges_surface_forms_with_original_quotes():
    rows = docs()
    rows[10]["title"] = "photonic transducer"
    rows[11]["title"] = "photonic transducers"
    assert find_phrase_candidates(rows, WINDOWS, policy=POLICY)["rows"] == []
    normalized = find_phrase_candidates(rows, WINDOWS, policy=POLICY,
                                         phrase_mode="source-local-regular-plural-v1")
    row = normalized["rows"][0]
    assert row["phrase"] == "photonic transducer"
    assert row["recent_works"] == 2
    assert row["surface_forms"] == ["photonic transducer", "photonic transducers"]
    assert row["weak_signal_verified"] is None


def test_acronym_title_can_be_supported_by_own_abstract_not_other_work():
    rows = docs()
    rows[10].update(title="PT", abstract="photonic transducer (PT) for particles")
    rows[11].update(title="photonic transducer", abstract="")
    row = find_phrase_candidates(rows, WINDOWS, policy=POLICY,
                                 phrase_mode="source-local-regular-plural-v1")["rows"][0]
    assert row["recent_works"] == 2
    evidence = next(item for item in row["evidence"] if item["work_id"] == "2021-0")
    assert evidence["phrase_in_title"] is True
    assert any(span["basis"] == "source_defined_acronym" and span["field"] == "title"
               for span in evidence["source_phrase_spans"])
    rows[10].update(abstract="")
    assert find_phrase_candidates(rows, WINDOWS, policy=POLICY,
                                  phrase_mode="source-local-regular-plural-v1")["rows"] == []


def test_unsupported_normalization_mode_does_not_silently_fall_back():
    with pytest.raises(ValueError, match="Unsupported phrase"):
        find_phrase_candidates(docs(), WINDOWS, policy=POLICY, phrase_mode="invent-synonyms")
