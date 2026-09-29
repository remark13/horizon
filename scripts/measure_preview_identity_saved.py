"""Compare old key-only and new conservative merge on a saved GNN source set."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from saia.balanced_merge import merge
from saia.discovery import Publication, _openalex_publications
from saia.preview_identity import same_publication


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/gnn-retro-2017"
OUTPUT = ROOT / "outputs/priority-identity-audit/gnn-preview-v3.json"
NS = "{http://arxiv.org/OAI/arXiv/}"


def _arxiv_record(path: Path) -> Publication:
    record = ET.parse(path).getroot().find(f".//{NS}arXiv")
    if record is None:
        raise ValueError(f"No arXiv metadata in {path}")
    identifier = record.findtext(f"{NS}id") or ""
    authors = []
    for author in record.findall(f"{NS}authors/{NS}author"):
        first, last = author.findtext(f"{NS}forenames") or "", author.findtext(f"{NS}keyname") or ""
        authors.append(" ".join((first, last)).strip())
    title = " ".join((record.findtext(f"{NS}title") or "").split())
    from saia.discovery import _title_key
    doi = record.findtext(f"{NS}doi")
    url = f"https://arxiv.org/abs/{identifier}"
    return Publication(f"doi:{doi}" if doi else f"title:{_title_key(title)}", title,
                       record.findtext(f"{NS}abstract"), record.findtext(f"{NS}created") or "",
                       ("arxiv",), (url,), (url,), doi, tuple(authors))


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _old_key_merge(openalex: list[dict], arxiv: list[dict], limit: int) -> list[dict]:
    """Preserve the old source rotation and key-only duplicate decision."""
    queues = [iter(openalex), iter(arxiv)]
    seen, selected = set(), []
    exhausted = [False, False]
    while len(selected) < limit and not all(exhausted):
        for index, queue in enumerate(queues):
            if exhausted[index]:
                continue
            try:
                row = next(queue)
            except StopIteration:
                exhausted[index] = True
                continue
            if row["canonical_key"] not in seen:
                selected.append(row)
                seen.add(row["canonical_key"])
            if len(selected) >= limit:
                break
    return selected


def main() -> None:
    oa_file = RAW / "openalex/page_0001.json"
    arxiv_files = sorted((RAW / "arxiv").glob("record_*.xml"))
    oa = [asdict(item) for item in _openalex_publications(
        json.loads(oa_file.read_text())["results"], date(2017, 1, 1))]
    ax = [asdict(_arxiv_record(path)) for path in arxiv_files]
    branch = [{"branch_id": "gnn", "sources": {"openalex": oa, "arxiv": ax}}]
    current = merge(branch, max_results=15)
    old = _old_key_merge(oa, ax, limit=15)
    duplicate_pairs = [
        {"left": left["source_ids"], "right": right["source_ids"]}
        for i, left in enumerate(old) for right in old[i + 1:]
        if same_publication(left, right)
    ]
    report = {
        "role": "saved_real_source_preview_identity_diagnostic_not_accuracy_measurement",
        "source_sha256": {str(oa_file.relative_to(ROOT)): _sha(oa_file),
                          **{str(path.relative_to(ROOT)): _sha(path) for path in arxiv_files}},
        "input_counts": {"openalex": len(oa), "arxiv": len(ax)},
        "old_key_only": {"selected": len(old), "source_ids": [item["source_ids"] for item in old],
                         "duplicate_pairs_by_new_conservative_rule": duplicate_pairs},
        "conservative": {"selected": len(current["results"]),
                         "cross_key_duplicates_collapsed": current["cross_key_duplicates_collapsed"],
                         "source_ids": [item["source_ids"] for item in current["results"]],
                         "source_provenance": [item["source_provenance"] for item in current["results"]]},
        "caveat": "The preview is limited to one saved GNN page and three saved arXiv records; manual identity adjudication is not complete.",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") != rendered:
        raise FileExistsError(f"Existing audit differs: {OUTPUT}")
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "input_counts": report["input_counts"],
                      "old_selected": len(old), "new_selected": len(current["results"]),
                      "old_duplicate_pairs": len(duplicate_pairs),
                      "cross_key_duplicates_collapsed": current["cross_key_duplicates_collapsed"]}))


if __name__ == "__main__":
    main()
