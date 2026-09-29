"""Measure exact source overlap for the fixed six-area customer pilot.

This is a retrieval diagnostic, not a weak-signal recall or precision score.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

import openpyxl
import requests


URL_RE = re.compile(r"https?://[^\s)\]>;,]+")
ARXIV_RE = re.compile(r"(?:arxiv\.org|ar5iv\.labs\.arxiv\.org)/(?:abs|html|pdf)/(\d{4}\.\d{4,5})", re.I)


def keys(url: str) -> set[str]:
    url = url.strip().rstrip("./")
    match = ARXIV_RE.search(url)
    if match:
        return {"arxiv:" + match.group(1)}
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/").lower()
    if host in {"doi.org", "dx.doi.org"}:
        return {"doi:" + path.lstrip("/")}
    return {"url:" + host + path} if host and path else set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local-arxiv-dir", type=Path,
                        help="Optionally check cited arXiv IDs against the pinned local snapshot")
    parser.add_argument("--base-url", default="http://127.0.0.1:8082")
    args = parser.parse_args()
    areas: dict[str, dict] = {}
    for file in args.pilot:
        areas.update(json.loads(file.read_text(encoding="utf-8"))["areas"])
    session = requests.Session()
    found_by_area: dict[str, set[str]] = {}
    retrieved_by_area: dict[str, int] = {}
    for area, entry in areas.items():
        job = session.get(args.base_url + "/jobs/" + entry["discovery_job_id"], timeout=30)
        job.raise_for_status()
        data = job.json()
        works = data["result"]["merge"]["results"]
        found_by_area[area] = set().union(*[
            set().union(*(keys(url) for url in (work.get("urls") or []) +
                         (work.get("source_ids") or [])))
            for work in works
        ]) if works else set()
        retrieved_by_area[area] = len(works)
    workbook = openpyxl.load_workbook(args.workbook, read_only=True, data_only=True)
    row_results = []
    summary: dict[str, Counter] = defaultdict(Counter)
    for excel_row, row in enumerate(workbook.active.iter_rows(min_row=3, values_only=True), 3):
        if not row[2]:
            continue
        area = str(row[3])
        if area not in areas:
            continue
        source_urls = URL_RE.findall(str(row[9] or ""))
        cited_keys = set().union(*(keys(url) for url in source_urls)) if source_urls else set()
        overlap = sorted(cited_keys & found_by_area.get(area, set()))
        arxiv_keys = sorted(key for key in cited_keys if key.startswith("arxiv:"))
        summary[area]["rows"] += 1
        summary[area]["cited_urls"] += len(source_urls)
        summary[area]["rows_with_arxiv"] += bool(arxiv_keys)
        summary[area]["cited_arxiv_papers"] += len(arxiv_keys)
        summary[area]["rows_with_exact_source_overlap"] += bool(overlap)
        summary[area]["exact_source_overlaps"] += len(overlap)
        row_results.append({
            "excel_row": excel_row, "number": row[1], "area": area,
            "title": row[2], "cited_urls": len(source_urls),
            "cited_arxiv_ids": arxiv_keys, "exact_source_overlap": overlap,
        })
    cited_arxiv = {key.removeprefix("arxiv:") for row in row_results
                   for key in row["cited_arxiv_ids"]}
    available_arxiv: set[str] = set()
    if args.local_arxiv_dir:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        lookup = pa.array(sorted(cited_arxiv))
        for path in sorted(args.local_arxiv_dir.glob("*.parquet")):
            for batch in pq.ParquetFile(path).iter_batches(batch_size=65536, columns=["id"]):
                hits = pc.filter(batch.column(0),
                                 pc.is_in(batch.column(0), value_set=lookup))
                available_arxiv.update(hits.to_pylist())
    output = {
        "method": "Exact normalized URL, DOI or arXiv ID overlap in the 25-per-source merged discovery sample; not semantic signal matching.",
        "retrieved_works_by_area": retrieved_by_area,
        "summary_by_area": {area: dict(counts) for area, counts in summary.items()},
        "cited_arxiv_ids": sorted(cited_arxiv),
        "cited_arxiv_ids_in_local_snapshot": (
            sorted(available_arxiv) if args.local_arxiv_dir else None
        ),
        "rows": row_results,
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for area, counts in summary.items():
        print(area, dict(counts), "retrieved", retrieved_by_area[area])


if __name__ == "__main__":
    main()
