"""Bounded, read-only arXiv retrievability check for published signal titles.

This is an oracle-title probe, not autonomous weak-signal detection or a
representative accuracy benchmark. The external titles are used as queries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION
from saia.local_arxiv_multibranch import scan
from saia.public_signals import CATALOG_PATH, load_catalog


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = {
    "jrc_weak_signals_2024": [
        "asynchronous federated learning", "decentralized federated learning",
        "federated reinforcement learning", "machine unlearning",
        "scientific machine learning", "tiny machine learning",
        "vertical federated learning", "multimodal hate speech detection",
        "large language models",  # broad-name stress control
    ],
    "jrc_weak_signals_2021": [
        "3D multi-object tracking", "DNS over HTTPS", "Earable computing",
        "Edge artificial intelligence", "Fog robotics", "Internet of space things",
        "Programmable wireless environment", "Wireless time sensitive network",
        "Directed Acyclic Graph",  # ambiguous-name stress control
    ],
    "espas_signal_cards_2026": [
        "XENOTRANSPLANTATION", "NEUROMORPHIC ENGINEERING",
        "HUMANOID ROBOTS AT WORK", "ROBOT METABOLISM",
    ],
}
CUTOFF = {
    "jrc_weak_signals_2024": 2025,  # detected in 2024; published 2025
    "jrc_weak_signals_2021": 2022,  # detected in 2021; published 2022
    "espas_signal_cards_2026": 2026,  # edition year, not detection date
}


def sample(catalog: dict) -> list[dict]:
    chosen = []
    for source, titles in SAMPLE.items():
        for title in titles:
            matches = [row for row in catalog["records"]
                       if row["source_id"] == source
                       and row["title"].casefold() == title.casefold()]
            if len(matches) != 1:
                raise ValueError(f"Pinned public title missing or ambiguous: {source}/{title}")
            chosen.append(matches[0])
    return chosen


def summarize(item: dict, branch: dict) -> dict:
    counts = {int(year): int(count) for year, count in branch["year_counts"].items()}
    cutoff = CUTOFF[item["source_id"]]
    pre = sum(count for year, count in counts.items() if year < cutoff)
    after = sum(count for year, count in counts.items() if year >= cutoff)
    latest_three = sum(counts.get(year, 0) for year in range(cutoff - 3, cutoff))
    prior_three = sum(counts.get(year, 0) for year in range(cutoff - 6, cutoff - 3))
    return {
        "source_id": item["source_id"], "public_signal_id": item["id"],
        "public_title": item["title"], "category": item["category"],
        "source_url": item["source_url"], "source_date": item["source_date"],
        "comparison_cutoff_year_exclusive": cutoff,
        "matching_version": branch["matching_version"],
        "matched_arxiv_total_2010_to_2026_08": branch["eligible_matches"],
        "matched_before_cutoff": pre, "matched_from_cutoff": after,
        "first_observed_year_since_2010": min(counts) if counts else None,
        "counts_by_v1_year": {str(year): counts[year] for year in sorted(counts)},
        "last_three_pre_cutoff": latest_three,
        "prior_three_pre_cutoff": prior_three,
        "raw_count_ratio_last_vs_prior": (
            round(latest_three / prior_three, 3) if prior_three else None
        ),
        "sample_publications": [
            {"title": work["title"], "arxiv_url": work["source_ids"][0],
             "published_at": work["published_at"]}
            for work in branch["works"][:3]
        ],
        "interpretation": "oracle_title_retrieval_only_not_weak_signal_validation",
    }


def report(rows: list[dict], mirror_report: dict, catalog_hash: str) -> str:
    counts = Counter((row["source_id"], row["matched_before_cutoff"] > 0)
                     for row in rows)
    lines = [
        "# Публичные сигналы: ограниченный тест поиска в arXiv",
        "",
        "Тест использует **названия уже опубликованных сигналов как поисковые фразы**. "
        "Он показывает, можно ли найти связанные публикации в закреплённом локальном "
        "arXiv; это не самостоятельное обнаружение сигнала SAIA и не измерение её точности.",
        "",
        f"Каталог: {len(load_catalog()['records'])} записей; в тесте: {len(rows)} заранее "
        f"выбранных названий. Отпечаток JSON-каталога SHA-256: `{catalog_hash}`. "
        f"Снимок arXiv: `{mirror_report['revision']}`, просмотрено "
        f"{mirror_report['scanned_rows']:,} записей.",
        "",
        "| Источник | Проверено | Название нашло хотя бы одну работу до года сравнения |",
        "|---|---:|---:|",
    ]
    for source, titles in SAMPLE.items():
        lines.append(f"| {source} | {len(titles)} | {counts[(source, True)]} |")
    lines += [
        "", "## Результат по карточкам", "",
        "Три последних и три предыдущих года приведены как **сырые количества**, "
        "не как доли всех публикаций и не как балл слабого сигнала.",
        "", "| Источник | Публичная карточка | До года сравнения | Последние 3 / предыдущие 3 года | После |",
        "|---|---|---:|---:|---:|",
    ]
    for row in rows:
        title = row["public_title"].replace("|", "\\|")
        lines.append(
            f"| {row['source_id']} | [{title}]({row['source_url']}) "
            f"| {row['matched_before_cutoff']} | {row['last_three_pre_cutoff']} / "
            f"{row['prior_three_pre_cutoff']} | {row['matched_from_cutoff']} |"
        )
    lines += [
        "", "## Что видно на контрольных случаях", "",
        "- `machine unlearning`: до 2025 года найдено 334 работы, из них 310 в "
        "2022–2024 против 24 в 2019–2021. Это сильный рост сырого числа работ, "
        "но без доли в области и проверки содержания ещё не статус слабого сигнала.",
        "- `large language models`: до 2025 года найдено 23 186 работ. Публичное "
        "название присутствует в каталоге JRC, но настолько широкую и массовую "
        "тему нельзя автоматически считать малозаметной в arXiv.",
        "- `Earable computing` и `Wireless time sensitive network` не нашли "
        "дословных работ до года сравнения. Это показывает предел буквального "
        "поиска и покрытия arXiv, а не ошибку JRC.",
        "- У `HUMANOID ROBOTS AT WORK` и `ROBOT METABOLISM` нашлась только одна "
        "работа до 2026 года на каждое название. Одной работы недостаточно "
        "для измерения устойчивого публикационного роста.",
        "", "## Как читать", "",
        "- Ноль означает отсутствие **точного нормализованного названия** в заголовках "
        "и аннотациях локального arXiv, а не отсутствие исследований или ложность чужого сигнала.",
        "- Положительное совпадение не доказывает, что публикация относится к смыслу "
        "карточки. Особенно осторожно трактовать широкие названия вроде `large language models` "
        "и `Directed Acyclic Graph`.",
        "- JRC 2024 использовал Scopus и PATSTAT, JRC 2021 — корпус рецензируемых "
        "научных публикаций; наш arXiv не эквивалентен их данным. ESPAS включает "
        "общественные и политические сигналы; выбранные здесь "
        "четыре карточки только технологические.",
        "- Текущий снимок arXiv хранит последнюю метаинформацию и дату v1, а не текст "
        "заголовка/аннотации в исторический момент. Поэтому это не строгий ретротест.",
        "- Исторический год сравнения для JRC взят из года выявления в названии "
        "отчёта; для ESPAS — год издания, не год выявления.",
        "- Для проверки самой SAIA следующий этап должен подавать широкий запрос по "
        "направлению, скрыть названия этих карточек от поискового плана, затем вручную "
        "сопоставлять ТОП-15 с первоисточниками и оценивать релевантность.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output directory already exists; previous pilot is immutable")
    catalog = load_catalog()
    chosen = sample(catalog)
    specs = [
        {"branch_id": item["id"], "included_phrases": [item["title"]],
         "excluded_phrases": [], "matching_version": ORTHOGRAPHIC_MATCHING_VERSION}
        for item in chosen
    ]
    result = scan(args.mirror, specs, date(2010, 1, 1), date(2026, 9, 1), 3)
    if result["scanned_rows"] != result["inventory_rows"]:
        raise ValueError("Partial arXiv traversal is not a complete pilot")
    by_id = {branch["branch_id"]: branch for branch in result["branches"]}
    rows = [summarize(item, by_id[item["id"]]) for item in chosen]
    catalog_hash = hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest()
    payload = {
        "version": "public-catalog-arxiv-retrievability-v1",
        "catalog_sha256": catalog_hash,
        "catalog_total": len(catalog["records"]),
        "sample_policy": "predeclared_tech_titles_9_jrc2024_9_jrc2021_4_espas2026_with_broad_controls",
        "sample_size": len(rows),
        "source_revision": result["revision"],
        "source_inventory_rows": result["inventory_rows"],
        "source_scanned_rows": result["scanned_rows"],
        "matching_version": ORTHOGRAPHIC_MATCHING_VERSION,
        "as_of_date_exclusive": "2026-09-01",
        "oracle_titles_used_as_queries": True,
        "full_saia_pipeline_run": False,
        "weak_signal_accuracy_measured": False,
        "rows": rows,
    }
    args.output.mkdir(parents=True)
    (args.output / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "Отчёт.md").write_text(
        report(rows, result, catalog_hash), encoding="utf-8")
    print(json.dumps({
        "sample_size": len(rows),
        "pre_cutoff_nonzero": sum(row["matched_before_cutoff"] > 0 for row in rows),
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
