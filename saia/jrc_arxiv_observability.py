"""Strict lexical observability of JRC AI/ML titles in the pinned arXiv mirror.

The JRC title catalogue does not contain the source document sets or exact TIM
queries.  Therefore this module measures a reproducible lower-bound lexical
observability, not retrieval recall, weak-signal precision, or future success.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

import yaml

from saia.arxiv_metadata import first_submission, validate_schema
from saia.hybrid import digest
from saia.local_arxiv_search import policy, validate_inventory
from saia.retrieval_benchmark import wilson_interval
from saia.source_registry import file_sha256


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "jrc-arxiv-observability.v0.4.24-r4.yaml"
CONFIG_VERSIONS = {
    "jrc-arxiv-observability-config-0.4.24": "jrc-arxiv-lexical-observability-0.4.24",
    "jrc-arxiv-observability-config-0.4.24-r2": "jrc-arxiv-lexical-observability-0.4.24-r2",
    "jrc-arxiv-observability-config-0.4.24-r3": "jrc-arxiv-lexical-observability-0.4.24-r3",
    "jrc-arxiv-observability-config-0.4.24-r4": "jrc-arxiv-lexical-observability-0.4.24-r4",
}


def normalize_phrase(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def exact_phrase_pattern(value: object) -> str:
    tokens = normalize_phrase(value).split()
    if not tokens:
        raise ValueError("JRC signal title has no searchable tokens.")
    return r"(?:^| )" + r" +".join(re.escape(token) for token in tokens) + r"(?: |$)"


def _verify_payload(value: dict, expected: str) -> None:
    payload = dict(value)
    actual = payload.pop("report_payload_sha256", None)
    if actual != expected or digest(payload) != expected:
        raise ValueError("JRC reference payload hash mismatch.")


def load_inputs(config_path: Path = CONFIG_PATH) -> tuple[dict, dict, list[dict]]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") not in CONFIG_VERSIONS:
        raise ValueError("Unknown JRC observability config version.")
    reference = config["reference_catalog"]
    official = config["official_report"]
    catalog_path = (ROOT / reference["path"]).resolve()
    pdf_path = (ROOT / official["path"]).resolve()
    if file_sha256(catalog_path) != reference["bytes_sha256"]:
        raise ValueError("JRC reference catalog bytes changed.")
    if file_sha256(pdf_path) != official["bytes_sha256"]:
        raise ValueError("Official JRC PDF bytes changed.")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    _verify_payload(catalog, reference["payload_sha256"])
    if catalog["source"]["official_claimed_signals"] != official["official_signal_count"]:
        raise ValueError("Official and derived JRC signal counts are inconsistent.")
    target_cluster = config["benchmark"]["target_cluster"]
    signals = [
        {
            "source_number": item["source_number"],
            "signal_original": item["signal_original"],
            "signal_ru": item["signal_ru"],
            "cluster_original": item["cluster_original"],
            "description_present_in_supplied_source": item[
                "description_present_in_supplied_source"
            ],
        }
        for item in catalog["records"]
        if item["cluster_original"] == target_cluster
    ]
    if len(signals) != int(config["benchmark"]["expected_target_signals"]):
        raise ValueError("JRC target cluster size drifted.")
    normalized = [normalize_phrase(item["signal_original"]) for item in signals]
    if len(normalized) != len(set(normalized)):
        raise ValueError("JRC target titles collide after normalization.")
    return config, catalog, signals


def scan_paths(paths: list[Path], signals: list[dict], start: date, cutoff: date,
               recent_start_year: int = 2021,
               recent_end_year: int = 2023,
               low_support_below_documents: int = 5) -> dict:
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - optional bulk extra
        raise RuntimeError("pyarrow is required for JRC arXiv observability.") from exc
    if (not paths or cutoff <= start or recent_end_year < recent_start_year
            or low_support_below_documents < 1):
        raise ValueError("Non-empty parquet input and a valid interval are required.")
    prepared = []
    for signal in signals:
        prepared.append({
            **signal,
            "normalized_phrase": normalize_phrase(signal["signal_original"]),
            "pattern": exact_phrase_pattern(signal["signal_original"]),
            "matches": {},
            "outside_period_matches": 0,
            "invalid_v1_dates": 0,
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
            title = pc.fill_null(table["title"], "")
            abstract = pc.fill_null(table["abstract"], "")
            combined = pc.binary_join_element_wise(title, abstract, " ")
            title_normalized = pc.replace_substring_regex(
                pc.utf8_lower(title), pattern="[^a-z0-9]+", replacement=" "
            )
            combined_normalized = pc.replace_substring_regex(
                pc.utf8_lower(combined), pattern="[^a-z0-9]+", replacement=" "
            )
            for signal in prepared:
                combined_mask = pc.fill_null(
                    pc.match_substring_regex(combined_normalized, signal["pattern"]),
                    False,
                )
                if pc.sum(pc.cast(combined_mask, pa.int64())).as_py() == 0:
                    continue
                title_mask = pc.fill_null(
                    pc.match_substring_regex(title_normalized, signal["pattern"]),
                    False,
                )
                selected = table.append_column("_title_match", title_mask).filter(combined_mask)
                for row in selected.to_pylist():
                    identifier = str(row.get("id") or "").strip()
                    try:
                        published = date.fromisoformat(first_submission(row.get("versions")))
                    except ValueError:
                        signal["invalid_v1_dates"] += 1
                        continue
                    if not start <= published < cutoff:
                        signal["outside_period_matches"] += 1
                        continue
                    if identifier in signal["matches"]:
                        raise ValueError(f"Duplicate arXiv ID in mirror: {identifier}")
                    signal["matches"][identifier] = {
                        "arxiv_id": identifier,
                        "url": f"https://arxiv.org/abs/{identifier}",
                        "title": " ".join(str(row.get("title") or "").split()),
                        "first_submission_date": published.isoformat(),
                        "categories": str(row.get("categories") or "").split(),
                        "title_match": bool(row.pop("_title_match")),
                    }
    results = []
    for signal in prepared:
        matches = sorted(
            signal.pop("matches").values(),
            key=lambda item: (item["first_submission_date"], item["arxiv_id"]),
        )
        years = Counter(item["first_submission_date"][:4] for item in matches)
        recent_documents = sum(
            count for year, count in years.items()
            if recent_start_year <= int(year) <= recent_end_year
        )
        categories = Counter(category for item in matches for category in item["categories"])
        results.append({
            **{key: value for key, value in signal.items() if key != "pattern"},
            "title_abstract_matches": len(matches),
            "title_matches": sum(item["title_match"] for item in matches),
            "earliest_first_submission_date": (
                matches[0]["first_submission_date"] if matches else None
            ),
            "year_counts": dict(sorted(years.items())),
            "active_years": len(years),
            "diagnostic_recent_documents": recent_documents,
            "diagnostic_activeness": (
                round(recent_documents / len(matches), 6) if matches else None
            ),
            "diagnostic_activeness_support_documents": len(matches),
            "diagnostic_activeness_low_support": (
                bool(matches) and len(matches) < low_support_below_documents
            ),
            "top_categories": [
                {"category": name, "documents": count}
                for name, count in categories.most_common(10)
            ],
            "earliest_examples": matches[:5],
            "observed": bool(matches),
        })
    return {"scanned_rows": scanned_rows, "results": results}


def evaluate(config_path: Path, mirror_dir: Path) -> dict:
    config, catalog, signals = load_inputs(config_path)
    benchmark = config["benchmark"]
    start = date.fromisoformat(benchmark["period_start"])
    cutoff = date.fromisoformat(benchmark["as_of_exclusive"])
    mirror_policy = policy()
    paths = validate_inventory(
        str(mirror_dir.resolve()),
        mirror_policy["expected_files"],
        mirror_policy["expected_rows"],
    )
    scan = scan_paths(
        paths,
        signals,
        start,
        cutoff,
        int(benchmark.get("diagnostic_recent_start_year", 2021)),
        int(benchmark.get("diagnostic_recent_end_year", 2023)),
        int(benchmark.get("diagnostic_low_support_below_documents", 5)),
    )
    results = scan["results"]
    observed = sum(item["observed"] for item in results)
    title_observed = sum(item["title_matches"] > 0 for item in results)
    total = len(results)
    report = {
        "version": CONFIG_VERSIONS[config["version"]],
        "config": {
            "file": config_path.name,
            "bytes_sha256": file_sha256(config_path),
        },
        "reference": {
            "catalog_file": Path(config["reference_catalog"]["path"]).name,
            "catalog_bytes_sha256": config["reference_catalog"]["bytes_sha256"],
            "catalog_payload_sha256": config["reference_catalog"]["payload_sha256"],
            "derived_rows": catalog["counts"]["rows"],
            "official_claimed_signals": config["official_report"]["official_signal_count"],
            "gap_vs_official_claim": catalog["counts"]["gap_vs_official_claim"],
            "official_pdf_bytes_sha256": config["official_report"]["bytes_sha256"],
            "official_url": config["official_report"]["landing_url"],
            "doi": config["official_report"]["doi"],
            "licence": config["official_report"]["licence"],
            "released_at": config["official_report"]["released_at"],
            "role": "silver_reference_not_outcome_truth",
        },
        "jrc_methodology": config["methodology"],
        "source": {
            "dataset": mirror_policy["dataset"],
            "revision": mirror_policy["revision"],
            "inventory_files": len(paths),
            "inventory_rows": mirror_policy["expected_rows"],
            "scanned_rows": scan["scanned_rows"],
            "text_semantics": "current_snapshot_title_abstract_with_historical_v1_date_cutoff",
        },
        "benchmark": {
            **benchmark,
            "target_signals": total,
            "title_abstract_observed_signals": observed,
            "title_abstract_observed_share": round(observed / total, 4),
            "title_abstract_observed_wilson_95": wilson_interval(observed, total),
            "title_observed_signals": title_observed,
            "title_observed_share": round(title_observed / total, 4),
            "signals_with_diagnostic_activeness_above_0_90": sum(
                item["diagnostic_activeness"] is not None
                and item["diagnostic_activeness"] > 0.90
                for item in results
            ),
            "signals_with_diagnostic_activeness_above_0_90_and_sufficient_support": sum(
                item["diagnostic_activeness"] is not None
                and item["diagnostic_activeness"] > 0.90
                and not item["diagnostic_activeness_low_support"]
                for item in results
            ),
            "high_activeness_low_support_titles": [
                item["signal_original"] for item in results
                if item["diagnostic_activeness"] is not None
                and item["diagnostic_activeness"] > 0.90
                and item["diagnostic_activeness_low_support"]
            ],
            "unobserved_original_titles": [
                item["signal_original"] for item in results if not item["observed"]
            ],
        },
        "results": results,
        "interpretation": {
            "measures": "strict_original_title_lexical_observability_in_arxiv",
            "does_not_measure": [
                "retrieval_recall_without_jrc_source_document_sets",
                "semantic_topic_recovery",
                "weak_signal_precision",
                "future_scientific_or_market_success",
            ],
            "jrc_descriptions_generated": False,
            "production_score_modified": False,
            "production_thresholds_modified": False,
            "holdout_used": False,
            "diagnostic_activeness_is_jrc_score": False,
            "diagnostic_low_support_is_production_gate": False,
            "diagnostic_activeness_comparison_reason": (
                "The numerator/window shape is copied from JRC, but Horizon uses strict exact-phrase "
                "arXiv matches rather than reconstructed Scopus/TIM document sets."
            ),
        },
        "limitations": [
            "The supplied 219-row workbook is not the complete 221-signal official catalogue.",
            "The supplied workbook has no descriptions for the 25 selected AI/ML titles.",
            "JRC source document sets and exact reconstructed TIM queries are unavailable here.",
            "Strict title matching is a lower bound and misses aliases, acronyms and semantic variants.",
            "The local arXiv snapshot stores current title/abstract text, not historical v1 text.",
            "arXiv and Scopus/PATSTAT have different disciplinary and regional coverage.",
            "Diagnostic activeness is structurally similar to JRC but not numerically comparable.",
            "The five-document support flag is diagnostic only and was not calibrated as a threshold.",
        ],
    }
    report["implementation"] = {
        "module": Path(__file__).name,
        "module_bytes_sha256": file_sha256(Path(__file__)),
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
        parser.error("JRC observability report is immutable; choose a new output path.")
    report = evaluate(args.config, args.mirror_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "output": str(args.output),
        "target_signals": report["benchmark"]["target_signals"],
        "observed": report["benchmark"]["title_abstract_observed_signals"],
        "title_observed": report["benchmark"]["title_observed_signals"],
        "sha256": report["report_payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
