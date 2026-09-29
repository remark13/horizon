"""Scout-facing results and bounded enrichment; scientific storage is immutable."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os

from saia import candidate_assessment, candidates, source_context, triage, scout_public_signals

PRIORITIES = {"growth_observed": 0, "insufficient_data": 1, "mixed_evidence": 2, "growth_not_confirmed": 3}


def rank(result: dict) -> dict:
    """No external bonus can move contradicted science ahead of supported growth."""
    items = result.get("queue") or []
    for item in items:
        item.setdefault("scientific_rank", item.get("rank"))
    items.sort(key=lambda q: (PRIORITIES.get(q.get("screening", {}).get("state"), 1),
                             len(q.get("failed_checks") or []),
                             -(q.get("assessment", {}).get("overall_score") if q.get("assessment", {}).get("overall_score") is not None else -1),
                             q.get("scientific_rank") or 0, str(q.get("candidate_id"))))
    for index, item in enumerate(items, 1):
        item["rank"] = index
    result["ranking_policy"] = {**result.get("ranking_policy", {}),
        "multisource_version": candidate_assessment.VERSION, "kind": "scientific_screening_then_explainable_multisource_priority",
        "external_bonus_caps": candidate_assessment.BONUS_CAPS, "not_probability": True,
        "failed_scientific_checks_not_rescued_by_news": True, "expert_required": False,
        "order": ["scientific_screening_group", "fewer_failed_scientific_checks", "higher_multisource_score", "stable_scientific_rank"]}
    return result


def build(mission: str, score: int | None = None, limit: int = 100, *, packet: dict | None = None, include_public_signals: bool = True) -> dict:
    packet = packet or candidates.export_cards(mission, score)
    # Rank the full eligible set before applying the requested result limit.
    result = triage.build_queue(packet, 100)
    errors = []
    for item in result["queue"]:
        try:
            item["assessment"] = candidate_assessment.load(mission, result["score_run_id"], item["card"])
        except (ValueError, OSError) as error:
            # A broken external context must not erase scientific candidates.
            item["assessment"] = candidate_assessment.calculate(item["card"])
            item["assessment_status"] = "saved_context_unavailable"
            errors.append({"candidate_id": item["candidate_id"], "status": "saved_context_unavailable", "error_type": type(error).__name__})
    result = rank(result)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Размер выдачи — от 1 до 100.")
    result["queue"] = result["queue"][:limit]
    result["counts"]["shown"] = len(result["queue"])
    result["assessment_errors"] = errors
    result["scientific_results_modified"] = False
    result["expert_validation_required"] = False
    result["version"] = "scout-multisource-results-0.4.58"
    if include_public_signals:
        try:
            published = scout_public_signals.for_packet(packet)
        except (ValueError, OSError) as error:
            published = {"records": [], "total": 0, "status": "public_catalog_unavailable", "error_type": type(error).__name__}
        scout_public_signals.join(result, published)
    return result


def default_sources() -> list[str]:
    patent = "lens_patents" if os.environ.get("LENS_API_TOKEN") and not os.environ.get("EPO_OPS_CONSUMER_KEY") else "epo_ops"
    return ["google_news_rss", patent, "dealroom_public_rounds"]


def enrich(mission: str, score: int, identifiers: list[int], sources: list[str] | None = None) -> dict:
    if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 15 or len(set(identifiers)) != len(identifiers) or any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in identifiers):
        raise ValueError("За один шаг дополняются от 1 до 15 разных карточек.")
    packet = candidates.export_cards(mission, score)
    cards = {c["candidate_id"]: c for c in packet["cards"]}
    if any(identifier not in cards for identifier in identifiers):
        raise ValueError("Карточка не относится к выбранному результату.")
    selected = source_context.validate_sources(sources if sources is not None else default_sources())
    if any(source_context.SOURCE_INFO[s][1] not in {"commercial", "patents", "investment"} for s in selected):
        raise ValueError("Автоматическое дополнение оценки использует новости, патенты и инвестиционные сообщения.")
    def one(identifier):
        try:
            value = source_context.collect(mission, score, identifier, source_context.default_query(cards[identifier]), selected)
            return {"candidate_id": identifier, "status": "collected", "fetched_sources": value.get("fetched_sources"),
                    "source_statuses": [{"source": r["source"], "status": r["status"], "count": r["observed_count"]} for r in value["reports"]]}
        except (ValueError, OSError) as error:
            return {"candidate_id": identifier, "status": "not_completed", "error_type": type(error).__name__}
    # Reuse the global two-card collection gate. Never multiply source requests
    # by spawning a task for every candidate at once.
    with ThreadPoolExecutor(max_workers=2) as executor:
        collected = list(executor.map(one, identifiers))
    result = build(mission, score, 100, packet=packet)
    result["enrichment"] = {"cards": collected, "requested_sources": selected, "max_parallel_cards": 2,
                            "bounded_sample": True, "no_expert_gate": True}
    return result
