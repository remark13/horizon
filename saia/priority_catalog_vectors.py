"""Small RU semantic-navigation index over 89 areas and 100 client examples.

Nearest neighbours are candidate links only. They are not relevance labels,
search-plan approvals, or weak-signal detections.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

from saia.controlled_collection import sha256_file
from saia.embed import OllamaEmbedder
from saia.priority_arxiv_full import MIN_FREE_BYTES
from saia.query_translation_pilot import _model_digest, LOCAL_API


VERSION = "priority-catalog-ru-vectors-v1"
REFINED_VERSION = "priority-catalog-ru-vectors-v2"
MODEL = "bge-m3:latest"
MAX_OUTPUT_BYTES = 16 * 1024 * 1024


def _rows(catalog: dict) -> list[dict]:
    if (catalog.get("counts", {}).get("national_search_areas") != 89
            or catalog.get("counts", {}).get("customer_examples") != 100):
        raise ValueError("Priority catalog is not the frozen 89+100 source")
    rows = []
    for item in catalog["national_search_areas"]:
        rows.append({"catalog_id": item["id"], "role": "national_search_area_not_signal",
                     "direction_id": item["direction_id"],
                     "text_ru": item["title_ru"],
                     "embedding_input": item["title_ru"] + ". " + item["query_draft"]["ru"]})
    for item in catalog["customer_examples"]:
        rows.append({"catalog_id": item["id"], "role": "customer_supplied_example_not_gold",
                     "direction_id": None, "text_ru": item["title_original"],
                     "embedding_input": item["title_original"] + ". " + item["client_area_ru"]})
    if len({row["catalog_id"] for row in rows}) != 189:
        raise ValueError("Priority catalog identifiers are not unique")
    return rows


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def build(*, catalog_path: Path, output_dir: Path,
          embedder: OllamaEmbedder | None = None,
          model_digest: str | None = None, batch_size: int = 16) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if output_dir.exists():
        raise FileExistsError("Priority vector index is immutable")
    if not 1 <= batch_size <= 32:
        raise ValueError("Vector batch size must be 1–32")
    if shutil.disk_usage(output_dir.parent).free < MIN_FREE_BYTES:
        raise OSError("Insufficient free disk reserve")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = _rows(catalog)
    embedder = embedder or OllamaEmbedder(model=MODEL, url=LOCAL_API)
    digest = model_digest or _model_digest(LOCAL_API, MODEL)
    if not digest:
        raise ValueError("Pinned local BGE-M3 model digest is unavailable")
    vectors = []
    dimension = None
    for offset in range(0, len(rows), batch_size):
        batch = embedder.embed([row["embedding_input"] for row in rows[offset:offset + batch_size]])
        if len(batch) != len(rows[offset:offset + batch_size]):
            raise ValueError("Embedding batch response size mismatch")
        for vector in batch:
            if dimension is None:
                dimension = len(vector)
            if (len(vector) != dimension or dimension < 2
                    or any(not math.isfinite(float(value)) for value in vector)
                    or math.isclose(sum(float(value) ** 2 for value in vector), 0.0)):
                raise ValueError("Invalid or inconsistent catalog embedding")
            vectors.append([float(value) for value in vector])
    for row, vector in zip(rows, vectors):
        row["embedding"] = vector
    areas = [row for row in rows if row["role"] == "national_search_area_not_signal"]
    examples = [row for row in rows if row["role"] == "customer_supplied_example_not_gold"]
    links = []
    for example in examples:
        nearest = sorted(
            ((area["catalog_id"], _cosine(example["embedding"], area["embedding"]))
             for area in areas), key=lambda pair: (-pair[1], pair[0]))[:3]
        links.extend({"customer_id": example["catalog_id"], "national_area_id": area_id,
                      "rank": rank, "semantic_similarity": score,
                      "mapping_status": "model_candidate_unreviewed_not_authoritative"}
                     for rank, (area_id, score) in enumerate(nearest, 1))
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=output_dir.name + ".tmp-", dir=output_dir.parent) as name:
        temp = Path(name)
        vector_path, links_path = temp / "catalog_vectors.parquet", temp / "mapping_candidates.parquet"
        pq.write_table(pa.Table.from_pylist(rows), vector_path, compression="zstd")
        pq.write_table(pa.Table.from_pylist(links), links_path, compression="zstd")
        if (vector_path.stat().st_size + links_path.stat().st_size > MAX_OUTPUT_BYTES
                or shutil.disk_usage(temp).free < MIN_FREE_BYTES):
            raise OSError("Priority vector index exceeded size cap or reserve")
        if pq.read_metadata(vector_path).num_rows != 189 or pq.read_metadata(links_path).num_rows != 300:
            raise ValueError("Priority vector index row count mismatch")
        result = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                  "catalog_sha256": sha256_file(catalog_path),
                  "model": MODEL, "model_digest": digest, "embedding_dimension": dimension,
                  "embedding_policy": {"input_text": "source Russian title and draft query/client area",
                                       "one_embedding_per_row_and_model_version": True,
                                       "similarity": "cosine; not calibrated probability",
                                       "nearest_area_links_are_unreviewed": True,
                                       "unknown_user_query_must_keep_general_route": True},
                  "counts": {"national_search_areas": len(areas),
                             "customer_examples": len(examples),
                             "unreviewed_mapping_candidates": len(links)},
                  "files": {path.name: {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
                            for path in (vector_path, links_path)},
                  "limits": {"max_output_bytes": MAX_OUTPUT_BYTES,
                             "min_free_disk_reserve_bytes": MIN_FREE_BYTES}}
        (temp / "manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temp, output_dir)
    return result


def nearest_areas(*, index_dir: Path, query_vector: list[float],
                  query_model_digest: str, top_k: int = 5) -> list[dict]:
    import pyarrow.parquet as pq

    if not 1 <= top_k <= 10:
        raise ValueError("Invalid area-suggestion limit")
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("version") not in {VERSION, REFINED_VERSION}
            or len(query_vector) != manifest["embedding_dimension"]
            or query_model_digest != manifest["model_digest"]):
        raise ValueError("Query embedding is incompatible with the priority index")
    path = index_dir / "catalog_vectors.parquet"
    if sha256_file(path) != manifest["files"][path.name]["sha256"]:
        raise ValueError("Priority vector index checksum mismatch")
    rows = pq.read_table(path, columns=["catalog_id", "role", "direction_id",
                                       "text_ru", "embedding"]).to_pylist()
    areas = [row for row in rows if row["role"] == "national_search_area_not_signal"]
    ranked = sorted(areas, key=lambda row: (-_cosine(query_vector, row["embedding"]),
                                            row["catalog_id"]))[:top_k]
    return [{"catalog_id": row["catalog_id"], "direction_id": row["direction_id"],
             "title_ru": row["text_ru"],
             "semantic_similarity": _cosine(query_vector, row["embedding"]),
             "role": "navigation_suggestion_not_a_signal_or_filter"} for row in ranked]


def refine_with_area_hints(*, catalog_path: Path, base_index_dir: Path,
                           output_dir: Path) -> dict:
    """Constrain example→area suggestions to the client's *draft* direction hints.

    No candidate becomes an approved map. A topic with no plausible area may
    remain unmapped in human review; this function must not force acceptance.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    if output_dir.exists():
        raise FileExistsError("Refined priority map is immutable")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    base = json.loads((base_index_dir / "manifest.json").read_text(encoding="utf-8"))
    vectors_path = base_index_dir / "catalog_vectors.parquet"
    if (base.get("version") != VERSION
            or base.get("catalog_sha256") != sha256_file(catalog_path)
            or sha256_file(vectors_path) != base["files"]["catalog_vectors.parquet"]["sha256"]):
        raise ValueError("Base vectors and source catalog differ")
    rows = {item["catalog_id"]: item for item in pq.read_table(vectors_path).to_pylist()}
    if len(rows) != 189:
        raise ValueError("Base vector catalog is incomplete")
    area_directions = {item["id"]: item["direction_id"]
                       for item in catalog["national_search_areas"]}
    client_hints = {item["id"]: set(item["direction_hints"])
                    for item in catalog["client_search_areas"]}
    links = []
    for customer in catalog["customer_examples"]:
        example = rows[customer["id"]]
        allowed = client_hints[customer["client_area_id"]]
        choices = [(area_id, _cosine(example["embedding"], rows[area_id]["embedding"]))
                   for area_id, direction_id in area_directions.items() if direction_id in allowed]
        for rank, (area_id, score) in enumerate(
                sorted(choices, key=lambda pair: (-pair[1], pair[0]))[:3], 1):
            links.append({"customer_id": customer["id"], "national_area_id": area_id,
                          "rank": rank, "semantic_similarity": score,
                          "allowed_direction_ids": sorted(allowed),
                          "mapping_status": "direction_hint_and_model_candidate_unreviewed"})
    if len(links) != 300:
        raise ValueError("Refined mapping candidate count mismatch")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=output_dir.name + ".tmp-", dir=output_dir.parent) as name:
        temp = Path(name)
        os.link(vectors_path, temp / vectors_path.name)
        links_path = temp / "mapping_candidates.parquet"
        pq.write_table(pa.Table.from_pylist(links), links_path, compression="zstd")
        result = {"version": REFINED_VERSION,
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "catalog_sha256": sha256_file(catalog_path),
                  "base_index_manifest_sha256": sha256_file(base_index_dir / "manifest.json"),
                  "model": base["model"], "model_digest": base["model_digest"],
                  "embedding_dimension": base["embedding_dimension"],
                  "counts": {"national_search_areas": 89, "customer_examples": 100,
                             "unreviewed_mapping_candidates": len(links)},
                  "files": {path.name: {"bytes": path.stat().st_size,
                                        "sha256": sha256_file(path)}
                            for path in (temp / vectors_path.name, links_path)},
                  "policy": {"direction_hints_are_draft_navigation_only": True,
                             "not_automatic_customer_to_national_mapping": True,
                             "not_a_weak_signal_or_relevance_label": True,
                             "vectors_hardlinked_not_recomputed": True,
                             "free_query_remains_unrestricted": True}}
        (temp / "manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temp, output_dir)
    return result
