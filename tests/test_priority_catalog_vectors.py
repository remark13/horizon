from pathlib import Path

import pytest

from saia.priority_catalog_vectors import build, nearest_areas, refine_with_area_hints


class _FakeEmbedder:
    def embed(self, texts):
        return [[float(len(text) % 101 + 1), float(sum(map(ord, text)) % 97 + 1)]
                for text in texts]


def test_catalog_vectors_are_immutable_navigation_candidates(tmp_path: Path):
    catalog = Path(__file__).resolve().parents[1] / "data/reference/priority_catalog/v1/catalog.json"
    output = tmp_path / "catalog-vectors"
    manifest = build(catalog_path=catalog, output_dir=output,
                     embedder=_FakeEmbedder(), model_digest="test-digest")
    assert manifest["counts"] == {"national_search_areas": 89,
                                  "customer_examples": 100,
                                  "unreviewed_mapping_candidates": 300}
    assert manifest["embedding_dimension"] == 2
    candidates = nearest_areas(index_dir=output, query_vector=[1.0, 1.0],
                               query_model_digest="test-digest", top_k=3)
    assert len(candidates) == 3
    assert all(item["role"] == "navigation_suggestion_not_a_signal_or_filter"
               for item in candidates)
    with pytest.raises(FileExistsError):
        build(catalog_path=catalog, output_dir=output,
              embedder=_FakeEmbedder(), model_digest="test-digest")
    refined = tmp_path / "refined-vectors"
    new_manifest = refine_with_area_hints(catalog_path=catalog,
                                          base_index_dir=output, output_dir=refined)
    assert new_manifest["counts"]["unreviewed_mapping_candidates"] == 300
    import pyarrow.parquet as pq
    rows = pq.read_table(refined / "mapping_candidates.parquet").to_pylist()
    customer = next(row for row in rows if row["customer_id"] == "customer-signal-001")
    assert customer["mapping_status"] == "direction_hint_and_model_candidate_unreviewed"
    assert customer["national_area_id"] not in {"national-area-014"}
    assert len(nearest_areas(index_dir=refined, query_vector=[1.0, 1.0],
                             query_model_digest="test-digest", top_k=2)) == 2
    with pytest.raises(ValueError, match="incompatible"):
        nearest_areas(index_dir=refined, query_vector=[1.0, 1.0],
                      query_model_digest="other-model", top_k=2)
