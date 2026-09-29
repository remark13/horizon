"""Explainable, provisional multi-source priority; frozen scientific results stay intact.

Metadata rules are deliberately not a classifier of truth, adoption or market
size. Every bonus retains its records and matching rule. No model is called.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import math
import re
from urllib.parse import urlsplit

from saia.hybrid import digest
from saia.scientific_aliases import regular_plural_key
from saia.triage import CHECK_LABELS

VERSION = "candidate-multisource-assessment-0.4.56"
# Keep the stored API key "market" for compatibility; this channel contains
# news publications, never market-size, revenue or sales estimates.
AXES = {"science": "Наука", "market": "Новости", "patents": "Патенты", "investment": "Инвестиции"}
BONUS_CAPS = {"market": 10.0, "patents": 10.0, "investment": 5.0}
SCIENCE_WEIGHTS = {"momentum": .40, "novelty": .25, "persistence": .15, "independent_diffusion": .10, "publication_volume": .10}
SCIENCE_LABELS = {"momentum": "Рост относительно других тем", "novelty": "Новизна", "persistence": "Устойчивость по периодам", "independent_diffusion": "Независимые исследовательские группы", "publication_volume": "Объём научных оснований (до 20 работ)"}
STOP = frozenset("and or the for of in on with to a an new research study studies technology technologies method methods approach approaches system systems topic theme тема по запросу новые технологии исследование исследований метод методы система системы".split())
NON_MARKET_SOURCES = frozenset({"dealroom_marketmaps", "aist_press_rss", "osti_gov"})
READY = frozenset({"complete", "empty_observed_response"})


def number(value) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return float(value)


def tokens(value: str) -> set[str]:
    words = re.findall(r"[a-zа-яё][a-zа-яё0-9]*", str(value), re.IGNORECASE)
    words = [word[:-1] if word.endswith("s") and 3 <= len(word[:-1]) <= 8 and word[:-1].isupper() else word for word in words]
    return {regular_plural_key(word.casefold()) for word in words if len(word) >= 3 and word.casefold() not in STOP}


def _month(value) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", value[:10]):
        return None
    try:
        date.fromisoformat(value[:10] if len(value) >= 10 else value + "-01")
    except ValueError:
        return None
    return value[:7]


def _publisher(material: dict) -> str | None:
    value = material.get("publisher_organisation") or material.get("publisher")
    if isinstance(value, str) and value.strip():
        return value.strip().casefold()
    host = urlsplit(str(material.get("url") or "")).hostname
    # An aggregator is not an independent publisher.
    if host and host not in {"news.google.com", "www.lens.org", "lens.org", "dealroom.co"}:
        return host.removeprefix("www.")
    return None


def _family(material: dict) -> str | None:
    value = material.get("family_id")
    if isinstance(value, (str, int)) and str(value).strip():
        return str(material.get("source") or "unknown") + ":family:" + str(value)
    families = material.get("families")
    if isinstance(families, dict):
        simple = families.get("simple_family")
        if isinstance(simple, dict) and isinstance(simple.get("family_id"), (str, int)):
            return str(material.get("source") or "unknown") + ":family:" + str(simple["family_id"])
    return None


def _science(card: dict) -> dict:
    metrics = card.get("metrics") or {}
    normalized = metrics.get("normalized") or {}
    observed = metrics.get("observed") or {}
    series = metrics.get("publication_series") or {}
    values = {key: number(normalized.get(key)) for key in SCIENCE_WEIGHTS}
    document_count = number(observed.get("doc_count"))
    values["publication_volume"] = min(1.0, max(0.0, document_count) / 20) if document_count is not None else None
    # High momentum in an incomparable retrieval sample is not scientific growth.
    if series.get("coverage_comparable") is not True:
        values["momentum"] = None
    for key in ("momentum", "novelty"):
        if values[key] is None and (key != "momentum" or series.get("coverage_comparable") is True):
            percentile = number(observed.get(key + "_percentile"))
            values[key] = percentile / 100 if percentile is not None else None
    values = {k: min(1.0, max(0.0, v)) if v is not None else None for k, v in values.items()}
    available_weight = sum(SCIENCE_WEIGHTS[k] for k, v in values.items() if v is not None)
    # Sum observed contributions, do not extrapolate a volume-only sample to
    # a high emergence score. Unknown components stay null, with an explicit
    # unmeasured range, rather than becoming negative scientific findings.
    score = round(100 * sum(SCIENCE_WEIGHTS[k] * v for k, v in values.items() if v is not None), 2) if available_weight else None
    components = [{"metric": k, "label": SCIENCE_LABELS[k], "value": round(v * 100, 2) if v is not None else None,
                   "weight": SCIENCE_WEIGHTS[k], "effective_weight": SCIENCE_WEIGHTS[k] if v is not None else None,
                   "weighted_points": round(100 * SCIENCE_WEIGHTS[k] * v, 2) if v is not None else None}
                  for k, v in values.items()]
    return {"score": score, "components": components, "known_weight_percent": round(100 * available_weight, 2),
            "unmeasured_weight_percent": round(100 * (1-available_weight), 2),
            "possible_upper_score_if_all_unknown_maximal": round(min(100, score + 100 * (1-available_weight)), 2) if score is not None else None,
            "publication_count": observed.get("doc_count"), "coverage_comparable": series.get("coverage_comparable"),
            "share_change_percentage_points": round(series["share_change"] * 100, 4) if number(series.get("share_change")) is not None and series.get("coverage_comparable") is True else None,
            "share_slope_percentage_points": round(series["share_slope_per_window"] * 100, 4) if number(series.get("share_slope_per_window")) is not None and series.get("coverage_comparable") is True else None,
            "score_semantics": "Сумма измеренных взвешенных научных вкладов, не вероятность. Неизвестные признаки не отрицательные выводы; их неизмеренный вес показан отдельно. Объём публикаций без признаков роста может дать не более 10 баллов."}


def calculate(card: dict, context: dict | None = None, links: dict | None = None, *, today: date | None = None) -> dict:
    """Pure deterministic calculation with record-level audit and explicit nulls."""
    today = today or datetime.now(timezone.utc).date()
    context, links = context or {}, links or {}
    composition = card.get("composition_sha256")
    for packet in (context.get("binding") or {}, links):
        if packet.get("composition_sha256") not in {None, composition}:
            raise ValueError("Материалы не соответствуют составу карточки.")
    anchors = tokens(card.get("label") or "")
    # Require a compound topic; a lone generic token must not promote news noise.
    required = len(anchors) if 2 <= len(anchors) <= 3 else max(2, math.ceil(len(anchors) * 2 / 3))
    linked = {(str(row.get("observation_id")), row.get("record_url")): row.get("assessment") for row in links.get("links") or []}
    rows, seen = [], set()
    for report in context.get("reports") or []:
        for material in report.get("materials") or []:
            group = material.get("group") or report.get("group")
            axis = {"commercial": "market", "patents": "patents", "investment": "investment"}.get(group)
            if axis is None:
                continue
            source = material.get("source") or report.get("source")
            if source in NON_MARKET_SOURCES:
                continue
            title = str(material.get("title_original") or "")
            matched = sorted(anchors & tokens(title))
            selection = linked.get((str(material.get("observation_id") or report.get("observation_id")), material.get("url")))
            month = _month(material.get("record_date"))
            reason = None
            target = urlsplit(str(material.get("url") or ""))
            if report.get("status") not in READY or material.get("provider_cache_status") == "stale" or report.get("provider_cache_status") == "stale":
                reason = "Источник не предоставил свежий успешный ответ"
            elif target.scheme not in {"http", "https"} or not target.hostname or target.username is not None or target.password is not None:
                reason = "Нет безопасной ссылки на материал"
            elif material.get("original_record_url_available") is False:
                reason = "Нет отдельной первичной ссылки на запись"
            elif material.get("provider_duplicate_flag") is True:
                reason = "Источник отметил повторную публикацию"
            elif selection == "background_only":
                reason = "Скаут отметил материал как общий фон"
            elif selection != "relevant_to_topic" and (len(anchors) < 2 or len(matched) < required):
                reason = "Недостаточно совпадений с конкретной темой в заголовке"
            elif month is None:
                reason = "Дата материала недостаточна для проверки актуальности"
            elif month > today.isoformat()[:7] or (len(str(material.get("record_date"))) >= 10 and str(material["record_date"])[:10] > today.isoformat()):
                reason = "Дата материала находится в будущем"
            elif material.get("cache_fresh") is False or report.get("cache_fresh") is False:
                reason = "Сохранённый ответ устарел; нужно обновить материалы"
            family = _family(material) if axis == "patents" else None
            # Titles repeated by news aggregators are one observation, not independent support.
            title_key = " ".join(sorted(tokens(title)))
            identity = (axis, family or material.get("event_uri") or (title_key if axis == "market" else material.get("url") or material.get("record_id")))
            if not reason and identity in seen:
                reason = "Повторная запись или то же патентное семейство уже учтены"
            if not reason:
                seen.add(identity)
            quality = .65 if selection == "relevant_to_topic" else .35
            if axis == "patents" and family is None:
                quality *= .5
            rows.append({"axis": axis, "source": source, "title": title, "url": material.get("url"),
                         "observation_id": material.get("observation_id") or report.get("observation_id"),
                         "record_id": material.get("record_id"), "record_date": material.get("record_date"), "month": month,
                         "date_kind": material.get("date_kind"), "publisher": _publisher(material), "family_id": family,
                         "applicants": material.get("applicants") or [], "matched_terms": matched, "required_term_count": required,
                         "relevance_method": "analyst_selection" if selection == "relevant_to_topic" else "conservative_title_token_overlap",
                         "included": reason is None, "exclusion_reason": reason, "quality_weight": quality if reason is None else None,
                         "independent_primary_confirmation": False, "expert_validated": False})
    science = _science(card)
    axes = {"science": {"label": AXES["science"], **science, "metrics": {"Найдено публикаций": science["publication_count"], "Полнота расчёта, %": science["known_weight_percent"]},
                        "meaning": "Научные признаки возникновения и роста темы", "status": "provisional" if science["score"] is not None else "insufficient_data"}}
    histories = {}
    for axis in ("market", "patents", "investment"):
        selected = [r for r in rows if r["axis"] == axis and r["included"]]
        observed_count = len([r for r in rows if r["axis"] == axis])
        effective = sum(r["quality_weight"] for r in selected)
        publishers = len({r["publisher"] for r in selected if r["publisher"]})
        recent = sum(r["quality_weight"] for r in selected if r["month"] >= (today.replace(day=1).isoformat()[:4] + "-01"))
        count_label = "Количество новостных публикаций с поправкой на связь с темой" if axis == "market" else "Объём с поправкой на связь с темой"
        components = {count_label: round(60 * min(1.0, effective / 4), 2),
                      "Разнообразие издателей": round(20 * min(1.0, publishers / 3), 2),
                      "Материалы текущего года": round(20 * min(1.0, recent / 4), 2)}
        if axis == "patents":
            applicants = {str(name).strip().casefold() for r in selected for name in r["applicants"] if isinstance(name, str) and name.strip()}
            components = {"Объём с поправкой на семейства и связь": round(70 * min(1.0, effective / 4), 2),
                          "Разные заявители": round(30 * min(1.0, len(applicants) / 3), 2)}
        score = round(sum(components.values()), 2) if selected else None
        counts = Counter(r["month"] for r in selected)
        histories[axis] = {"points": [{"period": month, "count": counts[month]} for month in sorted(counts)],
                           "complete_history": False, "growth_rate": None,
                           "note": "Даты учтённых материалов ограниченной выборки. Пропуски — неизвестность, не нули. Темп роста не вычисляется без сопоставимого полного ряда."}
        axes[axis] = {"label": AXES[axis], "score": score, "status": "provisional" if score is not None else "insufficient_data",
                      "observed_records": observed_count, "included_records": len(selected), "effective_records": round(effective, 3),
                      "components": components if selected else {}, "metrics": {"Найдено записей": observed_count, "Учтено после проверки": len(selected), "Разные издатели": publishers if selected else None},
                      "meaning": {"market": "Новостные публикации о теме: их количество, разные издатели и актуальность. Не спрос, размер рынка, продажи или выручка", "patents": "Связанная патентная активность, не внедрение или юридическая сила", "investment": "Связанные сообщения с отдельной ссылкой, не сумма инвестиций"}[axis],
                      "reason": None if selected else "Недостаточно подходящих проверяемых материалов. Отсутствие данных не уменьшает базовый научный балл."}
        if axis == "patents":
            axes[axis]["metrics"].update({"Известные семейства": len({r["family_id"] for r in selected if r["family_id"]}),
                                          "Записи без семейства": sum(r["family_id"] is None for r in selected), "Разные заявители": len(applicants) if selected else None})
    base = science["score"]
    raw_bonuses = {axis: round(axes[axis]["score"] * cap / 100, 2) if axes[axis]["score"] is not None else None for axis, cap in BONUS_CAPS.items()}
    available_bonus = sum(value for value in raw_bonuses.values() if value is not None)
    factor = min(1.0, max(0.0, 100 - base) / available_bonus) if base is not None and available_bonus else 1.0
    bonuses = {k: round(v * factor, 2) if v is not None and base is not None else None for k, v in raw_bonuses.items()}
    total = round(min(100, base + sum(v for v in bonuses.values() if v is not None)), 2) if base is not None else None
    checks = [{"label": CHECK_LABELS.get(g.get("gate"), g.get("gate")), "gate": g.get("gate"), "passed": g.get("passed"),
               "observed": g.get("observed"), "threshold": g.get("threshold"), "severity": g.get("severity")} for g in card.get("gates") or []]
    failed = [c for c in checks if c["severity"] == "block" and c["passed"] is False]
    external_support = any(axes[k]["score"] is not None for k in BONUS_CAPS)
    validation_label = "Дополнительные основания найдены" if external_support else "Есть только научные основания"
    if failed:
        validation_label = "Есть противоречия; внешние материалы их не снимают"
    elif science["coverage_comparable"] is not True:
        validation_label = "Рост ещё нельзя проверить по сопоставимым периодам"
    elif axes["market"]["score"] is not None and axes["patents"]["score"] is not None:
        validation_label = "Тема прослеживается в науке, СМИ и патентах"
    result = {"version": VERSION, "candidate_id": card.get("candidate_id"), "composition_sha256": composition, "calculated_for_date": today.isoformat(),
              "overall_score": total, "score_unit": "points_not_probability", "scientific_baseline": base, "scientific_score_modified": False,
              "axes": axes, "contributions": {"science": base, **bonuses}, "uncapped_bonuses": raw_bonuses,
              "external_contribution": round(total - base, 2) if total is not None else None,
              "formula": "Научный балл + до 10 за релевантные новости + до 10 за патенты + до 5 за инвестиционные сообщения; итог не выше 100.",
              "policy": {"science_weights": SCIENCE_WEIGHTS, "external_bonus_caps": BONUS_CAPS, "missing_is_zero": False,
                         "publication_volume_saturation": 20, "publication_volume_is_not_growth": True,
                         "unknown_science_metrics": "null_not_negative_sum_observed_contributions_no_extrapolation", "calibrated_on_labeled_data": False,
                         "relevance_rule": "All distinctive topic tokens for two- or three-token topics; otherwise 2/3, at least two; or explicit analyst relevance link",
                         "automatic_relevance_weight": .35, "analyst_relevance_weight": .65, "unknown_patent_family_weight_multiplier": .5,
                         "news_growth_used_for_score": False, "unavailable_sources_lower_score": False, "market_is_media_attention_not_adoption": True},
              "records": rows, "scientific_checks": checks,
              "validation": {"label": validation_label, "expert_required": False, "expert_validated": False, "automatic_checks_only": True,
                             "independent_primary_confirmation": False, "failed_scientific_checks": len(failed),
                             "excluded_external_records": sum(not r["included"] for r in rows),
                             "note": "Автоматическая проверка дат, повторов и лексической связи. Не экспертное подтверждение, не проверка истинности новостей или спроса."},
              "histories": {"science": {"points": [{"period": p.get("start"), "count": p.get("topic_works"), "share": p.get("share"), "complete": p.get("complete")}
                                                     for p in (card.get("metrics") or {}).get("publication_series", {}).get("points") or []],
                                        "coverage_comparable": science["coverage_comparable"], "note": "Научный ряд внутри собранного корпуса. Неполный текущий период исключён из расчёта роста."}, **histories},
              "limitations": ["Экспериментальная шкала приоритета; не откалиброванная вероятность слабого сигнала.",
                              "Совпадение терминов не гарантирует смысловую связь. Новости могут описывать один пресс-релиз.",
                              "Полные тексты новостей, юридическое состояние патентов и независимость событий автоматически не проверены.",
                              "Гранты и госконтракты не считаются инвестиционными сделками. Каталог компаний не считается спросом.",
                              "Внешние данные описывают текущий контекст, не реконструкцию их доступности в прошлом."],
              "network_requested": False, "model_called": False, "contexts_truncated": context.get("contexts_truncated", False)}
    from saia import publication_profile
    result["publication_profile"] = publication_profile.build(card, today=today)
    from saia.assessment_visuals import render
    result["visualization_html"] = render(result)
    result["assessment_payload_sha256"] = digest(result)
    return result


def load(mission: str, score: int, card: dict) -> dict:
    from saia import candidate_external_links, source_context
    return calculate(card, source_context.read_saved_all(mission, score, card["candidate_id"], card),
                     candidate_external_links.for_candidate(mission, score, card["candidate_id"], card=card))


def for_candidate(mission: str, score: int, candidate: int) -> dict:
    from saia.candidate_external_links import _candidate
    return load(mission, score, _candidate(mission, score, candidate))
