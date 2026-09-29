from saia.balanced_merge import merge
from saia.discovery import Publication, deduplicate
from saia.preview_identity import same_publication
import json
from pathlib import Path


def work(key: str, title: str, *, doi=None, authors=(), source_id="", date="2024-01-01") -> dict:
    return {"canonical_key": key, "title": title, "doi": doi,
            "authors": authors, "source_ids": [source_id] if source_id else [],
            "published_at": date}


def test_doi_and_no_doi_same_title_author_share_preview_slot() -> None:
    oa = work("doi:10.1000/abc", "A new neural method", doi="10.1000/abc",
              authors=["Ada Smith"], source_id="https://openalex.org/W1")
    ax = work("title:a new neural method", "A New Neural Method", authors=["Ada Smith"],
              source_id="https://arxiv.org/abs/2401.00001")
    result = merge([{"branch_id": "a", "sources": {"openalex": [oa]}},
                    {"branch_id": "b", "sources": {"arxiv": [ax,
                        work("doi:10.1000/other", "Independent study", doi="10.1000/other")]}}], max_results=2)
    assert len(result["results"]) == 2
    assert result["results"][0]["source_provenance"] == ["arxiv", "openalex"]
    assert result["cross_key_duplicates_collapsed"] == 1
    assert result["results"][0]["source_ids"] == [
        "https://openalex.org/W1", "https://arxiv.org/abs/2401.00001",
    ]


def test_changed_title_needs_strong_identity() -> None:
    left = work("doi:10.1000/a", "Original title", doi="10.1000/a", authors=["Ada Smith"])
    changed = work("title:changed title", "Changed title", authors=["Ada Smith"])
    assert not same_publication(left, changed)
    changed["source_ids"] = ["https://arxiv.org/abs/2401.00001"]
    left["source_ids"] = ["https://arxiv.org/abs/2401.00001"]
    assert same_publication(left, changed)


def test_missing_authors_and_similar_distinct_works_are_not_collapsed() -> None:
    a = work("title:shared title", "Shared title", authors=(), source_id="https://arxiv.org/abs/2401.00001")
    b = work("title:shared title", "Shared title", authors=(), source_id="https://arxiv.org/abs/2401.00002")
    assert not same_publication(a, b)
    a["authors"], b["authors"] = ["Ada Smith"], ["Ben Jones"]
    assert not same_publication(a, b)
    b["authors"] = ["Ada Smith"]
    b["title"] = "Shared title: a different problem"
    assert not same_publication(a, b)


def test_different_publisher_dois_remain_distinct_even_with_same_title() -> None:
    a = work("doi:10.1000/a", "Shared title", doi="10.1000/a", authors=["Ada Smith"])
    b = work("doi:10.1000/b", "Shared title", doi="10.1000/b", authors=["Ada Smith"])
    assert not same_publication(a, b)


def test_discovery_dedup_uses_same_conservative_rule() -> None:
    def pub(key, doi, sources, sid, authors):
        return Publication(key, "A new neural method", "abstract", "2024-01-01", sources,
                           (sid,), (sid,), doi, authors)
    oa = pub("doi:10.1000/abc", "10.1000/abc", ("openalex",), "https://openalex.org/W1", ("Ada Smith",))
    ax = pub("title:a new neural method", None, ("arxiv",), "https://arxiv.org/abs/2401.00001", ("Ada Smith",))
    merged = deduplicate([oa, ax])
    assert len(merged) == 1
    assert merged[0].sources == ("arxiv", "openalex")


def test_saved_gnn_preview_regains_two_slots_before_limit() -> None:
    report_path = Path(__file__).resolve().parents[1] / "outputs/priority-identity-audit/gnn-preview-v3.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["input_counts"] == {"openalex": 62, "arxiv": 3}
    assert report["old_key_only"]["selected"] == report["conservative"]["selected"] == 15
    assert len(report["old_key_only"]["duplicate_pairs_by_new_conservative_rule"]) == 2
    assert report["conservative"]["cross_key_duplicates_collapsed"] == 2
