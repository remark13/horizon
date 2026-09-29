"""Bounded retrieval coverage check over an existing arXiv snapshot cache.

This never assigns a weak-signal label or publication-share growth. The cache
is a preselected subset of the full arXiv mirror, so its counts have no valid
field denominator. Current snapshot abstracts may postdate first submission.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re


SUPPORTED_VERSIONS = {"priority-arxiv-pilot-v1", "priority-arxiv-pilot-v2"}


def _boundary_pattern(term: str) -> str:
    """RE2-compatible word boundaries; CAR-T must not match car-to-cloud."""
    return r"(?:^|\W)" + re.escape(term) + r"(?:$|\W)"


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validated_cases(config: dict, catalog: dict) -> list[dict]:
    if config.get("version") not in SUPPORTED_VERSIONS:
        raise ValueError("Unknown pilot version")
    ids = {item["id"] for item in catalog["national_search_areas"] + catalog["customer_examples"]}
    cases = config.get("cases") or []
    if len(cases) != 16 or len({case["id"] for case in cases}) != 16:
        raise ValueError("Pilot must contain 16 distinct cases")
    if not {case["id"] for case in cases} <= ids:
        raise ValueError("Pilot contains an unknown source catalog ID")
    for case in cases:
        terms = case.get("all_terms")
        if not isinstance(terms, list) or not 1 <= len(terms) <= 3 or any(
                not isinstance(term, str) or not term.strip() or term != term.strip().casefold()
                for term in terms):
            raise ValueError(f"Invalid exact terms for {case['id']}")
    return cases


def run(*, catalog_path: Path, config_path: Path, cache_dir: Path,
        output_path: Path, max_examples_per_case: int = 20) -> dict:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from saia.arxiv_metadata import first_submission

    if not 1 <= max_examples_per_case <= 30:
        raise ValueError("Example cap must be between 1 and 30")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    cases = _validated_cases(config, catalog)
    word_boundaries = config["version"] == "priority-arxiv-pilot-v2"
    cache_manifest = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
    if cache_manifest.get("version") != "thematic-arxiv-cache-0.4.41-r2":
        raise ValueError("Unexpected arXiv cache version")
    docs = cache_dir / "documents.parquet"
    docs_sha = _sha(docs)
    if docs_sha != cache_manifest["files"]["documents.parquet"]["sha256"]:
        raise ValueError("arXiv cache file changed since its manifest was written")
    input_hashes = {"catalog": _sha(catalog_path), "pilot_config": _sha(config_path),
                    "arxiv_documents": docs_sha}
    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if existing.get("input_sha256") != input_hashes:
            raise FileExistsError("Pilot inputs changed; use a new output path")
        return existing

    states = {case["id"]: {"matched": 0, "invalid_first_date": 0,
                            "years": Counter(), "earliest": [], "latest": []}
              for case in cases}
    parquet = pq.ParquetFile(docs)
    scanned = 0
    earliest_cap = min(5, max_examples_per_case // 2)
    latest_cap = max_examples_per_case - earliest_cap
    for batch in parquet.iter_batches(batch_size=8192, columns=["id", "title", "abstract", "versions"]):
        scanned += batch.num_rows
        title, abstract = batch.column(1), batch.column(2)
        for case in cases:
            mask = None
            for term in case["all_terms"]:
                if word_boundaries:
                    pattern = _boundary_pattern(term)
                    title_match = pc.match_substring_regex(title, pattern, ignore_case=True)
                    abstract_match = pc.match_substring_regex(abstract, pattern, ignore_case=True)
                else:
                    title_match = pc.match_substring(title, term, ignore_case=True)
                    abstract_match = pc.match_substring(abstract, term, ignore_case=True)
                term_mask = pc.or_(pc.fill_null(title_match, False), pc.fill_null(abstract_match, False))
                mask = term_mask if mask is None else pc.and_(mask, term_mask)
            matching = batch.filter(mask).to_pylist()
            state = states[case["id"]]
            for row in matching:
                state["matched"] += 1
                try:
                    first_date = first_submission(row.get("versions"))
                    year = int(first_date[:4])
                except (TypeError, ValueError, IndexError):
                    state["invalid_first_date"] += 1
                    continue
                state["years"][str(year)] += 1
                reference = {"arxiv_id": row["id"], "first_submission": first_date,
                             "title_in_current_snapshot": row["title"],
                             "url": f"https://arxiv.org/abs/{row['id']}"}
                state["earliest"].append(reference)
                state["earliest"].sort(key=lambda item: (item["first_submission"], item["arxiv_id"]))
                del state["earliest"][earliest_cap:]
                state["latest"].append(reference)
                state["latest"].sort(key=lambda item: (item["first_submission"], item["arxiv_id"]), reverse=True)
                del state["latest"][latest_cap:]
    rows = []
    for case in cases:
        state = states[case["id"]]
        references = list({item["arxiv_id"]: item for item in state["earliest"] + state["latest"]}.values())
        rows.append({"catalog_id": case["id"], "terms_and": case["all_terms"],
                     "matched_in_selected_cache": state["matched"],
                     "invalid_first_date": state["invalid_first_date"],
                     "year_counts_in_selected_cache": dict(sorted(state["years"].items())),
                     "example_references": references,
                     "interpretation": "seeded literal retrieval only; not signal detection"})
    report = {
        "version": config["version"], "created_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": input_hashes, "cache_source": cache_manifest["source"],
        "cache_period": cache_manifest["period"], "scanned_cached_documents": scanned,
        "pilot_cases": rows, "limits": {
            "max_example_references_per_case": max_examples_per_case,
            "full_arxiv_coverage": False, "field_denominator_available": False,
            "publication_share_growth_measured": False, "weak_signal_detected": False,
            "customer_examples_are_independent_gold": False,
            "metadata_title_abstract_may_be_updated_after_first_submission": True,
            "zero_matches_means_no_match_in_selected_cache_not_no_research": True,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
