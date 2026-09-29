from saia.retrieval_benchmark import (development_targets, select_automatic_terms,
                                      wilson_interval)


def test_automatic_terms_are_selected_by_support_without_nested_duplicates():
    terms, decisions = select_automatic_terms("machine learning", [
        {"phrase": "machine learning algorithms", "document_support": 110},
        {"phrase": "learning algorithms", "document_support": 164},
        {"phrase": "learning techniques", "document_support": 88},
        {"phrase": "neural networks", "document_support": 79},
        {"phrase": "representation learning", "document_support": 999},
    ], 3)
    assert terms == ["machine learning", "representation learning",
                     "learning algorithms", "learning techniques"]
    nested = next(item for item in decisions
                  if item["phrase"] == "machine learning algorithms")
    assert nested["selected"] is False


def test_development_targets_exclude_reserved_and_observation_negatives():
    catalog = {"cases": [
        {"case_id": "dev", "family_id": "a", "split": "development",
         "kind": "research_line_positive_proposal", "title": "A",
         "as_of_date": "2017-01-01", "arxiv_ids": ["1601.00001"]},
        {"case_id": "negative", "family_id": "a", "split": "development",
         "kind": "empirical_observation_negative", "title": "duplicate",
         "as_of_date": "2017-01-01", "arxiv_ids": ["1601.00001"]},
        {"case_id": "reserved", "family_id": "b", "split": "reserved",
         "kind": "ambiguous_line", "title": "B",
         "as_of_date": "2017-01-01", "arxiv_ids": ["1602.00001"]},
        {"case_id": "later", "family_id": "c", "split": "development",
         "kind": "ambiguous_line", "title": "C",
         "as_of_date": "2020-01-01", "arxiv_ids": ["1801.00001"]},
    ]}
    cases, identifiers = development_targets(catalog, "2017-01-01")
    assert [item["case_id"] for item in cases] == ["dev"]
    assert identifiers == {"1601.00001"}


def test_wilson_interval_exposes_small_sample_uncertainty():
    assert wilson_interval(5, 16) == [0.1416, 0.556]
    assert wilson_interval(0, 0) is None
