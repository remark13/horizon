import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia.controlled_collection import sha256_file
from saia.openalex_venue_view import enrich_triage, load_index
from saia.priority_openalex_venues import materialize


def _fixture(tmp_path):
    raw = tmp_path / "raw"
    page = raw / "mission-1" / "openalex" / "page_0001.json"
    page.parent.mkdir(parents=True)
    page.write_text(json.dumps({"results": [{
        "id": "https://openalex.org/W1", "type": "article",
        "primary_location": {"raw_type": "journal-article",
                             "raw_source_name": "C&EN Global Enterprise",
                             "source": {"id": "https://openalex.org/S1",
                                        "display_name": "C&EN Global Enterprise",
                                        "type": "journal", "is_core": False,
                                        "is_in_doaj": False}},
    }]}), encoding="utf-8")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    works = corpus / "works.parquet"
    pq.write_table(pa.Table.from_pylist([{
        "openalex_id": "W1", "source_mission_id": "mission-1",
        "source_page": "page_0001.json", "source_page_sha256": sha256_file(page),
    }]), works)
    (corpus / "manifest.json").write_text(json.dumps({
        "version": "priority-openalex-complete-cohorts-v2",
        "file": {"name": works.name, "bytes": works.stat().st_size,
                 "sha256": sha256_file(works)},
        "counts": {"unique_openalex_works": 1},
    }), encoding="utf-8")
    return raw, page, corpus


def test_venue_layer_preserves_trade_publication_without_calling_it_primary(tmp_path):
    raw, _, corpus = _fixture(tmp_path)
    out = tmp_path / "venues"
    manifest = materialize(corpus_dir=corpus, raw_root=raw, output_dir=out)
    row = pq.read_table(out / "venues.parquet").to_pylist()[0]
    assert manifest["counts"]["rows"] == 1
    assert row["primary_source_name"] == "C&EN Global Enterprise"
    assert row["primary_source_type"] == "journal"
    assert row["primary_source_is_core"] is False
    assert row["scientific_primary_result_verified"] is None
    assert manifest["policy"]["no_records_excluded"] is True
    assert load_index(out)["W1"]["primary_source_name"] == "C&EN Global Enterprise"
    with pytest.raises(FileExistsError):
        materialize(corpus_dir=corpus, raw_root=raw, output_dir=out)


def test_venue_layer_rejects_modified_source_page(tmp_path):
    raw, page, corpus = _fixture(tmp_path)
    page.write_text(page.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        materialize(corpus_dir=corpus, raw_root=raw, output_dir=tmp_path / "venues")


def test_venue_view_adds_context_without_changing_saved_ranking_or_source(tmp_path):
    raw, _, corpus = _fixture(tmp_path)
    out = tmp_path / "venues"
    materialize(corpus_dir=corpus, raw_root=raw, output_dir=out)
    packet = {"queue": [{"rank": 1, "card": {"evidence": [{
        "title": "Example", "sources": [
            {"type": "openalex", "url": "https://openalex.org/W1"},
            {"type": "doi", "url": "https://doi.org/10.1/example"},
        ]}]}}]}
    shown = enrich_triage(packet, load_index(out))
    assert shown["queue"][0]["rank"] == 1
    assert shown["queue"][0]["card"]["evidence"][0]["venue_context"] == [{
        "name": "C&EN Global Enterprise", "source_type": "journal",
        "is_core": False, "primary_result_verified": None,
    }]
    assert "venue_context" not in packet["queue"][0]["card"]["evidence"][0]
    assert shown["venue_context"]["ranking_changed"] is False
