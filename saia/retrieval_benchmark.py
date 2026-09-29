"""Development-only retrieval benchmark over the pinned local arXiv mirror.

Target identifiers are applied after each source-agnostic selection strategy.
The result measures collection recall only.  It is not weak-signal precision,
topic recovery, future growth, or market validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from saia import db, evaluation_catalog, runs
from saia.arxiv_metadata import first_submission, matches_controlled_plan, validate_schema
from saia.local_arxiv_search import _substring_mask, policy, validate_inventory
from saia.query_expansion import digest


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "retrieval-benchmark.v0.4.18.yaml"
REPORT_VERSION = "retrieval-benchmark-0.4.18"


def _file_sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _normal(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", _normal(value)))


def wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total <= 0 or not 0 <= successes <= total:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total)
    ) / denominator
    return [round(max(0.0, centre - margin), 4),
            round(min(1.0, centre + margin), 4)]


def select_automatic_terms(original_query: str, suggestions: list[dict],
                           max_added_terms: int) -> tuple[list[str], list[dict]]:
    """Deterministic proposal selection without target names or outcomes."""
    if not original_query.strip() or not 1 <= max_added_terms <= 10:
        raise ValueError("Некорректные параметры автоматического расширения.")
    ranked = sorted(
        suggestions,
        key=lambda item: (-int(item.get("document_support") or 0),
                          _normal(item.get("phrase") or "")),
    )
    selected = [original_query.strip()]
    decisions: list[dict] = []
    selected_tokens = [_tokens(selected[0])]
    for item in ranked:
        phrase = str(item.get("phrase") or "").strip()
        tokens = _tokens(phrase)
        if not tokens or int(item.get("document_support") or 0) <= 0:
            continue
        contained = any(
            set(tokens).issuperset(existing) or set(tokens).issubset(existing)
            for existing in selected_tokens
        )
        decisions.append({
            "phrase": phrase,
            "document_support": int(item.get("document_support") or 0),
            "selected": not contained,
            "reason": "selected_by_support" if not contained else "token_containment_suppressed",
        })
        if contained:
            continue
        selected.append(phrase)
        selected_tokens.append(tokens)
        if len(selected) == max_added_terms + 1:
            break
    if len(selected) != max_added_terms + 1:
        raise ValueError("В предложении недостаточно независимых фраз для стратегии.")
    return selected, decisions


def development_targets(catalog: dict, as_of_date: str) -> tuple[list[dict], set[str]]:
    cases = []
    identifiers: set[str] = set()
    for item in catalog["cases"]:
        if item["split"] != "development" or item["as_of_date"] != as_of_date:
            continue
        if item["kind"] not in {"research_line_positive_proposal", "ambiguous_line"}:
            continue
        row = {
            "case_id": item["case_id"], "family_id": item["family_id"],
            "title": item["title"], "kind": item["kind"],
            "arxiv_ids": list(item["arxiv_ids"]),
        }
        cases.append(row)
        identifiers.update(row["arxiv_ids"])
    if not cases or not identifiers:
        raise ValueError("Для даты среза нет development-контролей retrieval.")
    return cases, identifiers


def _load_inputs(config_path: Path) -> tuple[dict, dict, dict, dict, list[str], list[dict]]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if config.get("version") != "retrieval-benchmark-config-0.4.18":
        raise ValueError("Неизвестная версия retrieval benchmark config.")
    catalog_path = (ROOT / config["catalog_path"]).resolve()
    parent_path = (ROOT / config["parent_mission_path"]).resolve()
    if _file_sha256(catalog_path) != config["catalog_sha256"]:
        raise ValueError("Хеш development-каталога изменился.")
    if _file_sha256(parent_path) != config["parent_mission_sha256"]:
        raise ValueError("Хеш исходной категорийной миссии изменился.")
    catalog = evaluation_catalog.load(catalog_path)
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT payload,content_sha256 FROM query_version "
            "WHERE query_version_id=%s AND mission_id=%s",
            (config["query_version_id"], config["mission_id"]),
        )
        query_row = cur.fetchone()
        if not query_row:
            raise ValueError("Закреплённая версия запроса не найдена.")
        profile, profile_hash = query_row
        if profile_hash != config["query_profile_sha256"] or digest(profile) != profile_hash:
            raise ValueError("Хеш закреплённого профиля запроса изменился.")
        cur.execute(
            "SELECT payload,content_sha256 FROM query_expansion_proposal "
            "WHERE mission_id=%s AND content_sha256=%s",
            (config["mission_id"], config["proposal_sha256"]),
        )
        proposal_row = cur.fetchone()
        if not proposal_row or digest(proposal_row[0]) != proposal_row[1]:
            raise ValueError("Закреплённое предложение расширения не найдено или изменилось.")
    proposal = proposal_row[0]
    automatic_terms, term_decisions = select_automatic_terms(
        profile["controlled_search_plan"]["original_query"],
        proposal["suggestions"],
        int(config["automatic_expansion"]["max_added_terms"]),
    )
    return config, catalog, parent, profile, automatic_terms, term_decisions


def evaluate(config_path: Path, mirror_dir: Path) -> dict:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    config, catalog, parent, profile, automatic_terms, term_decisions = _load_inputs(config_path)
    plan = profile["controlled_search_plan"]
    start = date.fromisoformat(plan["date_from"])
    cutoff = date.fromisoformat(plan["as_of_date"])
    if cutoff.isoformat() != config["development_as_of_date"]:
        raise ValueError("Дата development-контроля не совпадает с профилем запроса.")
    cases, target_ids = development_targets(catalog, cutoff.isoformat())
    categories = set(parent["query"]["arxiv_categories"])
    if not categories:
        raise ValueError("Исходная категорийная граница пуста.")
    automatic_plan = {
        "included_terms": automatic_terms,
        "exclusions": list(plan.get("exclusions") or []),
        "matching_version": plan.get("matching_version", "literal-phrase-0.4.6"),
    }
    cfg = policy()
    files = validate_inventory(str(mirror_dir.resolve()), cfg["expected_files"],
                               cfg["expected_rows"])
    strategies = {
        "exact_approved": {"selected_ids": set(), "invalid_dates": 0},
        "automatic_expansion": {"selected_ids": set(), "invalid_dates": 0},
        "category_parent": {"selected_ids": set(), "invalid_dates": 0},
    }
    target_metadata: dict[str, dict] = {}
    scanned = 0
    category_pattern = r"(^| )(?:(?:" + "|".join(
        re.escape(value) for value in sorted(categories)) + r"))( |$)"
    target_values = pa.array(sorted(target_ids))
    for path in files:
        parquet = pq.ParquetFile(path)
        validate_schema(parquet.schema_arrow.names)
        for batch in parquet.iter_batches(
                batch_size=8192, columns=["id", "title", "abstract", "categories", "versions"]):
            scanned += batch.num_rows
            table = pa.Table.from_batches([batch])
            exact_mask = pc.fill_null(_substring_mask(table, list(plan["included_terms"])), False)
            auto_mask = pc.fill_null(_substring_mask(table, automatic_terms), False)
            category_mask = pc.match_substring_regex(
                pc.fill_null(table["categories"], ""), category_pattern)
            target_mask = pc.is_in(table["id"], value_set=target_values)
            union = pc.or_kleene(pc.or_kleene(exact_mask, auto_mask),
                                 pc.or_kleene(category_mask, target_mask))
            table = table.append_column("_exact_coarse", exact_mask)
            table = table.append_column("_auto_coarse", auto_mask)
            table = table.append_column("_category_match", category_mask)
            for row in table.filter(pc.fill_null(union, False)).to_pylist():
                identifier = str(row.get("id") or "").strip()
                # The vectorized substring masks are only coarse prefilters.
                # Run the authoritative boundary/exclusion regex only where
                # the corresponding coarse mask matched.
                exact = bool(row.pop("_exact_coarse")) and matches_controlled_plan(row, plan)
                automatic = bool(row.pop("_auto_coarse")) and matches_controlled_plan(
                    row, automatic_plan)
                category = bool(row.pop("_category_match"))
                try:
                    published = date.fromisoformat(first_submission(row.get("versions")))
                except ValueError:
                    for name, matched in (("exact_approved", exact),
                                          ("automatic_expansion", automatic),
                                          ("category_parent", category)):
                        strategies[name]["invalid_dates"] += int(matched)
                    if identifier in target_ids:
                        target_metadata[identifier] = {"status": "invalid_v1_date"}
                    continue
                in_period = start <= published < cutoff
                for name, matched in (("exact_approved", exact),
                                      ("automatic_expansion", automatic),
                                      ("category_parent", category)):
                    if matched and in_period:
                        selected_ids = strategies[name]["selected_ids"]
                        if identifier in selected_ids:
                            raise ValueError(f"Повторён arXiv ID в стратегии {name}: {identifier}")
                        selected_ids.add(identifier)
                if identifier in target_ids:
                    target_metadata[identifier] = {
                        "status": "eligible" if in_period else "outside_period",
                        "published_at": published.isoformat(),
                        "title": " ".join(str(row.get("title") or "").split()),
                        "categories": str(row.get("categories") or "").split(),
                    }
    missing_metadata = sorted(target_ids - set(target_metadata))
    if missing_metadata:
        raise ValueError("Контрольные arXiv ID отсутствуют в pinned mirror: " + ", ".join(missing_metadata))

    result_strategies = {}
    hard_limit = int(config["limits"]["full_job_hard_records"])
    default_limit = int(config["limits"]["full_job_default_records"])
    for name, raw in strategies.items():
        selected_ids = raw.pop("selected_ids")
        hits = sorted(target_ids.intersection(selected_ids))
        per_case = []
        for case in cases:
            case_hits = sorted(set(case["arxiv_ids"]).intersection(selected_ids))
            per_case.append({
                **case,
                "found": case_hits,
                "missing": sorted(set(case["arxiv_ids"]) - set(case_hits)),
                "recall": round(len(case_hits) / len(case["arxiv_ids"]), 4),
            })
        volume = len(selected_ids)
        result_strategies[name] = {
            "selected_records": volume,
            "control_hits": len(hits),
            "control_references": len(target_ids),
            "control_recall": round(len(hits) / len(target_ids), 4),
            "control_recall_wilson_95": wilson_interval(len(hits), len(target_ids)),
            "control_density_not_precision": round(len(hits) / volume, 8) if volume else None,
            "fits_default_full_job_limit": volume <= default_limit,
            "fits_hard_full_job_limit": volume <= hard_limit,
            "invalid_selected_dates": raw["invalid_dates"],
            "found_control_ids": hits,
            "missing_control_ids": sorted(target_ids - set(hits)),
            "fully_recovered_cases": sum(item["recall"] == 1.0 for item in per_case),
            "partly_recovered_cases": sum(0.0 < item["recall"] < 1.0 for item in per_case),
            "missed_cases": sum(item["recall"] == 0.0 for item in per_case),
            "cases": per_case,
        }
    exact_count = result_strategies["exact_approved"]["selected_records"]
    if exact_count != 4223:
        raise ValueError(f"Exact strategy drifted: expected 4223, got {exact_count}")
    for item in result_strategies.values():
        item["volume_multiple_vs_exact"] = round(
            item["selected_records"] / exact_count, 4
        )
    return {
        "version": REPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": _file_sha256(config_path),
        "code_version": runs.code_version(),
        "runtime": runs.runtime_snapshot(),
        "mission_id": config["mission_id"],
        "query_version_id": config["query_version_id"],
        "period": {"from": start.isoformat(), "as_of_exclusive": cutoff.isoformat()},
        "source": {
            "dataset": cfg["dataset"], "revision": cfg["revision"],
            "inventory_files": len(files), "inventory_rows": cfg["expected_rows"],
            "scanned_rows": scanned,
        },
        "development_controls": {
            "split": "development",
            "reserved_evaluated": False,
            "case_kinds": ["research_line_positive_proposal", "ambiguous_line"],
            "cases": len(cases), "families": len({item["family_id"] for item in cases}),
            "unique_references": len(target_ids),
            "reference_metadata": target_metadata,
        },
        "strategies": {
            "exact_approved": {
                "definition": {"included_terms": plan["included_terms"],
                               "exclusions": plan.get("exclusions") or []},
                **result_strategies["exact_approved"],
            },
            "automatic_expansion": {
                "definition": {"included_terms": automatic_terms,
                               "exclusions": plan.get("exclusions") or [],
                               "proposal_sha256": config["proposal_sha256"],
                               "selection_decisions": term_decisions,
                               "target_labels_used": False},
                **result_strategies["automatic_expansion"],
            },
            "category_parent": {
                "definition": {"arxiv_categories": sorted(categories),
                               "target_labels_used": False},
                **result_strategies["category_parent"],
            },
        },
        "summary": {
            "best_recall_strategy": max(
                result_strategies,
                key=lambda key: (result_strategies[key]["control_recall"],
                                 -result_strategies[key]["selected_records"]),
            ),
            "precision_measured": False,
            "topic_recovery_measured": False,
            "weak_signal_detection_measured": False,
            "market_success_measured": False,
        },
        "limitations": [
            "Контрольные ссылки являются proposal/development, а не экспертным gold standard.",
            "Reserved-кейсы не оценивались, но существующий каталог не является независимо ослеплённым holdout.",
            "Доля контрольных ссылок в корпусе не является precision: неизвестно, сколько остальных работ релевантны.",
            "Текущие title/abstract из зеркала не восстанавливают исторические версии текста.",
            "Высокий recall ценой корпуса выше лимита ещё не является работоспособной стратегией продукта.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Сравнить стратегии retrieval до кластеризации")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--export", type=Path, required=True)
    args = parser.parse_args()
    if args.export.exists():
        raise ValueError("Отчёт уже существует; воспроизводимый результат не перезаписывается.")
    if not args.export.parent.is_dir():
        raise ValueError("Каталог отчёта должен существовать.")
    report = evaluate(args.config.resolve(), args.mirror.resolve())
    args.export.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({
        name: {"selected": item["selected_records"], "recall": item["control_recall"]}
        for name, item in report["strategies"].items()
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
