"""Фильтр релевантности и карантин подозрительной хронологии источника.

Модуль не исправляет сырьё и не переписывает даты. Он создаёт отдельное
решение с причинами, чтобы сомнительная запись не стала «ранним сигналом»
только из-за ошибки внешней базы.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from saia import db, runs, methodology
from saia.research_identity import author_identity_disagreement
from saia.source_identity import openalex_arxiv_identity
from saia.publication_status import status_evidence
from saia.compiled_phrase_matching import matches_spec
from saia.arxiv_metadata import LITERAL_MATCHING_VERSION, _matches_phrase

POLICY_VERSION = "publication-quality-v10-supporting-type-diagnostic"
WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
JOURNAL_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
NON_STANDALONE_OPENALEX_TYPES = frozenset({
    "peer-review", "erratum", "supplementary-materials",
})

# Это контроль качества исторических метаданных, а не признак emergence.
# Граница означает: запись с таким словосочетанием и более ранней заявленной
# датой требует первичного подтверждения. Она не удаляется и не получает
# «правильную» дату автоматически.
ANACHRONISM_REVIEW = {
    "large language model": 2018,
    "large language models": 2018,
    "transformers": 2017,
    "explainable ai": 2016,
}


@dataclass(frozen=True)
class QualityDecision:
    decision: str
    relevance_score: float
    source_quality: float
    matched_terms: tuple[str, ...]
    flags: dict
    reasons: tuple[str, ...]


def normalize_text(value: str) -> str:
    return " ".join(WORD.findall((value or "").casefold()))


def exact_matches(text: str, terms: list[str]) -> tuple[str, ...]:
    normalized = normalize_text(text)
    return tuple(term for term in terms if normalize_text(term) in normalized)


def decide(*, title: str, abstract: str | None, publication_year: int | None,
           date_is_imprecise: bool, terms: list[str], sources: set[str],
           openalex_payloads: list[dict], arxiv_payloads: list[dict] | None = None,
           duplicate_rank: int = 0, categories: list[str] | None = None,
           as_of_date: str | None = None, relevance_mode: str = 'publication',
           policy: dict | None = None, identity_conflicts: list[dict] | None = None,
           status_observed_at: str | None = None,
           effective_date: str | None = None,
           compiled_branch_specs: list[dict] | None = None) -> QualityDecision:
    if relevance_mode not in ('publication', 'strict_title'):
        raise ValueError('Неизвестный режим проверки релевантности.')
    policy = policy or methodology.load_default().raw['publication_quality']
    supporting_mode = policy.get("non_standalone_mode", "annotate")
    if supporting_mode not in ("annotate", "exclude"):
        raise ValueError("Unknown non_standalone_mode")
    matching_specs = []
    if compiled_branch_specs:
        record = {"title": title, "abstract": abstract or ""}
        matching_specs = [spec for spec in compiled_branch_specs
                          if matches_spec(record, spec)]
        phrase_versions = {
            phrase: spec.get("matching_version", LITERAL_MATCHING_VERSION)
            for spec in matching_specs
            for phrase in [*spec["included_phrases"],
                           *(term for group in spec.get("concept_groups") or [] for term in group)]
        }
        title_matched = tuple(phrase for phrase, version in phrase_versions.items()
                              if _matches_phrase(title, phrase, version))
        abstract_matched = tuple(phrase for phrase, version in phrase_versions.items()
                                 if _matches_phrase(abstract or "", phrase, version))
    else:
        title_matched = exact_matches(title, terms)
        abstract_matched = exact_matches(abstract or "", terms)
    matched = tuple(dict.fromkeys(title_matched + abstract_matched))
    flags: dict[str, object] = {}
    reasons: list[str] = []
    if as_of_date and effective_date and str(effective_date)[:10] >= as_of_date[:10]:
        return QualityDecision(
            "exclude", 0.0, 0.2, (),
            {"outside_analysis_cutoff": True,
             "effective_date": str(effective_date)[:10],
             "date_is_imprecise": bool(date_is_imprecise)},
            ("Публикация не доказана доступной до даты анализа. При известном "
             "только годе консервативная дата приходится на 31 декабря; "
             "запись сохранена, но не входит в расчёт текущего окна.",),
        )
    category_mode = bool(categories)
    arxiv_categories = {
        str(category)
        for payload in (arxiv_payloads or [])
        for category in (
            payload.get("categories")
            if isinstance(payload.get("categories"), list)
            else str(payload.get("categories") or "").split()
        )
    }
    matched_categories = sorted(set(categories or []) & arxiv_categories)

    if category_mode and not matched_categories:
        reasons.append("Запись не относится к категориям широкого корпуса.")
        return QualityDecision(
            "exclude", 0.0, 0.2, (), {"off_category": True}, tuple(reasons)
        )
    if not category_mode and (not matched or (compiled_branch_specs and not matching_specs)):
        reasons.append("Нет точного термина запроса в названии или аннотации.")
        return QualityDecision("exclude", 0.0, 0.2, (), {"off_query": True}, tuple(reasons))

    if matching_specs:
        flags["compound_or_literal_plan_match"] = True
        reasons.append("Совпадение с составным поисковым планом — проверка извлечения, не содержания исследования.")

    if not category_mode and not title_matched and relevance_mode == 'strict_title':
        reasons.append(
            "Термин найден только в аннотации при нерелевантном названии; "
            "для точного ретротеста запись исключена как риск склейки метаданных."
        )

        return QualityDecision(
            "exclude", 0.35, 0.25, matched,
            {"abstract_only_query_match": True}, tuple(reasons),
        )

    if not category_mode and not title_matched:
        flags['abstract_only_query_match'] = True
        reasons.append('Термин подтверждён в аннотации; это предварительная релевантность, не проверка научного результата.')

    relevance = (
        1.0 if category_mode else
        min(1.0, 0.55 + 0.15 * len(matched) + (0.15 if matched[0] in title.casefold() else 0))
    )
    if category_mode:
        flags["matched_categories"] = ", ".join(matched_categories)
        # Термины широкого пользовательского направления не являются
        # названием найденной темы. Иначе почти все карточки получают
        # бессодержательные ярлыки «Machine learning» или «Neural network».
        matched = ()

    # These are supporting objects, not standalone research publications.
    # A canonical work containing an article/preprint as well must survive:
    # only an all-supporting OpenAlex record set with no arXiv text is excluded.
    observed_types = [payload.get("type") for payload in openalex_payloads
                      if payload.get("type")]
    if (openalex_payloads and not arxiv_payloads and observed_types
            and len(observed_types) == len(openalex_payloads)
            and all(kind in NON_STANDALONE_OPENALEX_TYPES for kind in observed_types)):
        flags["non_standalone_publication_object"] = True
        flags["openalex_record_types"] = sorted(set(observed_types))
        reasons.append("OpenAlex описывает рецензию, исправление или дополнительные "
                       "материалы, а не самостоятельную исследовательскую публикацию.")
        if supporting_mode == "exclude":
            return QualityDecision(
                "exclude", round(relevance, 3), 0.2, matched, flags,
                tuple(reasons + ["Экспериментальный режим: запись сохранена в источнике, "
                                 "но не увеличивает научный счётчик."]),
            )

    has_arxiv = "arxiv" in sources
    if author_identity_disagreement(openalex_payloads):
        flags['author_identity_conflict'] = True
        reasons.append('У связанных OpenAlex записей различаются полные наборы авторских ID; '
                       'идентичность требует проверки, это не доказательство дополнительных команд.')
    if any(openalex_arxiv_identity(p)['conflicting_arxiv_links'] for p in openalex_payloads):
        flags['openalex_arxiv_link_ambiguity'] = True
        reasons.append('Ссылки OpenAlex ведут к другим arXiv ID; они не заменяют ID из DOI и не доказывают тождество публикаций. Нужна проверка связанных версий.')
    if has_arxiv and "openalex" in sources:
        source_quality = 0.95
    elif has_arxiv:
        source_quality = 0.85
    elif abstract:
        source_quality = 0.62
    else:
        source_quality = 0.38
    if date_is_imprecise:
        source_quality -= 0.12
        flags["imprecise_date"] = True

    mirror_payloads = [
        payload for payload in (arxiv_payloads or [])
        if payload.get("_source_format") == "hf-arxiv-parquet-snapshot"
    ]
    if mirror_payloads and "openalex" not in sources:
        source_quality = min(source_quality, 0.78)
    revised_after_cutoff = any(
        as_of_date and payload.get("updated") and (payload.get("created") or payload.get('published'))
        and str(payload["updated"])[:10] >= as_of_date[:10]
        and str(payload["updated"]) != str(payload.get("created") or payload.get('published'))
        for payload in (arxiv_payloads or [])
    )
    if revised_after_cutoff:
        flags["content_revision_after_cutoff"] = True
        reasons.append(
            "Метаданные отражают версию, обновлённую после даты среза; "
            "без проверки v1 текст нельзя использовать в ретротесте."
        )

    # arXiv ``created`` dates the repository deposit, not necessarily the
    # underlying research. A journal-ref is only a bibliographic assertion;
    # it is sufficient to withhold a *freshness* claim, not to invent an exact
    # earlier publication date. Keep the raw work and the assertion visible.
    late_deposits = []
    for payload in (arxiv_payloads or []):
        submitted = str(payload.get("created") or payload.get("published") or "")[:4]
        journal_ref = str(payload.get("arxiv_journal_ref") or "")
        if not submitted.isdigit() or not journal_ref:
            continue
        earlier_years = sorted({
            int(match.group()) for match in JOURNAL_YEAR.finditer(journal_ref)
            if int(match.group()) <= int(submitted) - 2
        })
        if earlier_years:
            late_deposits.append({
                "arxiv_id": payload.get("id"),
                "arxiv_submission_year": int(submitted),
                "journal_ref_earliest_year": earlier_years[0],
                "journal_ref": journal_ref,
                "date_status": "bibliographic_assertion_not_verified_publication_date",
            })
    if late_deposits:
        flags["late_arxiv_deposit_evidence"] = late_deposits
        flags["research_freshness_unverified"] = True
        reasons.append(
            "В arXiv запись подана не менее чем через два года после года, "
            "указанного в journal-ref. Дату подачи нельзя считать датой "
            "первого исследования; запись сохранена, но исключена из "
            "автоматического расчёта новизны и роста до проверки первоисточника."
        )

    repository_backdate = False
    for payload in openalex_payloads:
        location = payload.get("primary_location") or {}
        source = location.get("source") or {}
        source_name = (source.get("display_name") or "").casefold()
        created = str(payload.get("created_date") or "")[:4]
        cited_years = [
            int(row.get("year")) for row in (payload.get("counts_by_year") or [])
            if row.get("year")
        ]
        has_early_citation = bool(
            publication_year and any(year <= publication_year + int(policy['early_citation_years']) for year in cited_years)
        )
        created_year = int(created) if created.isdigit() else None
        if (
            source.get("type") == "repository"
            and "arxiv" not in source_name
            and created_year and publication_year
            and created_year - publication_year >= int(policy['repository_index_delay_years'])
            and not has_early_citation
            and not has_arxiv
        ):
            repository_backdate = True

    full_text = normalize_text(f"{title} {abstract or ''}")
    anachronisms = sorted(
        phrase for phrase, earliest in ANACHRONISM_REVIEW.items()
        if phrase in full_text and publication_year and publication_year < earliest
    )
    if anachronisms:
        flags["anachronism_review"] = ", ".join(anachronisms)
        reasons.append(
            "Заявленная дата противоречит хронологии терминов в названии; "
            "нужно подтверждение первоисточником."
        )
    if repository_backdate:
        flags["repository_backdate"] = True
        reasons.append(
            "Репозиторная запись создана значительно позже заявленной даты "
            "и не имеет раннего независимого подтверждения."
        )
    if duplicate_rank:
        flags["duplicate_title_rank"] = duplicate_rank
        reasons.append('Повтор названия требует проверки идентичности; сам по себе не означает дубликат.')

    arxiv_titles = [
        normalize_text(payload.get("title") or "") for payload in (arxiv_payloads or [])
        if payload.get("title")
    ]
    canonical_tokens = set(normalize_text(title).split())
    title_conflict = any(
        canonical_tokens and source_title and
        len(canonical_tokens & set(source_title.split())) /
        len(canonical_tokens | set(source_title.split())) < float(policy['title_conflict_jaccard_max'])
        for source_title in arxiv_titles
    )
    if title_conflict:
        flags["cross_source_title_conflict"] = True
        reasons.append(
            "OpenAlex и arXiv связывают один ID с существенно разными названиями; "
            "запись помещена в карантин."
        )

    statuses = status_evidence(list(arxiv_payloads or []), openalex_payloads)
    reported = [item for item in statuses if item['status'] != 'not_reported']
    unsupported_due_to_status = bool(statuses) and all(item['status'] in ('withdrawn', 'retracted') for item in statuses)
    if reported:
        for item in statuses:
            item['status_observed_at'] = status_observed_at
            item['observation_time_source'] = 'quality_generation.created_at' if status_observed_at else 'not_provided'
        flags['deposit_status_evidence'] = statuses
        flags['publication_status_requires_review'] = True
        reasons.append('Есть evidence отзыва/ретракции или запроса отзыва конкретной записи. '
                       'Это не автоматически статус всех связанных текстов; дата вступления статуса неизвестна.')
        if unsupported_due_to_status:
            flags['all_available_deposits_withdrawn_or_retracted'] = True
            reasons.append('Все доступные в этой работе записи имеют явный отзыв/ретракцию; '
                           'научное подтверждение помещено в карантин, наблюдение сохраняется.')
        else:
            flags['canonical_text_status_support_unknown'] = True
    if identity_conflicts:
        flags['canonical_publication_identity_unknown'] = True
        flags['withheld_doi_assertions'] = identity_conflicts
        reasons.append('Спорный DOI не использован для объединения. Разные source ID сохранены, '
                       'но число независимых исследований требует проверки; это не label шума.')

    if title_conflict or revised_after_cutoff or unsupported_due_to_status or late_deposits:
        decision = "quarantine"
        source_quality = min(source_quality, 0.25)
    else:
        decision = "include"
        if reported or identity_conflicts:
            reasons.append('Запись предварительно релевантна; identity/status ограничения отмечены отдельно, научная валидность не установлена.')
        else:
            reasons.append(
                "Категория подтверждена метаданными; критических флагов источника нет."
                if category_mode else
                "Точный термин подтверждён в тексте; критических флагов источника нет."
            )

    return QualityDecision(
        decision, round(relevance, 3), round(max(0.0, source_quality), 3),
        matched, flags, tuple(reasons),
    )


def evaluate_mission(mission_id: str, normalize_run_id: int | None = None,
                     config: methodology.Methodology | None = None) -> dict:
    config = config or methodology.load_default()
    with db.connect() as conn, conn.cursor() as cur:
        if normalize_run_id is None:
            normalize_run_id = runs.current_run_id(cur, mission_id, "normalize")
        elif (not isinstance(normalize_run_id, int) or isinstance(normalize_run_id, bool)
              or normalize_run_id < 1):
            raise ValueError("Номер normalize-прогона должен быть положительным целым")
        if normalize_run_id is None:
            raise ValueError("сначала требуется завершённый прогон нормализации")
        cur.execute(
            "SELECT 1 FROM analysis_run WHERE run_id=%s AND mission_id=%s "
            "AND kind='normalize' AND status='done'",
            (normalize_run_id, mission_id),
        )
        if not cur.fetchone():
            raise ValueError("Нужен точный завершённый normalize-прогон этой миссии")
        cur.execute(
            "SELECT payload->'query'->'terms', payload->'query'->'arxiv_categories', "
            "payload->>'as_of_date', "
            "payload->'controlled_search_plan'->'compiled_branch_specs' FROM query_version "
            "WHERE query_version_id = (SELECT query_version_id FROM analysis_run WHERE run_id = %s)",
            (normalize_run_id,),
        )
        query_row = cur.fetchone()
        terms = list(query_row[0] or [])
        categories = list(query_row[1] or [])
        as_of_date = query_row[2]
        compiled_branch_specs = list(query_row[3] or [])
        cur.execute(
            """
            WITH links AS (
                SELECT work_id, raw_record_id FROM work_version WHERE run_id = %s
                UNION
                SELECT (item->>'work_id')::bigint, (item->>'raw_record_id')::bigint
                FROM analysis_run r CROSS JOIN LATERAL jsonb_array_elements(
                    COALESCE(r.notes->'source_identity_decisions', '[]'::jsonb)
                    || COALESCE(r.notes->'identity_conflict_queue', '[]'::jsonb)) AS item
                WHERE r.run_id = %s
            )
            SELECT w.work_id, w.canonical_title, w.abstract, w.publication_year,
                   w.date_is_imprecise, w.effective_date,
                   array_agg(DISTINCT r.source),
                   jsonb_agg(r.payload || jsonb_build_object('_status_origin_raw_record_id', r.raw_record_id)) FILTER (WHERE r.source = 'openalex'),
                   jsonb_agg(r.payload || jsonb_build_object('_status_origin_raw_record_id', r.raw_record_id)) FILTER (WHERE r.source = 'arxiv')
            FROM work w
            JOIN links l ON l.work_id = w.work_id
            JOIN raw_record r ON r.raw_record_id = l.raw_record_id
            WHERE w.run_id = %s
            GROUP BY w.work_id
            ORDER BY w.effective_date, w.work_id
            """,
            (normalize_run_id, normalize_run_id, normalize_run_id),
        )
        rows = cur.fetchall()
        cur.execute('SELECT notes->%s FROM analysis_run WHERE run_id=%s',
                    ('identity_conflict_queue', normalize_run_id))
        conflicts_by_work = {}
        for item in cur.fetchone()[0] or []:
            conflicts_by_work.setdefault(item['work_id'], []).append(item)
        cur.execute('INSERT INTO quality_generation (normalize_run_id, policy_version, '
                    'methodology_hash, effective_config, code_version, runtime_provenance, status) '
                    "VALUES (%s, %s, %s, %s, %s, %s, 'created') RETURNING generation_id, created_at",
                    (normalize_run_id, POLICY_VERSION, config.config_hash, Jsonb(config.raw),
                     runs.code_version(), Jsonb(runs.runtime_snapshot())))
        generation_id, status_observed_at = cur.fetchone()
        title_counts = Counter(normalize_text(row[1]) for row in rows)
        title_seen: Counter[str] = Counter()
        summary = Counter()
        for work_id, title, abstract, year, imprecise, work_date, sources, payloads, arxiv_payloads in rows:
            title_key = normalize_text(title)
            duplicate_rank = title_seen[title_key] if title_counts[title_key] > 1 else 0
            title_seen[title_key] += 1
            result = decide(
                title=title, abstract=abstract, publication_year=year,
                date_is_imprecise=imprecise, terms=terms, sources=set(sources or []),
                openalex_payloads=list(payloads or []),
                arxiv_payloads=list(arxiv_payloads or []), duplicate_rank=duplicate_rank,
                categories=categories, as_of_date=as_of_date,
                relevance_mode=config.raw['publication_quality']['relevance_mode'],
                policy=config.raw['publication_quality'],
                identity_conflicts=conflicts_by_work.get(work_id, []),
                status_observed_at=status_observed_at.isoformat(),
                effective_date=work_date.isoformat() if work_date else None,
                compiled_branch_specs=compiled_branch_specs,
            )
            cur.execute(
                """
                INSERT INTO work_quality
                    (work_id, run_id, policy_version, decision, relevance_score,
                     source_quality, matched_terms, flags, reasons)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (work_id, policy_version) DO UPDATE SET
                    decision = EXCLUDED.decision,
                    relevance_score = EXCLUDED.relevance_score,
                    source_quality = EXCLUDED.source_quality,
                    matched_terms = EXCLUDED.matched_terms,
                    flags = EXCLUDED.flags,
                    reasons = EXCLUDED.reasons,
                    evaluated_at = now()
                """,
                (
                    work_id, normalize_run_id, POLICY_VERSION, result.decision,
                    result.relevance_score, result.source_quality,
                    list(result.matched_terms), Jsonb(result.flags), list(result.reasons),
                ),
            )
            summary[result.decision] += 1
            cur.execute('INSERT INTO quality_snapshot (generation_id, work_id, decision, '
                        'relevance_score, source_quality, matched_terms, flags, reasons) '
                        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                        (generation_id, work_id, result.decision, result.relevance_score,
                         result.source_quality, list(result.matched_terms), Jsonb(result.flags),
                         list(result.reasons)))
        cur.execute("UPDATE quality_generation SET status = 'done', finished_at = now() "
                    'WHERE generation_id = %s', (generation_id,))
        conn.commit()
    return {"run_id": normalize_run_id, 'quality_generation_id': generation_id,
            "policy_version": POLICY_VERSION, **dict(summary)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Релевантность и карантин записей Horizon")
    parser.add_argument("mission_id")
    args = parser.parse_args()
    result = evaluate_mission(args.mission_id)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
