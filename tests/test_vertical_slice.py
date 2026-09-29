import builtins
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from saia.benchmark import (
    benchmark_targets,
    cluster_signature,
    identifier_url,
    interpret_target_result,
    recovery_metrics,
)
from saia.candidates import confirmation_independence, percentile
from saia.cluster import cluster_window, graph_cluster_window
from saia.embed import HashingNgramEmbedder
from saia.ingest import arxiv_snapshot_records
from saia.normalize import parse_openalex
from saia.quality import decide


def test_hashing_embedding_is_deterministic_and_normalized():
    embedder = HashingNgramEmbedder(dim=64)
    first = np.asarray(embedder.embed(["Graph convolution\n\nGraph learning"])[0])
    second = np.asarray(embedder.embed(["Graph convolution\n\nGraph learning"])[0])
    assert np.allclose(first, second)
    assert np.isclose(np.linalg.norm(first), 1.0)


def test_graph_backend_can_leave_noise_unassigned():
    vectors = np.asarray([[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]])
    labels, _ = graph_cluster_window(
        ["alpha topic", "alpha method", "unrelated beta"],
        vectors, min_topic_size=2, similarity_threshold=0.8,
    )
    assert labels.tolist()[:2] == [0, 0]
    assert labels.tolist()[2] == -1


