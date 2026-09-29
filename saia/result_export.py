"""Portable result snapshot from server storage, not a browser memory cache."""
from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy

from saia import candidates, candidate_analysis, candidate_external_links, candidate_assessment, card_presentation, score_expert, source_context, triage
from saia.hybrid import digest
from saia import scout_public_signals, public_signal_reviews

VERSION = "saia-result-export-v3"
MAX_CANDIDATES = 100


def parse_ids(value: str | None) -> list[int] | None:
    if value is None:
        return None
    parts = value.split(",")
    if not 1 <= len(parts) <= MAX_CANDIDATES or any(not v.isascii() or not v.isdigit() or len(v) > 12 or int(v) <= 0 for v in parts):
        raise ValueError("Выберите от 1 до 100 карточек с корректными идентификаторами.")
    identifiers = [int(v) for v in parts]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Карточки в выгрузке не должны повторяться.")
    return identifiers


def build(mission: str, score: int, candidate_ids: list[int] | None = None, *, public_signal_ids: list[str] | None = None) -> dict:
    if not isinstance(score, int) or isinstance(score, bool) or score <= 0:
        raise ValueError("Нужен конкретный завершённый прогон для выгрузки.")
    if candidate_ids is not None:
        if not isinstance(candidate_ids, list) or any(not isinstance(v, int) or isinstance(v, bool) for v in candidate_ids):
            raise ValueError("Неверный список карточек выгрузки.")
        if candidate_ids or not public_signal_ids:
            candidate_ids = parse_ids(",".join(str(v) for v in candidate_ids))
    packet = candidates.export_cards(mission, score)
    if packet["score_run_id"] != score:
        raise ValueError("Выгрузка не совпадает с выбранным прогоном.")
    cards = packet["cards"]
    references, published = scout_public_signals.select(packet, public_signal_ids if public_signal_ids is not None else ([] if candidate_ids is not None else None))
    by_id = {card["candidate_id"]: card for card in cards}
    if candidate_ids is None:
        if len(cards) > MAX_CANDIDATES:
            raise ValueError("Уточните выбор карточек: весь прогон содержит более 100 карточек.")
        selected = cards
    else:
        if any(v not in by_id for v in candidate_ids):
            raise ValueError("Одна из карточек не относится к выбранному прогону.")
        selected = [by_id[v] for v in candidate_ids]
    # References merged into a selected scientific card stay in its export,
    # even when the user did not separately select a duplicate public row.
    keys = {scout_public_signals.title_key(card['label']) for card in selected}
    public_ids = {row['id'] for row in published}
    published += [row for row in references['records'] if row['id'] not in public_ids
                  and scout_public_signals.title_key(row['title']) in keys]
    if len(published) + len(selected) > MAX_CANDIDATES:
        raise ValueError("За одну выгрузку выберите не более 100 записей обоих типов.")
    published = [dict(row, expert_opinions=public_signal_reviews.history_for_reference(row["reference_content_sha256"])) for row in published]
    # Recompute only the deterministic display order, not research metrics.
    ranked = triage.build_queue(packet, MAX_CANDIDATES)
    scientific_ranked = deepcopy(ranked)
    export_cards = []
    assessment_by_id = {}
    for card in selected:
        identifier = card["candidate_id"]
        context = source_context.read_saved_all(mission, score, identifier, card)
        analyst_links = candidate_external_links.for_candidate(mission, score, identifier)
        assessment = candidate_assessment.calculate(card, context, analyst_links)
        assessment_by_id[identifier] = assessment
        export_cards.append({"candidate_id": identifier, "composition_sha256": card["composition_sha256"],
            "scientific_source_passports": [{"source": source.get("type"), "source_id": source.get("id"),
                "url": source.get("url"), "title_original": evidence.get("title"),
                "publication_date": evidence.get("published_at"), "language_original": source.get("language"),
                "record_type": "scientific_bibliographic_record", "primary_research_verified": False,
                "trust_comment": "Библиографическая запись. Первичность исследования и рецензирование отдельно не подтверждены."}
                for evidence in card.get("evidence") or [] for source in evidence.get("sources") or []],
            "source_context": context, "multisource_assessment": assessment,
            "presentation": card_presentation.read(mission, score, identifier)["presentation"],
            "analysis_note": candidate_analysis.read(mission, score, identifier)["note"],
            "expert_opinions": score_expert.history(mission, identifier, score, 100),
            "analyst_links": analyst_links})
    selected_set = {card["candidate_id"] for card in selected}
    # The exported default order must match the scout, including saved external
    # bonuses. Retain the original scientific ordering separately for audit.
    for item in ranked["queue"]:
        card = item["card"]
        identifier = card["candidate_id"]
        item["candidate_id"] = identifier
        item["assessment"] = assessment_by_id.get(identifier) or candidate_assessment.load(mission, score, card)
    from saia.scout_results import rank
    ranked = rank(ranked)
    displayed = scout_public_signals.join({"queue": [deepcopy(q) for q in ranked["queue"] if q["card"]["candidate_id"] in selected_set]},
                                         {**references, "records": published})
    report = {"version": VERSION, "application": "Horizon", "mission_id": mission, "score_run_id": score,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "scope": {"kind": "requested_candidates" if candidate_ids is not None else "complete_saved_score_run",
                  "exported_candidate_ids": [c["candidate_id"] for c in selected], "exported_count": len(selected),
                  "exported_public_signal_ids": [row["id"] for row in published], "exported_public_signal_count": len(published),
                  "total_exported_items": len(selected) + len(published),
                  "score_run_total_cards": len(cards), "complete_score_run": len(selected) == len(cards),
                  "contexts_complete_within_declared_scope": not any(c["source_context"]["contexts_truncated"] for c in export_cards),
                  "includes_all_observation_revisions": False, "expert_opinion_limit_per_card": 100},
        "scientific_results": {**packet, "cards": selected},
        "scientific_ranked_candidates": [q for q in scientific_ranked["queue"] if q["card"]["candidate_id"] in selected_set],
        "ranked_candidates": [q for q in ranked["queue"] if q["card"]["candidate_id"] in selected_set],
        "ranking_policy": ranked["ranking_policy"], "additional_card_data": export_cards,
        "public_signals": {**references, "records": published}, "ranked_result_items": displayed["result_items"],
        "public_signal_policy": displayed["public_signal_policy"],
        "multisource_assessment_version": candidate_assessment.VERSION,
        "network_requested": False, "model_generation_requested": False, "scientific_results_modified": False,
        "expert_validation_required": False,
        "notes": ["Автоматические кандидаты доступны без экспертной валидации.",
                  "Оценка полноты оснований не является откалиброванной вероятностью успеха.",
                  "Внешние материалы — поисковые совпадения. Отдельная рабочая оценка учитывает только материалы, прошедшие описанные правила связи, дат и повторов; это не проверка истинности.",
                  "Базовый научный рейтинг сохранён отдельно. Общий многоканальный балл и диаграммы находятся в additional_card_data.multisource_assessment.",
                  "ranked_candidates повторяет текущий порядок скаута; scientific_ranked_candidates сохраняет прежний научный порядок.",
                  "ranked_result_items включает научные кандидаты и помеченные внешние подборки; public_signals не имеют научного балла и не являются разметкой истинности.",
                  "Неизвестные значения сохранены как null, а не заменены нулями.",
                  "Исходные заголовки и языки сохраняются; русский текст модели отдельно помечен как машинный.",
                  "Файл для личного ревью. Права на публичную перепубликацию всех источников не установлены."]}
    report["report_payload_sha256"] = digest(report)
    return report
