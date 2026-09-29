"""Audit a bounded OpenAlex discovery sample against a frozen arXiv scope."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

from saia.hybrid import digest
from saia.ingest import collection_content_hash, validate_collection_input
from saia.source_identity import openalex_arxiv_identity

POLICY = "openalex-monthly-sample-audit-0.4.9"


def abstract_text(row: dict) -> str:
    positions = []
    for word, indexes in (row.get("abstract_inverted_index") or {}).items():
        for index in indexes:
            positions.append((index, word))
    return " ".join(word for _, word in sorted(positions))


def summarize(rows: list[dict], arxiv_ids: set[str], terms: list[str],
              page_entries: list[dict]) -> dict:
    openalex_ids = [row.get("id") for row in rows]
    if any(not item for item in openalex_ids) or len(openalex_ids) != len(set(openalex_ids)):
        raise ValueError("OpenAlex IDs must be present and unique")
    identities = [openalex_arxiv_identity(row) for row in rows]
    linked = [item["canonical_arxiv_id"] for item in identities if item["canonical_arxiv_id"]]
    linked_set = set(linked)
    literal = Counter()
    rows_with_literal = 0
    normalized_terms = [term.lower() for term in terms]
    for row in rows:
        text = f"{row.get('title') or ''} {abstract_text(row)}".lower()
        hits = [term for term in normalized_terms if term in text]
        literal.update(hits)
        rows_with_literal += bool(hits)
    source_total = sum(int(item["source_reported_count"]) for item in page_entries)
    return {
        "sample_records": len(rows),
        "unique_openalex_ids": len(set(openalex_ids)),
        "months": len(page_entries),
        "source_reported_records_in_disjoint_months": source_total,
        "sample_fraction_of_reported_query_results": len(rows) / source_total if source_total else None,
        "publication_date_min": min((row.get("publication_date") for row in rows), default=None),
        "publication_date_max": max((row.get("publication_date") for row in rows), default=None),
        "with_doi": sum(bool(row.get("doi")) for row in rows),
        "with_abstract": sum(bool(row.get("abstract_inverted_index")) for row in rows),
        "with_authorships": sum(bool(row.get("authorships")) for row in rows),
        "with_counts_by_year": sum(bool(row.get("counts_by_year")) for row in rows),
        "rows_with_literal_query_phrase": rows_with_literal,
        "literal_phrase_counts_nonexclusive": dict(sorted(literal.items())),
        "rows_with_resolved_arxiv_identity": len(linked),
        "unique_resolved_arxiv_ids": len(linked_set),
        "resolved_arxiv_ids_in_phrase_scope": len(linked_set & arxiv_ids),
        "resolved_arxiv_ids_outside_phrase_scope": len(linked_set - arxiv_ids),
        "arxiv_phrase_scope_ids": len(arxiv_ids),
        "identity_conflicts": sum(item["conflicting_arxiv_links"] for item in identities),
    }


def _validated_manifest(root: Path) -> tuple[dict, bytes]:
    manifest_bytes = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    mission_file = manifest["mission_snapshot_file"]
    mission_path = root / mission_file
    if Path(mission_file).name != mission_file or not mission_path.resolve().is_relative_to(root):
        raise ValueError("Mission snapshot must be inside package")
    mission_text = mission_path.read_text(encoding="utf-8")
    validate_collection_input(root, manifest, json.loads(mission_text), mission_text)
    return manifest, manifest_bytes


def audit(openalex_root: Path, arxiv_root: Path) -> dict:
    openalex_root, arxiv_root = openalex_root.resolve(), arxiv_root.resolve()
    openalex_manifest, openalex_manifest_bytes = _validated_manifest(openalex_root)
    arxiv_manifest, arxiv_manifest_bytes = _validated_manifest(arxiv_root)
    mission = json.loads((openalex_root / openalex_manifest["mission_snapshot_file"]).read_text())
    block = openalex_manifest["sources"].get("openalex") or {}
    pages = [item for item in block.get("files", []) if item.get("file")]
    rows = []
    for entry in pages:
        payload = json.loads((openalex_root / "openalex" / entry["file"]).read_bytes())
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != entry["records"]:
            raise ValueError(f"OpenAlex page count mismatch: {entry['file']}")
        rows.extend(results)
    arxiv_ids = set()
    for entry in arxiv_manifest["sources"]["arxiv"]["files"]:
        if not entry.get("file"):
            continue
        table = pq.read_table(arxiv_root / "arxiv" / entry["file"], columns=["id"])
        arxiv_ids.update(value.as_py() for value in table["id"])
    result = {
        "policy_version": POLICY,
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "openalex_mission_id": openalex_manifest["mission_id"],
        "arxiv_mission_id": arxiv_manifest["mission_id"],
        "openalex_manifest_sha256": hashlib.sha256(openalex_manifest_bytes).hexdigest(),
        "arxiv_manifest_sha256": hashlib.sha256(arxiv_manifest_bytes).hexdigest(),
        "openalex_collection_sha256": collection_content_hash(openalex_manifest),
        "arxiv_collection_sha256": collection_content_hash(arxiv_manifest),
        "observations": summarize(rows, arxiv_ids, mission["query"]["terms"], pages),
        "scientific_labels": None,
        "decision": (
            "Use for candidate discovery and source enrichment only; do not use this ranked sample "
            "as a denominator or as an unbiased prevalence series."
        ),
        "limitations": [
            "One relevance-ranked page per month is not a random or complete OpenAlex corpus.",
            "OpenAlex search is stemmed and semantically broader than exact arXiv phrase matching.",
            "Current OpenAlex metadata does not reconstruct what metadata was visible historically.",
            "An arXiv link or DOI is bibliographic overlap, not an independent replication.",
            "Absence from the frozen arXiv phrase scope can reflect query or coverage differences.",
        ],
    }
    result["report_sha256"] = digest(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit bounded OpenAlex sample against arXiv scope")
    parser.add_argument("openalex_package", type=Path)
    parser.add_argument("arxiv_package", type=Path)
    parser.add_argument("--export", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.openalex_package, args.arxiv_package)
    with args.export.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report["observations"], ensure_ascii=False))


if __name__ == "__main__":
    main()
