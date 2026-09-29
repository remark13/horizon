"""Compare strict JRC title retrieval with one predeclared generic expansion.

The expansion keeps target words in order and allows a bounded number of
intervening tokens.  It is a post-hoc development diagnostic, not a claim of
precision, true recall, or production improvement.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

import yaml

from saia.arxiv_metadata import first_submission, validate_schema
from saia.hybrid import digest
from saia.jrc_arxiv_observability import (
    CONFIG_PATH as OBSERVABILITY_CONFIG_PATH,
    exact_phrase_pattern,
    load_inputs as load_observability_inputs,
    normalize_phrase,
)
from saia.local_arxiv_search import policy, validate_inventory
from saia.source_registry import file_sha256


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "jrc-arxiv-expansion.v0.4.25.yaml"
CONFIG_VERSION = "jrc-arxiv-expansion-config-0.4.25"
REPORT_VERSION = "jrc-arxiv-ordered-gap-expansion-0.4.25"
SAMPLE_SALT = "jrc-arxiv-ordered-gap-expansion-sample-0.4.25"


def ordered_gap_pattern(value: object, max_intervening_tokens: int) -> str:
    if not 0 <= max_intervening_tokens <= 10:
        raise ValueError("max_intervening_tokens must be between 0 and 10.")
    tokens = normalize_phrase(value).split()
    if not tokens:
        raise ValueError("JRC signal title has no searchable tokens.")
    expression = r"(?:^| )" + re.escape(tokens[0])
    for token in tokens[1:]:
        expression += (
            rf"(?: +[a-z0-9]+){{0,{max_intervening_tokens}}} +"
            + re.escape(token)
        )
    return expression + r"(?: |$)"


def _verify_payload(value: dict, expected: str) -> None:
    payload = dict(value)
    actual = payload.pop("report_payload_sha256", None)
    if actual != expected or digest(payload) != expected:
        raise ValueError("Pinned JRC baseline payload changed.")


def load_inputs(config_path: Path = CONFIG_PATH) -> tuple[dict, dict, list[dict]]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != CONFIG_VERSION:
        raise ValueError("Unknown JRC expansion config version.")
    baseline_cfg = config["baseline"]
    report_path = (ROOT / baseline_cfg["report_path"]).resolve()
    observability_config_path = (ROOT / baseline_cfg["config_path"]).resolve()
    if file_sha256(report_path) != baseline_cfg["report_bytes_sha256"]:
        raise ValueError("Pinned JRC observability report bytes changed.")
    if file_sha256(observability_config_path) != baseline_cfg["config_bytes_sha256"]:
        raise ValueError("Pinned JRC observability config bytes changed.")
    baseline = json.loads(report_path.read_text(encoding="utf-8"))
    _verify_payload(baseline, baseline_cfg["report_payload_sha256"])
    _, _, signals = load_observability_inputs(observability_config_path)
    baseline_by_number = {item["source_number"]: item for item in baseline["results"]}
    if set(baseline_by_number) != {item["source_number"] for item in signals}:
        raise ValueError("Baseline and JRC target signals differ.")
    for signal in signals:
        signal["baseline_exact_matches"] = baseline_by_number[
            signal["source_number"]
        ]["title_abstract_matches"]
    expansion = config["expansion"]
    if expansion.get("manual_aliases") != []:
        raise ValueError("This diagnostic must not contain manual aliases.")
    if expansion.get("corpus_outcome_tuning_used") is not False:
        raise ValueError("Expansion must remain a fixed generic transformation.")
    return config, baseline, signals


def _sample_rank(signal_number: int, identifier: str) -> int:
    value = f"{SAMPLE_SALT}:{signal_number}:{identifier}".encode()
    return int(hashlib.sha256(value).hexdigest()[:16], 16)


def _offer_sample(heap: list, limit: int, signal_number: int, item: dict) -> None:
    rank = _sample_rank(signal_number, item["arxiv_id"])
    heapq.heappush(heap, (-rank, item["arxiv_id"], item))
    if len(heap) > limit:
        heapq.heappop(heap)


def scan_paths(paths: list[Path], signals: list[dict], start: date, cutoff: date,
               max_intervening_tokens: int, sample_per_signal: int) -> dict:
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pyarrow is required for JRC expansion diagnostic.") from exc
    if not paths or cutoff <= start or sample_per_signal < 1:
        raise ValueError("Valid parquet input, interval and sample size are required.")
    prepared = []
    for signal in signals:
        prepared.append({
            **signal,
            "exact_pattern": exact_phrase_pattern(signal["signal_original"]),
            "expanded_pattern": ordered_gap_pattern(
                signal["signal_original"], max_intervening_tokens
            ),
            "exact_matches": 0,
            "expanded_matches": 0,
            "expansion_only_matches": 0,
            "outside_period_expanded_matches": 0,
            "invalid_v1_dates": 0,
            "exact_year_counts": Counter(),
            "expanded_year_counts": Counter(),
            "sample_heap": [],
        })
    scanned_rows = 0
    for path in sorted(paths):
        parquet = pq.ParquetFile(path)
        validate_schema(parquet.schema_arrow.names)
        for batch in parquet.iter_batches(
            batch_size=8192,
            columns=["id", "title", "abstract", "categories", "versions"],
        ):
            scanned_rows += batch.num_rows
            table = pa.Table.from_batches([batch])
            combined = pc.binary_join_element_wise(
                pc.fill_null(table["title"], ""),
                pc.fill_null(table["abstract"], ""),
                " ",
            )
            normalized = pc.replace_substring_regex(
                pc.utf8_lower(combined), pattern="[^a-z0-9]+", replacement=" "
            )
            for signal in prepared:
                exact_mask = pc.fill_null(
                    pc.match_substring_regex(normalized, signal["exact_pattern"]), False
                )
                expanded_mask = pc.fill_null(
                    pc.match_substring_regex(normalized, signal["expanded_pattern"]), False
                )
                invalid_superset = pc.and_(exact_mask, pc.invert(expanded_mask))
                if pc.any(invalid_superset).as_py():
                    raise ValueError("Ordered-gap expansion is not a superset of exact phrase.")
                if pc.sum(pc.cast(expanded_mask, pa.int64())).as_py() == 0:
                    continue
                selected = table.append_column("_exact_match", exact_mask).filter(expanded_mask)
                for row in selected.to_pylist():
                    try:
                        published = date.fromisoformat(first_submission(row.get("versions")))
                    except ValueError:
                        signal["invalid_v1_dates"] += 1
                        continue
                    if not start <= published < cutoff:
                        signal["outside_period_expanded_matches"] += 1
                        continue
                    exact = bool(row.pop("_exact_match"))
                    year = str(published.year)
                    signal["expanded_matches"] += 1
                    signal["expanded_year_counts"][year] += 1
                    if exact:
                        signal["exact_matches"] += 1
                        signal["exact_year_counts"][year] += 1
                        continue
                    signal["expansion_only_matches"] += 1
                    identifier = str(row.get("id") or "").strip()
                    _offer_sample(signal["sample_heap"], sample_per_signal,
                                  signal["source_number"], {
                        "arxiv_id": identifier,
                        "url": f"https://arxiv.org/abs/{identifier}",
                        "title": " ".join(str(row.get("title") or "").split()),
                        "first_submission_date": published.isoformat(),
                        "categories": str(row.get("categories") or "").split(),
                        "review_label": None,
                    })
    results = []
    for signal in prepared:
        if signal["exact_matches"] != signal["baseline_exact_matches"]:
            raise ValueError(
                f"Exact baseline drift for JRC #{signal['source_number']}: "
                f"expected {signal['baseline_exact_matches']}, got {signal['exact_matches']}"
            )
        sample = [item[2] for item in sorted(
            signal.pop("sample_heap"), key=lambda value: (-value[0], value[1])
        )]
        exact_years = dict(sorted(signal.pop("exact_year_counts").items()))
        expanded_years = dict(sorted(signal.pop("expanded_year_counts").items()))
        exact = signal["exact_matches"]
        expanded = signal["expanded_matches"]
        results.append({
            **{
                key: value for key, value in signal.items()
                if key not in {"exact_pattern", "expanded_pattern"}
            },
            "exact_observed": exact > 0,
            "expanded_observed": expanded > 0,
            "expansion_multiple_vs_exact": round(expanded / exact, 4) if exact else None,
            "exact_year_counts": exact_years,
            "expanded_year_counts": expanded_years,
            "expansion_only_review_sample": sample,
        })
    return {"scanned_rows": scanned_rows, "results": results}


def evaluate(config_path: Path, mirror_dir: Path) -> dict:
    config, baseline, signals = load_inputs(config_path)
    baseline_benchmark = baseline["benchmark"]
    start = date.fromisoformat(baseline_benchmark["period_start"])
    cutoff = date.fromisoformat(baseline_benchmark["as_of_exclusive"])
    mirror_policy = policy()
    paths = validate_inventory(
        str(mirror_dir.resolve()),
        mirror_policy["expected_files"],
        mirror_policy["expected_rows"],
    )
    expansion = config["expansion"]
    scan = scan_paths(
        paths,
        signals,
        start,
        cutoff,
        int(expansion["max_intervening_tokens_per_pair"]),
        int(expansion["sample_per_signal"]),
    )
    results = scan["results"]
    exact_observed = sum(item["exact_observed"] for item in results)
    expanded_observed = sum(item["expanded_observed"] for item in results)
    exact_documents = sum(item["exact_matches"] for item in results)
    expanded_documents = sum(item["expanded_matches"] for item in results)
    report = {
        "version": REPORT_VERSION,
        "config": {"file": config_path.name, "bytes_sha256": file_sha256(config_path)},
        "baseline": {
            "file": Path(config["baseline"]["report_path"]).name,
            "bytes_sha256": config["baseline"]["report_bytes_sha256"],
            "payload_sha256": config["baseline"]["report_payload_sha256"],
            "exact_counts_recomputed_and_matched": True,
        },
        "source": {
            "dataset": mirror_policy["dataset"],
            "revision": mirror_policy["revision"],
            "inventory_files": len(paths),
            "inventory_rows": mirror_policy["expected_rows"],
            "scanned_rows": scan["scanned_rows"],
            "text_semantics": "current_snapshot_title_abstract_with_historical_v1_date_cutoff",
        },
        "period": {"start": start.isoformat(), "as_of_exclusive": cutoff.isoformat()},
        "expansion": expansion,
        "summary": {
            "target_signals": len(results),
            "exact_observed_signals": exact_observed,
            "expanded_observed_signals": expanded_observed,
            "newly_observed_original_titles": [
                item["signal_original"] for item in results
                if not item["exact_observed"] and item["expanded_observed"]
            ],
            "still_unobserved_original_titles": [
                item["signal_original"] for item in results if not item["expanded_observed"]
            ],
            "exact_document_assignments": exact_documents,
            "expanded_document_assignments": expanded_documents,
            "expansion_only_document_assignments": expanded_documents - exact_documents,
            "expanded_volume_multiple_vs_exact": round(
                expanded_documents / exact_documents, 4
            ) if exact_documents else None,
            "document_assignments_may_overlap_between_signals": True,
        },
        "results": results,
        "interpretation": {
            **config["interpretation"],
            "output_role": "expanded_retrieval_candidates_for_manual_review",
            "independent_labels_created": False,
            "market_outcome_inferred": False,
        },
        "limitations": [
            "The diagnostic was defined after the strict baseline was observed and is development-only.",
            "No JRC source document sets are available, so precision and true recall are unknown.",
            "Allowing token gaps may retrieve syntactically related but topically irrelevant documents.",
            "Samples contain titles and links only; review labels remain null.",
            "Current arXiv title/abstract text is not a historical v1 text reconstruction.",
            "Document assignments can overlap between JRC signals and are not unique corpus counts.",
        ],
        "implementation": {
            "module": Path(__file__).name,
            "module_bytes_sha256": file_sha256(Path(__file__)),
        },
    }
    report["report_payload_sha256"] = digest(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--mirror-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("JRC expansion report is immutable; choose a new output path.")
    report = evaluate(args.config, args.mirror_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "output": str(args.output),
        "exact_observed": report["summary"]["exact_observed_signals"],
        "expanded_observed": report["summary"]["expanded_observed_signals"],
        "expansion_multiple": report["summary"]["expanded_volume_multiple_vs_exact"],
        "sha256": report["report_payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
