"""Auditable DOI/title link candidates between priority arXiv and OpenAlex.

This is a candidate queue, not an automatic merge or a source of signal labels.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from saia.controlled_collection import sha256_file


VERSION = "priority-cross-source-link-candidates-v1"
_TITLE = re.compile(r"[^\w]+", re.UNICODE)


def title_key(text: str | None) -> str:
    return " ".join(_TITLE.sub(" ", (text or "").casefold()).split())


def candidates(arxiv: list[dict], openalex: list[dict]) -> list[dict]:
    doi_index: dict[str, list[dict]] = defaultdict(list)
    title_index: dict[str, list[dict]] = defaultdict(list)
    for work in arxiv:
        doi = str(work.get("doi_current") or "").casefold().strip()
        if doi:
            doi_index[doi].append(work)
        key = title_key(work.get("title_current"))
        if key:
            title_index[key].append(work)
    pairs = {}
    for work in openalex:
        doi = str(work.get("doi") or "").casefold().strip()
        key = title_key(work.get("title"))
        matches = {item["arxiv_id"]: item for item in
                   doi_index.get(doi, []) + title_index.get(key, []) if doi or key}
        for arxiv_id, other in matches.items():
            same_doi = bool(doi and doi == str(other.get("doi_current") or "").casefold().strip())
            same_title = bool(key and key == title_key(other.get("title_current")))
            arxiv_authors = title_key(other.get("authors_current"))
            openalex_names = [title_key(author) for author in (work.get("authors") or [])]
            author_overlap = any(name and name in arxiv_authors for name in openalex_names)
            year_gap = abs(int(work["publication_date"][:4]) -
                           int(other["first_submission_date"][:4]))
            arxiv_doi = str(other.get("doi_current") or "").casefold().strip()
            conflicting_publisher_doi = bool(doi and arxiv_doi and doi != arxiv_doi
                                             and not doi.startswith("10.48550/arxiv.")
                                             and not arxiv_doi.startswith("10.48550/arxiv."))
            status = ("doi_conflict_review" if conflicting_publisher_doi else
                      "strong_doi_candidate" if same_doi and (same_title or author_overlap) else
                      "title_author_candidate" if same_title and author_overlap and year_gap <= 2 else
                      "title_only_review")
            pairs[(work["openalex_id"], arxiv_id)] = {
                "openalex_id": work["openalex_id"], "arxiv_id": arxiv_id,
                "openalex_url": work["openalex_url"],
                "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
                "source_mission_id": work["source_mission_id"],
                "same_doi": same_doi, "same_normalized_title": same_title,
                "author_name_overlap": author_overlap, "publication_year_gap": year_gap,
                "conflicting_publisher_doi": conflicting_publisher_doi,
                "status": status, "automatic_merge": False,
            }
    return [pairs[key] for key in sorted(pairs)]


def build(*, arxiv_dir: Path, openalex_dir: Path, output: Path) -> dict:
    import pyarrow.parquet as pq

    if output.exists():
        raise FileExistsError("Cross-source link report is immutable")
    am = json.loads((arxiv_dir / "manifest.json").read_text(encoding="utf-8"))
    om = json.loads((openalex_dir / "manifest.json").read_text(encoding="utf-8"))
    apath, opath = arxiv_dir / "documents.parquet", openalex_dir / "works.parquet"
    if (sha256_file(apath) != am["files"]["documents.parquet"]["sha256"]
            or sha256_file(opath) != om["file"]["sha256"]):
        raise ValueError("Input corpus hash mismatch")
    arxiv = pq.read_table(apath, columns=["arxiv_id", "title_current", "doi_current",
                                           "authors_current", "first_submission_date"]).to_pylist()
    openalex = pq.read_table(opath, columns=["openalex_id", "openalex_url", "title",
                                             "doi", "authors", "publication_date",
                                             "source_mission_id"]).to_pylist()
    links = candidates(arxiv, openalex)
    by_arxiv = Counter(item["arxiv_id"] for item in links)
    report = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
              "input_manifests_sha256": {"arxiv": sha256_file(arxiv_dir / "manifest.json"),
                                         "openalex": sha256_file(openalex_dir / "manifest.json")},
              "counts": {"arxiv_works": len(arxiv), "openalex_works": len(openalex),
                         "candidate_pairs": len(links),
                         "arxiv_ids_with_multiple_openalex_candidates": sum(
                             count > 1 for count in by_arxiv.values()),
                         "by_status": dict(sorted(Counter(item["status"] for item in links).items()))},
              "candidates": links,
              "policy": {"automatic_merge": False,
                         "same_title_alone_is_not_identity": True,
                         "only_saved_query_cohorts": True,
                         "not_a_precision_or_signal_detection_measure": True}}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return report
