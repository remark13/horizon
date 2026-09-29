"""Create a blind, deterministic article-directness packet for two BAS lines."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from saia.controlled_collection import sha256_file
from saia.priority_concept_search import exact_concept_search


PERIODS = (("early", "2016-09-01", "2021-09-01"),
           ("middle", "2021-09-01", "2024-09-01"),
           ("recent", "2024-09-01", "2026-09-01"))


def _stable_pick(rows: list[dict], seed: str, count: int) -> list[dict]:
    return sorted(rows, key=lambda row: hashlib.sha256(
        f"{seed}:{row['arxiv_id']}".encode()).hexdigest())[:count]


def build(config_path: Path) -> tuple[dict, dict]:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if cfg.get("version") != "bas-narrow-temporal-diagnostic-v1":
        raise ValueError("Only the frozen BAS line plan is accepted")
    root = Path(__file__).resolve().parents[1]
    index_dir = (root / cfg["source"]["index_manifest"]).parent
    if sha256_file(index_dir / "manifest.json") != cfg["source"]["index_manifest_sha256"]:
        raise ValueError("Pinned index manifest changed")
    conn = sqlite3.connect(f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro",
                           uri=True)
    conn.row_factory = sqlite3.Row
    items, selection = [], []
    try:
        for line in cfg["lines"]:
            search = exact_concept_search(index_dir, {
                "concept_groups": line["concept_groups"],
                "exclusions": line["exclusions"],
                "matching_version": cfg["matching_version"],
                "date_from": cfg["period"]["date_from"],
                "as_of_date": cfg["period"]["as_of_date"]}, max_matches=5000)
            rows = []
            for identifier in search["arxiv_ids"]:
                row = conn.execute(
                    "SELECT arxiv_id,first_submission_date,title,abstract FROM works "
                    "WHERE arxiv_id=?", (identifier,)).fetchone()
                if row is None:
                    raise ValueError("Selected arXiv record absent from pinned index")
                rows.append(dict(row))
            for period_id, start, cutoff in PERIODS:
                eligible = [row for row in rows if start <= row["first_submission_date"] < cutoff]
                if len(eligible) < 4:
                    raise ValueError(f"Too few papers for {line['line_id']} / {period_id}")
                chosen = _stable_pick(
                    eligible, f"{sha256_file(config_path)}:{line['line_id']}:{period_id}", 4)
                for row in chosen:
                    item_id = hashlib.sha256(
                        f"{line['line_id']}:{row['arxiv_id']}".encode()).hexdigest()[:16]
                    items.append({
                        "item_id": item_id, "line_id": line["line_id"],
                        "line_label_ru": line["label_ru"],
                        "arxiv_id": row["arxiv_id"],
                        "url": f"https://arxiv.org/abs/{row['arxiv_id']}",
                        "first_submission_date": row["first_submission_date"],
                        "title": " ".join((row["title"] or "").split()),
                        "abstract": " ".join((row["abstract"] or "").split()),
                        "annotation": {"directness": None,
                                       "technical_mechanism": None,
                                       "reason": None},
                    })
                    selection.append({"item_id": item_id, "line_id": line["line_id"],
                                      "period_id": period_id,
                                      "arxiv_id": row["arxiv_id"]})
    finally:
        conn.close()
    items.sort(key=lambda item: item["item_id"])
    return ({
        "version": "bas-narrow-directness-review-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": sha256_file(config_path),
        "instructions_ru": (
            "По названию и аннотации оцените, сообщает ли работа собственный результат "
            "непосредственно по указанной линии. Не считайте обзор, фоновые упоминания "
            "или применение другого метода прямым результатом. При недостатке текста "
            "выберите 'неясно'. Отдельно кратко назовите технический механизм, если он ясен."
        ),
        "directness_choices": ["direct_primary_result", "review_or_survey",
                               "background_mention_or_adjacent", "off_topic", "unclear"],
        "items": items,
        "scope_limit": "Developer diagnostic packet; not independent weak-signal ground truth",
    }, {
        "version": "bas-narrow-directness-selection-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": sha256_file(config_path),
        "selection_policy": "four smallest SHA-256 IDs per line and fixed time stratum",
        "periods": PERIODS,
        "selection": selection,
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    args = parser.parse_args()
    if args.packet.exists() or args.selection.exists():
        raise FileExistsError("Blind packet and selection manifest are immutable")
    packet, selection = build(args.config)
    for path, value in ((args.packet, packet), (args.selection, selection)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"items": len(packet["items"]),
                      "lines": len(set(item["line_id"] for item in packet["items"]))}))


if __name__ == "__main__":
    main()
