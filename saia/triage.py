"""Deterministic automatic-candidate ranking for a saved score run.

The queue is deliberately not a forecast, market ranking or a second signal
classifier. It orders already saved cards by how much scientific evidence is
known. Expert validation is a later optional action and does not gate display.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math


CORE_SIGNAL_GATES = frozenset({
    "G2_novelty",
    "G3_persistence",
    "G3_consecutive",
    "G4_momentum",
    "G4_positive_share_slope",
    "G4_positive_share_change",
    "G_coherence",
})

CHECK_LABELS = {
    "G0_volume": "число публикаций",
    "G0_concentration": "независимость организаций",
    "G0_independent_orgs": "число независимых организаций",
    "G1_publication_prevalence": "распространённость темы",
    "G2_novelty": "новизна темы",
    "G3_persistence": "устойчивость публикаций",
    "G3_consecutive": "последовательность активных периодов",
    "G3_independent_teams": "независимость исследовательских групп",
    "G4_momentum": "темп роста относительно других тем",
    "G4_positive_share_slope": "рост доли публикаций",
    "G4_positive_share_change": "изменение доли публикаций",
    "G6_primary_sources": "проверка первоисточников",
    "G_coherence": "связность публикаций в теме",
    "G_coherence_calibration": "проверка порога связности",
    "G_coverage": "сопоставимость покрытия",
    "G_embedding_coverage": "полнота анализа текстов",
}


def _finite(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _band(failed: list[str], unknown: list[str], core_passes: int) -> tuple[str, str]:
    if not failed:
        return (
            "verify_missing_evidence",
            "Ни одна обязательная проверка не опровергла тему. Неизвестные "
            "значения снижают уверенность автоматической оценки.",
        )
    if core_passes >= 4 and len(failed) <= 2:
        return (
            "monitor_early_acceleration",
            "Несколько признаков нового роста уже наблюдаются, но одна или две "
            "обязательные проверки пока не пройдены.",
        )
    if unknown:
        return (
            "resolve_and_recheck",
            "Есть непройденные и неизвестные проверки; кандидат показан с "
            "пониженной уверенностью.",
        )
    return (
        "lower_priority_recheck",
        "Тема сохранена для повторной проверки, но сейчас не проходит несколько "
        "обязательных правил слабого сигнала.",
    )


def _screening(card: dict, failed: list[str], unknown: list[str]) -> tuple[int, dict]:
    """Explain the observed publication evidence without changing a saved score."""
    series = ((card.get("metrics") or {}).get("publication_series") or {})
    slope = _finite(series.get("share_slope_per_window"))
    comparable = series.get("coverage_comparable") is True
    failed_names = [CHECK_LABELS.get(name, name) for name in failed]
    unknown_names = [CHECK_LABELS.get(name, name) for name in unknown]
    title_diagnostics = (((card.get("metrics") or {}).get("observed") or {})
                         .get("paper_title_diagnostics") or {})
    title_matches = title_diagnostics.get("title_matches") or {}
    total_works = title_diagnostics.get("total_works")
    scope_warning = None
    if (isinstance(total_works, int) and total_works > 0
            and title_matches.get("label_too_broad_for_title_check") == total_works):
        scope_warning = (
            "Название темы слишком широкое: по названиям статей нельзя проверить, "
            "описывают ли они одну конкретную технологию."
        )
    context_hints = title_diagnostics.get("context_hints") or {}
    acceptance_titles = context_hints.get("acceptance_or_market_context")
    context_warning = None
    if (isinstance(total_works, int) and total_works >= 3
            and isinstance(acceptance_titles, int)
            and acceptance_titles > total_works / 2):
        context_warning = (
            "Большинство названий работ относятся к восприятию или принятию "
            "технологии; их роль как технических исследований не установлена."
        )
    if comparable and slope is not None and slope <= 0:
        state = "growth_not_confirmed"
        label = "Рост не подтверждён"
        explanation = (
            "За последние сопоставимые периоды не видно устойчивого роста "
            "доли публикаций. Тема сохранена для проверки, но сейчас "
            "не должна трактоваться как растущий слабый сигнал."
        )
        priority = 4
    elif failed:
        state = "mixed_evidence"
        label = "Есть противоречия"
        explanation = (
            "Тема найдена в публикациях, но одна или несколько обязательных "
            "проверок слабого сигнала не пройдены."
        )
        priority = 2
    elif scope_warning and comparable and slope is not None and slope > 0:
        state = "insufficient_data"
        label = "Данных недостаточно"
        explanation = (
            "Доля публикаций в тематическом кластере растёт, но пока "
            "не установлено, что статьи описывают одну конкретную технологию. "
            "Такой рост нельзя выдавать за рост слабого сигнала."
        )
        priority = 3
    elif comparable and slope is not None and slope > 0:
        state = "growth_observed"
        label = "Есть признаки роста"
        explanation = (
            "Доля публикаций в последних сопоставимых периодах растёт; "
            "известные обязательные проверки не опровергли тему. "
            "Это ещё не экспертное подтверждение слабого сигнала."
        )
        priority = 0
    else:
        state = "insufficient_data"
        label = "Данных недостаточно"
        explanation = (
            "Публикации по теме найдены, но сопоставимого ряда пока "
            "недостаточно для вывода о росте."
        )
        priority = 1
    if scope_warning:
        explanation = f"{explanation} {scope_warning}"
    if context_warning:
        explanation = f"{explanation} {context_warning}"
    return priority, {
        "state": state,
        "label": label,
        "explanation": explanation,
        "failed_reasons": failed_names,
        "unknown_reasons": unknown_names,
        "publication_growth_comparable": comparable,
        "scope_warning": scope_warning,
        "context_warning": context_warning,
    }


def build_queue(cards_packet: dict, limit: int = 15) -> dict:
    """Rank automatic candidates without requiring expert validation."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Размер очереди должен быть целым числом от 1 до 100.")
    cards = cards_packet.get("cards")
    if not isinstance(cards, list):
        raise ValueError("Пакет карточек не содержит списка cards.")

    statuses = Counter()
    candidates = []
    seen = set()
    for original in cards:
        card = deepcopy(original)
        card.setdefault("expert_validation", {
            "status": "not_requested",
            "required_for_display": False,
        })
        candidate_id = card.get("candidate_id")
        if candidate_id is None or candidate_id in seen:
            raise ValueError("Идентификаторы карточек должны быть заполнены и уникальны.")
        seen.add(candidate_id)
        status = card.get("status")
        statuses[status] += 1
        if status in {"widespread", "mature"}:
            continue

        gates = card.get("gates") or []
        failed = sorted(
            g["gate"] for g in gates
            if g.get("severity") == "block" and g.get("passed") is False
        )
        unknown = sorted(
            g["gate"] for g in gates
            if g.get("severity") == "block" and g.get("passed") is None
        )
        coherence_calibrated = any(
            g.get("gate") == "G_coherence_calibration" and g.get("passed") is True
            for g in gates
        )
        core_passes = sum(
            g.get("gate") in CORE_SIGNAL_GATES and g.get("passed") is True
            and (g.get("gate") != "G_coherence" or coherence_calibrated)
            for g in gates
        )
        observed = ((card.get("metrics") or {}).get("observed") or {})
        novelty = _finite(observed.get("novelty_percentile"))
        momentum = _finite(observed.get("momentum_percentile"))
        joint = min(novelty, momentum) if novelty is not None and momentum is not None else None
        confidence = _finite(card.get("evidence_confidence")) or 0.0
        band, _ = _band(failed, unknown, core_passes)
        screening_priority, screening = _screening(card, failed, unknown)

        # Lexicographic, not weighted: observed growth first, then unknown
        # evidence, then failed checks. A known contradiction must not gain
        # priority merely because its failure is measured rather than unknown.
        sort_key = (
            screening_priority,
            len(failed),
            -core_passes,
            len(unknown),
            -(joint if joint is not None else -1.0),
            -confidence,
            str(card.get("label") or "").casefold(),
            str(card.get("composition_sha256") or ""),
            str(candidate_id),
        )
        candidates.append((sort_key, {
            "candidate_id": candidate_id,
            "topic_id": card.get("topic_id"),
            "composition_sha256": card.get("composition_sha256"),
            "review_band": band,
            "why_in_queue": screening["explanation"],
            "screening": screening,
            "failed_checks": failed,
            "unknown_checks": unknown,
            "passed_core_checks": core_passes,
            "joint_novelty_momentum_floor": joint,
            "card": card,
        }))

    candidates.sort(key=lambda item: item[0])
    queue = []
    for rank, (_, item) in enumerate(candidates[:limit], 1):
        item["rank"] = rank
        queue.append(item)

    return {
        "version": "automatic-candidate-ranking-0.4.53",
        "mission_id": cards_packet.get("mission_id"),
        "score_run_id": cards_packet.get("score_run_id"),
        "provenance": deepcopy(cards_packet.get("provenance") or []),
        "counts": {
            "all_cards": len(cards),
            "eligible_for_review": len(candidates),
            "automatic_candidates": len(candidates),
            "shown": len(queue),
            "excluded_widespread_or_mature": len(cards) - len(candidates),
            "statuses": dict(sorted(statuses.items(), key=lambda item: str(item[0]))),
        },
        "ranking_policy": {
            "kind": "lexicographic_automatic_candidate_order_not_prospectivity_score",
            "order": [
                "observed_growth_then_insufficient_data_then_mixed_evidence_then_decline",
                "fewer_failed_blocking_checks",
                "more_passed_core_scientific_checks",
                "fewer_unknown_blocking_checks",
                "higher_minimum_of_known_novelty_and_momentum_percentiles",
                "higher_evidence_confidence",
                "stable_label_composition_and_candidate_id_tiebreak",
            ],
            "core_scientific_checks": sorted(CORE_SIGNAL_GATES),
            "coherence_policy": "count_pass_only_when_model_threshold_is_calibrated",
        },
        "interpretation": (
            "Это ранжированный список автоматически выявленных кандидатов слабых "
            "сигналов. Экспертная валидация необязательна и показывается отдельно. "
            "Порядок не является вероятностью успеха, прогнозом рынка или рейтингом "
            "коммерческой перспективности."
        ),
        "queue": queue,
    }