@pytest.fixture
def without_optional_clustering_dependencies(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"bertopic", "hdbscan", "sklearn", "umap"}:
            raise ModuleNotFoundError(f"Optional clustering dependency unavailable: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


def test_bertopic_small_window_uses_deterministic_similarity_fallback(
    without_optional_clustering_dependencies,
):
    labels, _ = cluster_window(
        ["same emerging method", "same emerging method"],
        np.asarray([[1.0, 0.0], [0.99, 0.01]]),
        min_topic_size=2,
        seed=42,
        umap_cfg={"n_neighbors": 15, "n_components": 5, "metric": "cosine"},
        hdbscan_cfg={"metric": "euclidean"},
        small_window_similarity_threshold=0.7,
    )
    assert labels.tolist() == [0, 0]


@pytest.mark.parametrize("size", [0, 1])
def test_undersized_window_needs_no_optional_clustering_dependencies(
    size, without_optional_clustering_dependencies,
):
    labels, terms = cluster_window(
        ["emerging method"] * size,
        np.ones((size, 2)),
        min_topic_size=2,
        seed=42,
        umap_cfg={},
        hdbscan_cfg={},
        small_window_similarity_threshold=0.7,
    )
    assert labels.tolist() == [-1] * size
    assert terms == {}


def test_percentile_is_withheld_for_too_few_peer_topics():
    assert percentile(0.8, [0.2, 0.8]) is None
    assert percentile(0.8, [0.1, 0.2, 0.3, 0.4, 0.8]) == 90.0


def test_equal_values_are_midrank_not_all_top_percentile():
    assert percentile(0.0, [0.0] * 6) == 50.0
    assert percentile(0.0, [0.0] * 5 + [0.2]) == 41.67


def test_linked_openalex_enrichment_is_not_independent_confirmation():
    linked = confirmation_independence(10, 10, True, False)
    independent = confirmation_independence(10, 10, True, True)
    assert linked == 0.45
    assert independent == 1.0


def test_blind_target_requires_coherent_topic_not_just_any_assignment():
    scattered = recovery_metrics(
        [11, 12, 13], 3, 3, min_same_topic=2, min_recall=0.5,
        topic_sizes={11: 10, 12: 10, 13: 10},
    )
    coherent = recovery_metrics(
        [11, 11, None], 3, 2, min_same_topic=2, min_recall=0.5,
        topic_sizes={11: 10},
    )
    assert scattered["eligible_target_recall"] == 1.0
    assert scattered["detected_as_distinct_topic"] is False
    assert coherent["detected_as_distinct_topic"] is True


def test_target_recall_must_be_in_dominant_topic_not_scattered_anywhere():
    result = recovery_metrics([11, 11, 12, 13, 14, 15], 6, 6,
                              min_same_topic=2, min_recall=0.5,
                              topic_sizes={11: 4}, min_target_share=0.1)
    assert result['eligible_target_recall'] == 1.0
    assert result['dominant_topic_target_recall'] == 0.3333
    assert result['detected_as_distinct_topic'] is False


def test_benchmark_targets_support_doi_and_legacy_arxiv_ids():
    assert benchmark_targets({"target_arxiv_ids": ["1609.02907"]}) == [
        {"kind": "arxiv", "value": "1609.02907"}
    ]
    assert benchmark_targets({"target_identifiers": [
        {"kind": "DOI", "value": "10.1038/Example"}
    ]}) == [{"kind": "doi", "value": "10.1038/example"}]
    assert identifier_url("doi", "10.1038/example") == "https://doi.org/10.1038/example"


def test_benchmark_explains_collection_coverage_before_clustering_failure():
    rows = {
        "arxiv:a": {"found_in_corpus": True},
        "arxiv:b": {"found_in_corpus": False},
    }
    recovery = {"detected_as_distinct_topic": False}
    result = interpret_target_result(rows, recovery, "Контрольная тема")
    assert result["failure_stage"] == "collection_coverage"
    assert result["collection_coverage"] == {
        "control_publications": 2,
        "found_in_corpus": 1,
        "missing_from_corpus": 1,
        "control_set_recall": 0.5,
    }
    assert "охват корпуса" in result["conclusion"]


def test_benchmark_reports_topic_failure_only_when_all_controls_are_collected():
    rows = {
        "arxiv:a": {"found_in_corpus": True},
        "arxiv:b": {"found_in_corpus": True},
    }
    result = interpret_target_result(
        rows, {"detected_as_distinct_topic": False}, "Контрольная тема"
    )
    assert result["failure_stage"] == "topic_recovery"
    assert "присутствуют в корпусе" in result["conclusion"]


def test_openalex_arxiv_doi_is_also_indexed_as_arxiv_identifier():
    parsed = parse_openalex({
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.48550/arXiv.2605.16786",
        "display_name": "Example",
    })
    assert ("arxiv", "2605.16786") in parsed["identifiers"]


def test_cluster_signature_ignores_database_topic_ids_but_not_membership():
    first = [{"first_window": "2016", "last_window": "2016",
              "memberships": [["2016", 1]], "snapshots": []}]
    same = [{"last_window": "2016", "first_window": "2016",
             "memberships": [["2016", 1]], "snapshots": []}]
    changed = [{"first_window": "2016", "last_window": "2016",
                "memberships": [["2016", 2]], "snapshots": []}]
    assert cluster_signature(first) == cluster_signature(same)
    assert cluster_signature(first) != cluster_signature(changed)


def test_quality_excludes_abstract_only_query_match():
    result = decide(
        title="Unrelated clinical editorial",
        abstract="We introduce a graph neural network.",
        publication_year=2015,
        date_is_imprecise=False,
        terms=["graph neural network"],
        sources={"openalex"},
        openalex_payloads=[],
        relevance_mode='strict_title',
    )
    assert result.decision == "exclude"
    assert result.flags["abstract_only_query_match"] is True


def test_quality_flags_late_indexing_without_claiming_false_date():
    payload = {
        "created_date": "2025-01-01",
        "counts_by_year": [],
        "primary_location": {
            "source": {"type": "repository", "display_name": "Zenodo"}
        },
    }
    result = decide(
        title="Graph Neural Network for Fraud",
        abstract="Graph neural network method.",
        publication_year=2011,
        date_is_imprecise=False,
        terms=["graph neural network"],
        sources={"openalex"},
        openalex_payloads=[payload],
    )
    assert result.decision == "include"
    assert result.flags["repository_backdate"] is True


def test_category_corpus_does_not_require_known_trend_terms():
    result = decide(
        title="A previously unknown representation method",
        abstract="No query phrase is present here.",
        publication_year=2016,
        date_is_imprecise=False,
        terms=["machine learning", "deep learning"],
        categories=["cs.LG", "stat.ML"],
        sources={"arxiv"},
        openalex_payloads=[],
        arxiv_payloads=[{"categories": ["cs.LG"], "created": "2016-03-01",
                         "updated": "2016-03-01"}],
        as_of_date="2017-01-01",
    )
    assert result.decision == "include"
    assert result.flags["matched_categories"] == "cs.LG"
    assert result.matched_terms == ()


def test_late_arxiv_deposit_does_not_create_false_freshness():
    result = decide(
        title="Drug carrying microspheres for bone tissue engineering",
        abstract="Microspheres for bone tissue engineering.",
        publication_year=2026, date_is_imprecise=False,
        terms=["bone tissue engineering"], sources={"arxiv"},
        openalex_payloads=[],
        arxiv_payloads=[{
            "id": "2604.16454", "created": "2026-04-20", "updated": "2026-04-20",
            "arxiv_journal_ref": "Ceramics International 46 (2020) 123-130",
        }],
    )
    assert result.decision == "quarantine"
    assert result.flags["research_freshness_unverified"] is True
    assert result.flags["late_arxiv_deposit_evidence"][0]["journal_ref_earliest_year"] == 2020


def test_recent_journal_ref_does_not_quarantine_arxiv_work():
    result = decide(
        title="A new tissue engineering method",
        abstract="Tissue engineering method.",
        publication_year=2026, date_is_imprecise=False,
        terms=["tissue engineering"], sources={"arxiv"},
        openalex_payloads=[],
        arxiv_payloads=[{
            "id": "2604.16456", "created": "2026-04-20", "updated": "2026-04-20",
            "arxiv_journal_ref": "Biomedical Materials 21 (2025) 123-130",
        }],
    )
    assert result.decision == "include"
    assert "late_arxiv_deposit_evidence" not in result.flags


def test_imprecise_year_only_date_after_cutoff_is_not_counted():
    result = decide(
        title="Precision fermentation of milk components",
        abstract="Precision fermentation for milk proteins.",
        publication_year=2026, date_is_imprecise=True,
        terms=["precision fermentation"], sources={"openalex"},
        openalex_payloads=[], as_of_date="2026-09-01",
        effective_date="2026-12-31",
    )
    assert result.decision == "exclude"
    assert result.flags["outside_analysis_cutoff"] is True


def test_snapshot_revision_after_cutoff_is_quarantined():
    result = decide(
        title="Historical paper with a later revision",
        abstract="The current abstract may contain future knowledge.",
        publication_year=2016,
        date_is_imprecise=False,
        terms=[], categories=["cs.LG"], sources={"arxiv"},
        openalex_payloads=[],
        arxiv_payloads=[{
            "categories": ["cs.LG"], "created": "2016-03-01",
            "updated": "2017-02-01",
            "_source_format": "hf-arxiv-parquet-snapshot",
        }],
        as_of_date="2017-01-01",
    )
    assert result.decision == "quarantine"
    assert result.flags["content_revision_after_cutoff"] is True


def test_parquet_snapshot_reader_preserves_first_and_revision_dates(tmp_path):
    pyarrow = __import__("pyarrow")
    parquet = __import__("pyarrow.parquet", fromlist=["write_table"])
    path = tmp_path / "cs.LG_2016_01.parquet"
    parquet.write_table(pyarrow.Table.from_pylist([{
        "arxiv_id": "1601.00001",
        "title": "Blind topic candidate",
        "authors": ["Ada Researcher"],
        "submission_date": "3 Jan 2016, last revised 4 Feb 2017",
        "comments": "",
        "primary_subject": "Machine Learning (cs.LG)",
        "subjects": "Machine Learning (cs.LG); Artificial Intelligence (cs.AI)",
        "doi": "https://doi.org/10.48550/arXiv.1601.00001",
        "abstract": "Evidence text",
        "file_path": "cs-16.zip/1601.00001.pdf",
    }]), path)
    identifier, row = next(arxiv_snapshot_records(path))
    assert identifier == "1601.00001"
    assert row["created"] == "2016-01-03"
    assert row["updated"] == "2017-02-04"
    assert row["categories"] == ["cs.LG", "cs.AI"]


def test_linked_arxiv_ids_do_not_use_future_openalex_rows(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "saia_fetch", Path(__file__).parents[1] / "scripts" / "fetch.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    payload = {
        "results": [
            {
                "publication_date": "2016-09-09",
                "ids": {"doi": "https://doi.org/10.48550/arxiv.1609.02907"},
                "locations": [],
            },
            {
                "publication_date": "2017-10-30",
                "ids": {"doi": "https://doi.org/10.48550/arxiv.1710.10903"},
                "locations": [],
            },
        ]
    }
    (tmp_path / "page_0001.json").write_text(json.dumps(payload), encoding="utf-8")
    mission = {"as_of_date": "2017-01-01"}
    assert module.openalex_linked_arxiv_ids(mission, tmp_path) == ["1609.02907"]
