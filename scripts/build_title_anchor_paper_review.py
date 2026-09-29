"""Freeze a label-free paper-level relevance sample for the title-anchor pilot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random

from saia.controlled_collection import sha256_file
from scripts.audit_title_anchor_cohorts import _load_works


VERSION = "title-anchor-paper-review-v2"
SEED = 20260927
TOP_DISPLAY_GROUPS = 5
POSTHOC_CONTROL_PHRASES = ("task allocation", "visual inertial")
PER_STRATUM = 2


def select_cases(audit: dict) -> list[dict]:
    """Stratified, stable selection without prefilled relevance judgements."""
    if (audit.get("version") != "title-anchor-cohort-audit-v1"
            or audit.get("production_changed") is not False):
        raise ValueError("Expected frozen diagnostic cohort audit")
    by_phrase = {row["phrase_en"]: row for row in audit["rows"]}
    top = [group["representative_phrase_en"]
           for group in audit["top15_display_groups"][:TOP_DISPLAY_GROUPS]]
    phrases = top + list(POSTHOC_CONTROL_PHRASES)
    if len(set(phrases)) != len(phrases):
        raise ValueError("Review phrases must be unique")
    selected = []
    used_ids = set()
    for phrase in phrases:
        row = by_phrase[phrase]
        title = set(row["title_anchored_ids"])
        strata = (("title", sorted(title)),
                  ("abstract_only", sorted(set(row["expanded_ids"]) - title)))
        for stratum, candidates in strata:
            rng = random.Random(f"{SEED}:{audit['expansion_sha256']}:{phrase}:{stratum}")
            available = [identifier for identifier in candidates if identifier not in used_ids]
            if len(available) < PER_STRATUM:
                raise ValueError(f"Insufficient distinct papers for {phrase}/{stratum}")
            for identifier in rng.sample(available, PER_STRATUM):
                used_ids.add(identifier)
                selected.append({"phrase_en": phrase, "arxiv_id": identifier,
                                 "selection_stratum": stratum,
                                 "selection_role": ("top_display_group" if phrase in top
                                                    else "posthoc_known_line_control")})
    if len(selected) != len(phrases) * 2 * PER_STRATUM:
        raise ValueError("Unexpected review sample size")
    random.Random(SEED + 1).shuffle(selected)
    return selected


def build_packets(audit: dict, works: dict[str, dict], *, audit_sha256: str) -> tuple[dict, dict]:
    selected = select_cases(audit)
    cases, manifest_rows = [], []
    for item in selected:
        identifier = item["arxiv_id"]
        case_id = "paper-" + hashlib.sha256(
            f"{audit_sha256}:{item['phrase_en']}:{identifier}".encode()).hexdigest()[:16]
        work = works[identifier]
        cases.append({"case_id": case_id, "proposed_line_en": item["phrase_en"],
                      "paper": {"arxiv_id": identifier,
                                "title": work["title"],
                                "abstract": work["abstract"],
                                "source_url": f"https://arxiv.org/abs/{identifier}"},
                      "review_fields": {
                          "proposed_line_is_specific_technology": None,
                          "own_research_result_matches_line": None,
                          "bas_is_research_object": None,
                          "publication_role": None,
                          "exact_supporting_quote": None,
                          "reason": None,
                      }})
        manifest_rows.append({"case_id": case_id, **item})
    now = datetime.now(timezone.utc).isoformat()
    blind = {"version": VERSION, "created_at": now,
             "audit_sha256": audit_sha256, "case_count": len(cases),
             "review_instructions": {
                 "proposed_line_is_specific_technology": "Фраза обозначает достаточно конкретный технический принцип или исследовательскую задачу для одной карточки, а не целую отрасль, рынок или набор разных методов?",
                 "own_research_result_matches_line": "Описывает ли собственный исследовательский результат статьи эту конкретную технологическую линию, а не только фон или возможное применение?",
                 "bas_is_research_object": "БАС/дрон является исследуемым объектом, а не только примером или мотивацией?",
                 "publication_role": ["primary_result", "review", "application", "unclear"],
                 "answers": ["yes", "no", "unclear"],
                 "evidence": "Укажите дословный фрагмент названия или аннотации; если текста недостаточно, ответьте unclear. Два проверяющих размечают независимо.",
                 "dependency": "Если предложенная линия слишком широка, оцените саму фразу как no; не считайте совпадение статьи подтверждением единого слабого сигнала.",
             },
             "cases": cases,
             "limitations": [
                 "Это выборка статей, не оценка качества топ-15 карточек.",
                 "Две контрольные линии были известны разработчику до отбора, а не открыты вслепую.",
                 "Текст текущего снимка arXiv может отличаться от ранней версии публикации.",
                 "Оценки не заполнены; без них точность не измерена.",
             ]}
    manifest = {"version": VERSION + "-selection-manifest", "created_at": now,
                "audit_sha256": audit_sha256, "seed": SEED,
                "top_display_group_count": TOP_DISPLAY_GROUPS,
                "posthoc_control_phrases": list(POSTHOC_CONTROL_PHRASES),
                "per_phrase_title_and_abstract_only": PER_STRATUM,
                "rows": manifest_rows, "not_for_reviewers": True}
    return blind, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--blind-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    args = parser.parse_args()
    if args.blind_output.exists() or args.manifest_output.exists():
        raise FileExistsError("Review packets are immutable")
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    if sha256_file(args.index / "manifest.json") != audit["index_manifest_sha256"]:
        raise ValueError("Pinned index manifest changed")
    chosen = select_cases(audit)
    ids = {row["arxiv_id"] for row in chosen}
    blind, manifest = build_packets(audit, _load_works(args.index / "index.sqlite3", ids),
                                    audit_sha256=sha256_file(args.audit))
    for path, payload in ((args.blind_output, blind),
                          (args.manifest_output, manifest)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                   indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"review_cases": blind["case_count"],
                      "labels_prefilled": False}))


if __name__ == "__main__":
    main()
