"""Seal the already-frozen, bounded OpenAlex/arXiv discovery result for analysis.

This is a *sample* of a field, never a complete longitudinal corpus.  The
projection deliberately leaves citations, affiliations and source topics
unknown rather than inventing them from a preview response.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

from saia.controlled_collection import canonical_json
from saia.query_expansion import digest


VERSION = "bounded-balanced-discovery-projection-0.4.50"
ATOM = "http://www.w3.org/2005/Atom"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _year_counts(works, date_key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for work in works:
        year = str(work[date_key])[:4]
        counts[year] = counts.get(year, 0) + 1
    return dict(sorted(counts.items()))


def _inverted(text: str | None) -> dict[str, list[int]] | None:
    if not text:
        return None
    result: dict[str, list[int]] = {}
    for index, word in enumerate(text.split()):
        result.setdefault(word, []).append(index)
    return result


def _openalex_id(work: dict) -> str | None:
    return next((value for value in work.get("source_ids") or []
                 if isinstance(value, str) and value.startswith("https://openalex.org/W")), None)


def _arxiv_id(work: dict) -> str | None:
    for value in work.get("source_ids") or []:
        if isinstance(value, str) and "arxiv.org/abs/" in value:
            return value.split("arxiv.org/abs/", 1)[1].split("v", 1)[0]
    return None


def _openalex_projection(work: dict, identifier: str) -> dict:
    published = work["published_at"][:10]
    doi = work.get("doi")
    return {
        "id": identifier,
        "doi": (doi if doi.startswith("http") else f"https://doi.org/{doi}") if doi else None,
        "display_name": work.get("title") or "",
        "publication_date": published,
        "publication_year": int(published[:4]),
        "abstract_inverted_index": _inverted(work.get("abstract")),
        "authorships": [{"author": {"display_name": name}, "institutions": []}
                        for name in work.get("authors") or []],
        "cited_by_count": None,
        "counts_by_year": None,
        "primary_topic": None,
        "type": work.get("openalex_type"),
        "_projection": VERSION,
        "_source_urls": work.get("urls") or [],
    }


def _arxiv_xml(works: list[tuple[str, dict]]) -> bytes:
    feed = ET.Element(f"{{{ATOM}}}feed")
    for identifier, work in works:
        entry = ET.SubElement(feed, f"{{{ATOM}}}entry")
        fields = {
            "id": f"http://arxiv.org/abs/{identifier}",
            "title": work.get("title") or "",
            "summary": work.get("abstract") or "",
            "published": f"{work['published_at'][:10]}T00:00:00Z",
            "updated": f"{work['published_at'][:10]}T00:00:00Z",
        }
        for key, value in fields.items():
            ET.SubElement(entry, f"{{{ATOM}}}{key}").text = value
        for name in work.get("authors") or []:
            author = ET.SubElement(entry, f"{{{ATOM}}}author")
            ET.SubElement(author, f"{{{ATOM}}}name").text = name
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)


def build_package(profile: dict, output: Path, *, source_job: dict) -> dict:
    """Create an immutable package from a matching completed discovery job."""
    if output.exists():
        raise ValueError("Existing bounded package is immutable")
    collection = profile.get("collection_profile") or {}
    provenance = collection.get("provenance") or {}
    if (collection.get("decision") not in {
            "bounded_balanced_openalex_arxiv_candidates",
            "bounded_compound_concept_candidates"}
            or source_job.get("job_kind") != "approved_balanced_discovery"
            or source_job.get("status") != "succeeded"
            or str(source_job.get("job_id")) != provenance.get("balanced_discovery_job_id")
            or source_job.get("result_sha256") != provenance.get("balanced_result_sha256")):
        raise ValueError("Bounded package is not bound to the approved discovery result")
    result = source_job.get("result") or {}
    works = result.get("works") or []
    if not isinstance(works, list) or not 1 <= len(works) <= 300:
        raise ValueError("Frozen bounded discovery result is empty or over limit")
    start = date.fromisoformat(profile["period"]["from"])
    cutoff = date.fromisoformat(profile["as_of_date"])
    openalex: dict[str, dict] = {}
    arxiv: dict[str, dict] = {}
    omitted_boundary = 0
    omitted_original_only = 0
    for work in works:
        if not isinstance(work, dict) or not work.get("published_at"):
            raise ValueError("Frozen discovery work lacks publication date")
        if (profile.get("controlled_search_plan", {}).get(
                "original_query_used_for_preview_only") is True
                and set(work.get("branch_provenance") or []) == {"original-query"}):
            omitted_original_only += 1
            continue
        published = date.fromisoformat(work["published_at"][:10])
        if not start <= published < cutoff:
            omitted_boundary += 1
            continue
        sources = set(work.get("source_provenance") or work.get("sources") or [])
        if "openalex" in sources:
            identifier = _openalex_id(work)
            if not identifier:
                raise ValueError("OpenAlex preview work lacks source identity")
            openalex.setdefault(identifier, _openalex_projection(work, identifier))
        if "arxiv" in sources:
            identifier = _arxiv_id(work)
            if not identifier:
                raise ValueError("arXiv preview work lacks source identity")
            arxiv.setdefault(identifier, work)
    if not openalex and not arxiv:
        raise ValueError("No works fall in complete calendar months")
    expected = set(profile.get("sources") or [])
    if expected != {source for source, rows in (("openalex", openalex), ("arxiv", arxiv)) if rows}:
        raise ValueError("Frozen source scope differs from the execution profile")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=output.name + ".tmp-", dir=output.parent))
    try:
        mission_text = canonical_json(profile)
        (temp / "mission.json").write_text(mission_text, encoding="utf-8")
        audit = {
            "version": VERSION,
            "source_job_id": str(source_job["job_id"]),
            "source_job_result_sha256": source_job["result_sha256"],
            "selection_engine": "frozen_bounded_balanced_discovery_projection",
            "scanned_rows": len(works),
            "selected_records": len(openalex) + len(arxiv),
            "source_records": {"openalex": len(openalex), "arxiv": len(arxiv)},
            "source_year_counts": {
                "openalex": _year_counts(openalex.values(), "publication_date"),
                "arxiv": _year_counts(arxiv.values(), "published_at"),
            },
            "arxiv_selection_strategy": (
                (result.get("local_arxiv_audit") or {}).get("selection_strategy")),
            "openalex_collection_mode": (
                (result.get("source_modes") or {}).get("openalex")),
            "omitted_partial_boundary_works": omitted_boundary,
            "omitted_original_only_works": omitted_original_only,
            "coverage_comparable": None,
            "source_errors": result.get("errors") or {},
            "limitations": [
                ("OpenAlex is a bounded cache of prior Horizon ingestions, not a complete or fresh field corpus."
                 if str((result.get("source_modes") or {}).get("openalex") or "").startswith("previously_ingested_local_cache")
                 else "OpenAlex is a bounded live-API preview, not a complete field corpus."),
                "Projection does not recover citations, affiliations or OpenAlex topics.",
                "The arXiv preview is bounded independently of the pinned full mirror.",
            ],
        }
        audit_text = canonical_json(audit)
        (temp / "collection_audit.json").write_text(audit_text, encoding="utf-8")
        files = {}
        if openalex:
            (temp / "openalex").mkdir()
            raw = json.dumps({"results": list(openalex.values())}, ensure_ascii=False).encode()
            (temp / "openalex" / "bounded.json").write_bytes(raw)
            files["openalex"] = ("bounded.json", raw, len(openalex))
        if arxiv:
            (temp / "arxiv").mkdir()
            raw = _arxiv_xml(list(arxiv.items()))
            (temp / "arxiv" / "bounded.xml").write_bytes(raw)
            files["arxiv"] = ("bounded.xml", raw, len(arxiv))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        manifest = {
            "connector_version": VERSION,
            "mission_id": profile["mission_id"],
            "query_version": profile["query_version"],
            "mission_snapshot_file": "mission.json",
            "mission_file_sha256": _sha(mission_text.encode()),
            "fetch_started_utc": now, "fetch_finished_utc": now,
            "audit_file": "collection_audit.json", "audit_sha256": _sha(audit_text.encode()),
            "incomplete": {}, "source_errors": {},
            "upstream_source_errors": result.get("errors") or {},
            "sources": {
                source: {
                    "access_mode": "frozen-bounded-discovery-projection",
                    "independent_discovery": not (
                        source == "openalex" and str(
                            (result.get("source_modes") or {}).get("openalex") or ""
                        ).startswith("previously_ingested_local_cache")
                    ),
                    "record_shape": "limited_preview_projection",
                    "records_are_selected_cohort": True,
                    "total_records": count,
                    "field_coverage": "bounded_nonrepresentative_selection",
                    "temporal_comparable": None,
                    "retrieval_origin": ((result.get("source_modes") or {}).get("openalex")
                                         if source == "openalex" else
                                         (result.get("source_modes") or {}).get("arxiv")),
                    "source_job_result_sha256": source_job["result_sha256"],
                    "files": [{"file": name, "records": count, "sha256": _sha(raw),
                               "http_status": None, "url": None}],
                }
                for source, (name, raw, count) in files.items()
            },
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, output)
        return {"package": str(output), "manifest": manifest, "audit": audit}
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
