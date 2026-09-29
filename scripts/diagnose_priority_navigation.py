"""Bounded RU navigation check; vector suggestions never constrain search."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.controlled_collection import sha256_file
from saia.embed import OllamaEmbedder
from saia.priority_catalog_vectors import nearest_areas
from saia.query_planning import preview
from saia.query_translation_pilot import _model_digest, LOCAL_API


QUERIES = (
    ("inside_current_lexicon", "малые модульные реакторы"),
    ("inside_technology_but_outside_current_lexicon", "нейроморфные чипы для граничных устройств"),
    ("outside_priority_area_list", "технологии реставрации средневековых фресок"),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Navigation diagnostic output exists")
    manifest = json.loads((args.index / "manifest.json").read_text(encoding="utf-8"))
    model = manifest["model"]
    digest = _model_digest(LOCAL_API, model)
    if not digest or digest != manifest["model_digest"]:
        raise ValueError("Installed query model differs from stored catalog vectors")
    embedder = OllamaEmbedder(model=model, url=LOCAL_API)
    rows = []
    for role, query in QUERIES:
        plan = preview(query)
        started = perf_counter()
        vector = embedder.embed([query])[0]
        embed_seconds = perf_counter() - started
        started = perf_counter()
        nearest = nearest_areas(index_dir=args.index, query_vector=vector,
                                query_model_digest=digest, top_k=5)
        index_seconds = perf_counter() - started
        rows.append({"role": role, "query_ru": query,
                     "current_preview_status": plan["status"],
                     "current_preview_suggestions": len(plan["suggestions"]),
                     "query_embedding_seconds": embed_seconds,
                     "index_lookup_seconds": index_seconds,
                     "nearest_area_suggestions_unreviewed": nearest})
    report = {"version": "priority-navigation-diagnostic-v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "index_manifest_sha256": sha256_file(args.index / "manifest.json"),
              "model_digest": digest, "cases": rows,
              "policy": {"nearest_areas_auto_selected": False,
                         "original_user_query_preserved": True,
                         "no_map_match_does_not_block_general_route": True,
                         "cosine_similarity_is_not_relevance_probability": True,
                         "three_case_latency_not_service_sla": True}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({row["role"]: {"preview": row["current_preview_status"],
                                      "top_area": row["nearest_area_suggestions_unreviewed"][0]["catalog_id"],
                                      "embedding_seconds": row["query_embedding_seconds"],
                                      "lookup_seconds": row["index_lookup_seconds"]}
                      for row in rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
