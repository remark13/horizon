"""Create a label-free expert review packet from untouched BAS paper pairs.

Sampling is stratified by saved SPECTER2 similarity within each of two broad
cards, excludes all development pairs, and never presents similarity or any
model/developer judgement to reviewers. The separate selection manifest is
for evaluation only; it is not an expert-facing artifact.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from itertools import combinations
import json
from pathlib import Path
import random

import numpy as np

from saia.controlled_collection import sha256_file
from scripts.probe_bas_task_pair_similarity import (
    _load_vectors, _source, _validated_pairs, _works,
)


VERSION = "bas-blind-pair-review-v2"
SEED = 20260926
PER_CARD_PER_STRATUM = 5
STRATA = ("low", "middle", "high")


def _arxiv_url(work: dict) -> str | None:
    for identifier in work.get("identifiers") or []:
        if identifier.get("kind") == "arxiv" and identifier.get("value"):
            return "https://arxiv.org/abs/" + identifier["value"]
    return None


def select_pairs(config: dict, source: dict,
                 vectors: dict[int, np.ndarray]) -> list[dict]:
    works, card_of = _works(config, source)
    annotated = _validated_pairs(config, works, card_of)
    if set(vectors) != set(works):
        raise ValueError("Missing saved paper vectors")
    excluded = {tuple(sorted((row["a"], row["b"]))) for row in annotated}
    selected = []
    for card_rank in (1, 2):
        ids = sorted(identifier for identifier in works if card_of[identifier] == card_rank)
        candidates = []
        for a, b in combinations(ids, 2):
            if (a, b) in excluded:
                continue
            similarity = float(np.dot(vectors[a], vectors[b]))
            if not np.isfinite(similarity):
                raise ValueError("Nonfinite pair similarity")
            candidates.append((similarity, a, b))
        candidates.sort(key=lambda row: (row[0], row[1], row[2]))
        if len(candidates) < 3 * PER_CARD_PER_STRATUM:
            raise ValueError("Not enough unseen pairs in card")
        for index, stratum in enumerate(STRATA):
            start = index * len(candidates) // 3
            stop = (index + 1) * len(candidates) // 3
            bucket = candidates[start:stop]
            rng = random.Random(SEED + card_rank * 10 + index)
            for similarity, a, b in rng.sample(bucket, PER_CARD_PER_STRATUM):
                selected.append({"a": a, "b": b, "card_rank": card_rank,
                                 "stratum": stratum,
                                 "specter_cosine": round(similarity, 6)})
    selected.sort(key=lambda row: (row["card_rank"], row["stratum"],
                                   row["a"], row["b"]))
    if len(selected) != 30 or len({(r["a"], r["b"]) for r in selected}) != 30:
        raise ValueError("Review sample is incomplete or duplicated")
    return selected


def build_packets(config: dict, source: dict, vectors: dict[int, np.ndarray]) -> tuple[dict, dict]:
    works, _card_of = _works(config, source)
    selected = select_pairs(config, source, vectors)
    rng = random.Random(SEED + 1000)
    rng.shuffle(selected)
    cases, manifest_rows = [], []
    for row in selected:
        a, b = row["a"], row["b"]
        case_id = "bas-pair-" + hashlib.sha256(
            f"{config['source_sha256']}:{a}:{b}".encode()
        ).hexdigest()[:16]
        display_ids = (a, b) if rng.random() < 0.5 else (b, a)
        sides = []
        for identifier in display_ids:
            work = works[identifier]
            sides.append({"work_id": identifier, "title": work["title"],
                          "abstract": work.get("abstract") or "",
                          "source_url": _arxiv_url(work)})
        cases.append({"case_id": case_id, "paper_a": sides[0],
                      "paper_b": sides[1],
                      "review_fields": {
                          "same_research_problem": None,
                          "same_technical_mechanism": None,
                          "one_signal_line": None,
                          "evidence_quote_a": None,
                          "evidence_quote_b": None,
                          "reason": None,
                      }})
        manifest_rows.append({"case_id": case_id, **row,
                              "display_a": display_ids[0],
                              "display_b": display_ids[1]})
    now = datetime.now(timezone.utc).isoformat()
    blind = {
        "version": VERSION, "created_at": now,
        "source_sha256": config["source_sha256"],
        "review_instructions": {
            "allowed_answers": {"yes": "да", "no": "нет", "unclear": "неясно по тексту"},
            "questions": {
                "same_research_problem": "Статьи решают одну конкретную исследовательскую задачу, а не просто относятся к БАС?",
                "same_technical_mechanism": "В статьях применён одинаковый или явно родственный технический принцип?",
                "one_signal_line": "Можно ли считать эти работы свидетельствами одной узкой технологической линии?",
            },
            "rule": "Два эксперта независимо читают название и аннотацию каждой статьи. Если текста недостаточно — отметьте «неясно». Приведите фрагменты обеих статей в обоснование. По этому пакету нельзя делать вывод о рыночном внедрении или росте тренда.",
        },
        "cases": cases,
        "limitations": [
            "Только две широкие карточки БАС; выборка не представляет все десять направлений",
            "Выборка пар внутри карточек стратифицирована по сходству и не является оценкой Precision@15",
            "Нынешние аннотации могут отражать поздние версии arXiv; проверяется только связность",
            "Оценки экспертов не заполнены заранее и не подразумеваются",
        ],
    }
    manifest = {
        "version": VERSION + "-selection-manifest",
        "created_at": now,
        "source_sha256": config["source_sha256"],
        "method": "five pseudo-random unseen pairs from each SPECTER2 similarity tertile in each of the first two BAS cards",
        "seed": SEED,
        "excluded_developer_pair_count": len(config["pairs"]),
        "selected_pair_count": len(manifest_rows),
        "rows": manifest_rows,
        "not_for_reviewers": True,
    }
    return blind, manifest


def run(config_path: Path) -> tuple[dict, dict]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source = _source(config)
    works, card_of = _works(config, source)
    _validated_pairs(config, works, card_of)
    blind, manifest = build_packets(config, source, _load_vectors(config, works))
    manifest["config_sha256"] = sha256_file(config_path)
    return blind, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--blind-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    args = parser.parse_args()
    if args.blind_output.exists() or args.manifest_output.exists():
        raise FileExistsError("Review packets are immutable")
    blind, manifest = run(args.config)
    for path, payload in ((args.blind_output, blind),
                          (args.manifest_output, manifest)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                   indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(blind["cases"]),
                      "reviewer_labels_prefilled": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
