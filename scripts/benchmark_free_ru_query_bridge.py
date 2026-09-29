"""Immutable local-model RU→EN retrieval diagnostic on a frozen query set.

This does not approve or execute model phrases in the production user route.
Exact match counts do not establish semantic relevance or signal quality.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION
from saia.arxiv_trigram_index import exact_search, supports_plan
from saia.controlled_collection import sha256_file
from saia.query_translation_pilot import LOCAL_API, SCHEMA, _model_digest, _post, _validated_response


def benchmark(*, cases_path: Path, index_dir: Path, model: str, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("Benchmark output is immutable")
    specification = json.loads(cases_path.read_text(encoding="utf-8"))
    if specification.get("version") != "free-ru-query-bridge-pilot-v1" or not specification.get("frozen_before_run"):
        raise ValueError("Unexpected or unfrozen query set")
    digest = _model_digest(LOCAL_API, model)
    if not digest:
        raise ValueError("Selected local model is not installed")
    rows = []
    for case in specification["cases"]:
        query = case["query_ru"]
        prompt = (
            "Translate the Russian technology query faithfully for English scientific "
            "title/abstract retrieval. Preserve all distinctive concepts, including the "
            "intended technical sense of Russian terms. Return a concise full translation "
            "and 1–3 contiguous English noun phrases. Do not add or remove technologies, "
            "materials, or applications. If a term is ambiguous, explain it in uncertainty. "
            "Output JSON only.\nRussian query: " + query
        )
        started = perf_counter()
        raw = _post(LOCAL_API + "/api/generate", {
            "model": model, "prompt": prompt, "format": SCHEMA, "stream": False,
            "think": False, "options": {"temperature": 0, "num_predict": 180},
            "keep_alive": "5m",
        }, timeout=120)
        translation_seconds = perf_counter() - started
        proposal = _validated_response(raw)
        phrases = list(dict.fromkeys([query, case["reference_en"],
                                      proposal["translation_en"], *proposal["core_phrases_en"]]))
        observations = []
        for phrase in phrases:
            plan = {"included_terms": [phrase], "exclusions": [],
                    "date_from": specification["date_from"],
                    "as_of_date": specification["as_of_date"],
                    "matching_version": ORTHOGRAPHIC_MATCHING_VERSION}
            supported = supports_plan(plan)
            if supported:
                search_started = perf_counter()
                result = exact_search(index_dir, plan)
                audit = result["audit"]
                observations.append({"phrase": phrase, "supported": True,
                                     "exact_unique_ids": audit["exact_unique_ids"],
                                     "search_seconds": perf_counter() - search_started})
            else:
                observations.append({"phrase": phrase, "supported": False,
                                     "exact_unique_ids": None})
        rows.append({"role": case["role"], "query_ru": query,
                     "reference_en": case["reference_en"],
                     "proposal_unreviewed": proposal,
                     "translation_seconds": translation_seconds,
                     "observations": observations})
    report = {"version": "free-ru-query-bridge-benchmark-v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "case_set_sha256": sha256_file(cases_path),
              "index_manifest_sha256": sha256_file(index_dir / "manifest.json"),
              "model": model, "model_digest": digest, "cases": rows,
              "limitations": [
                  "Reference English phrases are hand-authored navigation anchors, not a gold standard.",
                  "Exact match counts do not measure relevance or recall.",
                  "Model phrases remain unreviewed and are not executed in production.",
              ]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                      encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = benchmark(cases_path=args.cases, index_dir=args.index,
                       model=args.model, output=args.output)
    print(json.dumps({"output": str(args.output), "model": result["model"],
                      "cases": len(result["cases"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
