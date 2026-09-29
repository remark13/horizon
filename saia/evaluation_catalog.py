"""Каталог проверки и post-hoc оценка замороженных результатов.

Никакой вызов детектора, настройки генератора или экспорт меток в признаки.
Резерв не является независимо ослеплённым gold; его оценка здесь закрыта.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

import yaml

from saia import db, hybrid, runs

CATALOG_PATH = Path(__file__).resolve().parents[1] / 'evaluation' / 'cases.v0.4.1.yaml'
KINDS = {'research_line_positive_proposal', 'ambiguous_line', 'empirical_observation_negative'}


def load(path: Path = CATALOG_PATH) -> dict:
    catalog = yaml.safe_load(path.read_text())
    validate(catalog)
    return catalog


def validate(catalog: dict) -> None:
    if catalog.get('content_policy') != 'identifiers_links_and_original_annotations_only_no_fulltext':
        raise ValueError('Каталог не разрешает включать полные тексты без проверки прав.')
    if any(catalog.get(k) is not None for k in ('weak_signal_label', 'future_growth_label', 'market_success_label')):
        raise ValueError('Предварительный каталог нельзя объявить истинной разметкой сигнала или будущего.')
    seen, family_split, reference_split = set(), {}, {}
    for c in catalog['cases']:
        if c['case_id'] in seen:
            raise ValueError('Повторён case_id.')
        seen.add(c['case_id'])
        if c['split'] not in ('development', 'reserved') or c['kind'] not in KINDS:
            raise ValueError('Неизвестный split или вид кейса.')
        date.fromisoformat(c['as_of_date'])
        if any(c.get(k) is not None for k in ('weak_signal_label', 'future_growth_label', 'market_success_label')):
            raise ValueError('Предварительный кейс не имеет подтверждённой целевой метки.')
        if not c['arxiv_ids'] or len(c['arxiv_ids']) != len(set(c['arxiv_ids'])):
            raise ValueError('Нужны неповторённые источники кейса.')
        for ref in c['arxiv_ids']:
            if not re.fullmatch(r'\d{4}\.\d{4,5}', ref):
                raise ValueError('Нужен точный arXiv ID без поздней версии или поискового выражения.')
            previous = reference_split.setdefault(ref, c['split'])
            if previous != c['split']:
                raise ValueError('Связанная работа попала в разные split.')
        previous = family_split.setdefault(c['family_id'], c['split'])
        if previous != c['split']:
            raise ValueError('Семейство попало в разные split.')
        if c['kind'] == 'empirical_observation_negative' and c.get('negative_rule') not in (
                'review_only_not_new_primary_development', 'same_work_not_independent_replication'):
            raise ValueError('Отрицательный кейс должен иметь наблюдаемое правило, не выдуманную судьбу технологии.')
        if c.get('negative_rule') == 'same_work_not_independent_replication':
            expected = {'10.48550/arxiv.' + ref for ref in c['arxiv_ids']}
            if set(c.get('alternate_identifiers', [])) != expected:
                raise ValueError('DOI не соответствует той же arXiv-работе.')
    if 'reference_titles' in catalog:
        titles = catalog['reference_titles']
        if set(titles) != set(reference_split) or any(not isinstance(t, str) or not t.strip() for t in titles.values()):
            raise ValueError('Expected reference titles must exactly cover catalog IDs')


def summary(catalog: dict) -> dict:
    cases = catalog['cases']
    return {'catalog_version': catalog['version'], 'catalog_hash': hybrid.digest(catalog),
            'cases': len(cases), 'families': len({c['family_id'] for c in cases}),
            'splits': dict(Counter(c['split'] for c in cases)),
            'kinds': dict(Counter(c['kind'] for c in cases)),
            'unique_arxiv_references': len({i for c in cases for i in c['arxiv_ids']}),
            'weak_signal_gold_labels': 0, 'future_growth_gold_labels': 0,
            'annotation_status': catalog['annotation_status'], 'split_status': catalog['split_status'],
            'training_readiness': 'not_ready',
            'limitations': ['Восемь положительных предложений относятся к исследовательским линиям, не к истинности слабого сигнала.',
                            'Отрицательные наблюдения — обзоры/дубли; нет эмпирически размеченных затухших траекторий.',
                            'Резерв подготовлен разработчиком, не является независимо ослеплённой проверкой.',
                            'Одностатейные seed-кейсы требуют расширения состава и экспертной проверки.',
                            'Не вычислять Precision@15 по этому каталогу.']}


def selected_cases(catalog: dict, split: str) -> list[dict]:
    if split != 'development':
        raise ValueError('Резерв этого предварительного релиза не открыт для настройки или оценки; нужен отдельный замороженный протокол.')
    return [c for c in catalog['cases'] if c['split'] == split]


def evaluate(snapshot: dict, catalog: dict, split: str = 'development', reference_report: dict | None = None) -> dict:
    validate(catalog)
    cases = selected_cases(catalog, split)
    if 'reference_titles' in catalog:
        from saia.reference_audit import validate_report
        validate_report(reference_report or {}, catalog)
    provenance = snapshot['provenance']
    all_refs = sorted({r for c in cases for r in c['arxiv_ids']})
    all_dois = sorted({r for c in cases for r in c.get('alternate_identifiers', [])})
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT i.kind, lower(i.value), i.work_id, w.effective_date, q.decision, q.flags, q.reasons "
                    'FROM identifier i JOIN work w USING (work_id) LEFT JOIN quality_snapshot q '
                    'ON q.work_id = w.work_id AND q.generation_id = %s '
                    'WHERE w.run_id = %s AND '
                    "((i.kind = 'arxiv' AND i.value = ANY(%s)) OR (i.kind = 'doi' AND lower(i.value) = ANY(%s)))",
                    (provenance['quality_generation_id'], provenance['normalize_run_id'], all_refs, all_dois))
        mapping = {}
        for kind, ref, work, day, decision, flags, reasons in cur.fetchall():
            mapping.setdefault((kind, ref), []).append({'work_id': work, 'date': day.isoformat(),
                                                       'quality': decision, 'quality_flags': flags,
                                                       'quality_reasons': reasons})
    eligible = set(snapshot['eligible_work_ids'])
    views = {name: [c for c in snapshot['candidates'] if name in c['channels']]
             for name in ('lexical', 'semantic')}
    views['hybrid'] = snapshot['candidates']
    rows = []
    for case in cases:
        row = {'case_id': case['case_id'], 'title': case['title'], 'kind': case['kind'],
               'family_id': case['family_id'], 'as_of_date': case['as_of_date'],
               'sources': ['https://arxiv.org/abs/' + r for r in case['arxiv_ids']],
               'weak_signal_label': None, 'weak_signal_detection': 'not_evaluated'}
        if case['as_of_date'] != snapshot['as_of_date']:
            row.update(status='different_historical_cutoff', recovery=None)
            rows.append(row)
            continue
        observations = {r: mapping.get(('arxiv', r), []) for r in case['arxiv_ids']}
        target_ids = {v['work_id'] for values in observations.values() for v in values if v['work_id'] in eligible}
        row.update(observations=observations, eligible_target_work_ids=sorted(target_ids),
                   missing_reference_ids=[r for r, values in observations.items() if not values],
                   mapped_but_ineligible_reference_ids=[r for r, values in observations.items()
                       if values and not any(v['work_id'] in eligible for v in values)])
        if case['kind'] != 'empirical_observation_negative':
            row['status'] = 'recovery_measured' if len(target_ids) >= 2 else 'insufficient_eligible_targets'
            row['recovery'] = ({name: hybrid.recovery(cands, target_ids) for name, cands in views.items()}
                               if len(target_ids) >= 2 else None)
        elif case['negative_rule'] == 'same_work_not_independent_replication':
            primary = {v['work_id'] for values in observations.values() for v in values}
            secondary = {v['work_id'] for r in case['alternate_identifiers'] for v in mapping.get(('doi', r), [])}
            row['status'] = 'canonicalization_observed' if primary and secondary else 'missing_alternate_identifier'
            row['same_work_observed'] = primary == secondary if primary and secondary else None
            row['recovery'] = None
            row['interpretation'] = 'Канонизация современных идентификаторов, не доказательство доступности DOI до среза и не проверка отказа от ложного forming.'
        else:
            row.update(status='review_rule_needs_primary_document_classification', recovery=None)
        rows.append(row)
    return {'evaluation_version': 'frozen-catalog-evaluation-0.4.1',
            'snapshot_id': snapshot['snapshot_id'], 'snapshot_content_hash': hybrid.digest(snapshot),
            'catalog': summary(catalog), 'evaluated_split': split, 'heldout': False,
            'reference_audit_hash': hybrid.digest(reference_report) if reference_report else None,
            'primary_source_identity': 'verified_metadata' if reference_report else 'legacy_not_machine_verified',
            'runtime': runs.runtime_snapshot(),
            'code_version': runs.code_version(),
            'provenance': provenance, 'rows': rows,
            'status_counts': dict(Counter(r['status'] for r in rows)),
            'precision_at_15': None, 'weak_signal_detection': 'not_evaluated',
            'interpretation': 'Отдельная оценка retrieval/recovery/канонизации после генерации; не разметка слабых сигналов.'}


def render(catalog: dict) -> str:
    lines = ['# Проверочный каталог Horizon v0.4 — предварительный', '',
             'Версия: ' + catalog['version'], '',
             '24 кейса: 16 предложенных исследовательских линий и 8 отрицательных наблюдений.',
             'Это не train set и не gold-разметка слабых сигналов. Резерв не ослеплён независимо.', '',
             '| Кейс | Семейство / раздел | Вид | Срез | Первичные ссылки |',
             '|---|---|---|---|---|']
    for c in catalog['cases']:
        refs = ' · '.join(f'[{i}](https://arxiv.org/abs/{i})' for i in c['arxiv_ids'])
        lines.append(f"| {c['title']} | {c['family_id']} / {c['split']} | {c['kind']} | {c['as_of_date']} | {refs} |")
    lines += ['', '## Комментарии по каждому кейсу', '']
    for c in catalog['cases']:
        lines += [f"### {c['case_id']}: {c['title']}", '', c['rationale'], '']
    lines += ['## Права и применение', '',
              'Включены идентификаторы, ссылки и собственные комментарии, не полные тексты.',
              'Метаданные arXiv и OpenAlex доступны под CC0; права на PDF и отдельные версии проверяются отдельно.',
              '[arXiv: metadata license](https://info.arxiv.org/help/license/index.html#metadata-license) · '
              '[OpenAlex: license](https://github.com/ourresearch/openalex-docs/blob/main/license.md)', '',
              'Метки слабого сигнала, будущего роста и рыночного успеха отсутствуют. Обзор или дубль — '
              'отрицательная проверка наблюдения, не утверждение, что технология не получила развития.', '']
    return '\n'.join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description='Проверить каталог и оценить сохранённый гибрид без вызова детектора')
    p.add_argument('--catalog', type=Path, default=CATALOG_PATH)
    p.add_argument('--snapshot')
    p.add_argument('--reference-audit', type=Path,
                   help='Required frozen official metadata audit for catalogs with reference_titles')
    p.add_argument('--export', type=Path, required=True)
    p.add_argument('--document', type=Path)
    args = p.parse_args()
    catalog = load(args.catalog)
    references = json.loads(args.reference_audit.read_text()) if args.reference_audit else None
    report = evaluate(hybrid.read(args.snapshot), catalog, reference_report=references) if args.snapshot else summary(catalog)
    args.export.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.document:
        args.document.write_text(render(catalog), encoding='utf-8')
    print(report.get('status_counts', report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
