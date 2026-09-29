"""Controlled corpus/policy comparison; not a precision or market-success test."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import yaml

from saia import composition_assessment as ca, hybrid, methodology


def snapshot_context(left: dict, right: dict) -> dict:
    for key in ('as_of_date', 'period_from', 'period_end_exclusive', 'window_step',
                'version', 'policy_hash', 'lexical_policy_hash'):
        if left[key] != right[key]:
            raise ValueError('Разные периоды или паспорта генераторов: ' + key)
    for key in ('embedding_model', 'quality_methodology_hash'):
        if left['provenance'][key] != right['provenance'][key]:
            raise ValueError('Разная модель или методика: ' + key)
    return {'period_from': left['period_from'], 'period_end_exclusive': left['period_end_exclusive'],
            'as_of_date': left['as_of_date'], 'window_step': left['window_step'],
            'generator_policy_hash': left['policy_hash'], 'lexical_policy_hash': left['lexical_policy_hash'],
            'embedding_model': left['provenance']['embedding_model'],
            'baseline_eligible_works': left['counts']['eligible_corpus'],
            'enriched_eligible_works': right['counts']['eligible_corpus'],
            'same_runtime': left['runtime'] == right['runtime'],
            'same_input_text': left['input_text_hash'] == right['input_text_hash'],
            'same_code': left['provenance']['code_version'] == right['provenance']['code_version']}


def summary(report: dict) -> dict:
    rows = report['candidates']
    statuses = dict(Counter(r['assessment']['status'] for r in rows))
    if statuses != report['counts']:
        raise ValueError('Свод статусов не соответствует карточкам.')
    unknowns = Counter(g['gate'] for r in rows for g in r['assessment']['gates'] if g['passed'] is None)
    return {'snapshot_id': report['snapshot_id'], 'snapshot_sha256': report['snapshot_content_sha256'],
            'assessment_sha256': report['assessment_content_sha256'],
            'policy_version': report['version'], 'policy_hash': report['policy_hash'],
            'provenance': report['provenance'], 'candidate_count': len(rows),
            'statuses': statuses, 'unknown_checks': dict(sorted(unknowns.items()))}


def policy_effect(left: dict, right: dict) -> dict:
    """Compare only the same exact compositions; never match cross-corpus labels."""
    for key in ('snapshot_id', 'snapshot_content_sha256', 'input_text_hash', 'methodology_hash', 'measurement_policy_hash'):
        if left[key] != right[key]:
            raise ValueError('Изменение политики проверяется на одном точном снимке и остальных паспортах.')
    a = {r['candidate_id']: r for r in left['candidates']}
    b = {r['candidate_id']: r for r in right['candidates']}
    if len(a) != len(left['candidates']) or len(b) != len(right['candidates']) or a.keys() != b.keys():
        raise ValueError('Составы кандидатов не совпали.')
    changed_statuses, changed_checks = Counter(), Counter()
    for identifier in a:
        x, y = a[identifier], b[identifier]
        if x['work_ids'] != y['work_ids']:
            raise ValueError('Один candidate_id обозначает разные составы.')
        before, after = x['assessment']['status'], y['assessment']['status']
        if before != after:
            changed_statuses[before + ' -> ' + after] += 1
        old_gates = {g['gate']: g['passed'] for g in x['assessment']['gates']}
        new_gates = {g['gate']: g['passed'] for g in y['assessment']['gates']}
        for gate in old_gates.keys() | new_gates.keys():
            if gate not in old_gates or gate not in new_gates or old_gates[gate] != new_gates[gate]:
                changed_checks[gate] += 1
    return {'same_exact_compositions': True, 'changed_statuses': dict(changed_statuses),
            'changed_check_outcomes': dict(sorted(changed_checks.items()))}


def comparison(baseline: dict, baseline_new_policy: dict, enriched_old_policy: dict, enriched: dict) -> dict:
    reports = [baseline, baseline_new_policy, enriched_old_policy, enriched]
    for key in ('as_of_date', 'methodology_hash', 'measurement_policy_hash'):
        if len({r[key] for r in reports}) != 1:
            raise ValueError('Для сравнения нужны одинаковые дата и остальные паспорта.')
    if baseline['policy_hash'] != enriched_old_policy['policy_hash'] or baseline_new_policy['policy_hash'] != enriched['policy_hash']:
        raise ValueError('Политики двух корпусов не сопоставимы.')
    return {'experiment_version': 'openalex-enrichment-2x2-0.4-experimental',
            'cells': {'arxiv_old_policy': summary(baseline), 'arxiv_new_policy': summary(baseline_new_policy),
                      'enriched_old_policy_diagnostic_only': summary(enriched_old_policy),
                      'enriched_new_policy': summary(enriched)},
            'policy_effect_on_arxiv': policy_effect(baseline, baseline_new_policy),
            'policy_effect_on_enriched': policy_effect(enriched_old_policy, enriched),
            'limits': ['Старую политику на новом корпусе используем только как диагностический контроль, не как рекомендованный результат.',
                       'Между корпусами меняются тексты, даты, фильтрация, авторские сведения и состав кластеров; это не изолированный эффект одного поля.',
                       'Идентификаторы и одинаковые названия кандидатов разных корпусов не доказывают одинаковый состав. Переходы статусов между ними не рассчитаны.',
                       'Число кандидатов и изменения проверок не доказывают повышение precision/recall; независимой разметки истинных сигналов для этих выдач нет.',
                       'OpenAlex здесь обогащает DOI-связанные записи arXiv, а не независимо открывает всю область ИИ. Первичность, историческая независимость и рынок не установлены.']}


def run(baseline_report: Path, new_snapshot: str, export_dir: Path) -> dict:
    baseline = json.loads(baseline_report.read_text(encoding='utf-8'))
    # Verify the preserved report by exact scientific replay before comparison.
    ca.replay(baseline)
    snapshot = hybrid.read(new_snapshot)
    context = snapshot_context(hybrid.read(baseline['snapshot_id']), snapshot)
    if snapshot['missing_vector_work_ids']:
        raise ValueError('Новый корпус ещё не имеет всех векторов.')
    config = methodology.from_mapping(baseline['effective_methodology'])
    old_policy = baseline['effective_policy']
    measure = baseline['effective_measurement_policy']
    new_policy = yaml.safe_load(ca.POLICY_PATH.read_text(encoding='utf-8'))
    names = ['arxiv-new-policy.json', 'enriched-old-policy-diagnostic.json', 'enriched-new-policy.json', 'comparison.json']
    export_dir.mkdir(parents=True, exist_ok=True)
    if any((export_dir / n).exists() for n in names):
        raise ValueError('Эксперимент уже имеет файлы; нужен новый каталог, ничего не перезаписывается.')
    baseline_new = ca.analyze(baseline['snapshot_id'], config, new_policy, measure)
    enriched_old = ca.analyze(new_snapshot, config, old_policy, measure)
    enriched_new = ca.analyze(new_snapshot, config, new_policy, measure)
    result = comparison(baseline, baseline_new, enriched_old, enriched_new)
    result['baseline_report_path'] = str(baseline_report.resolve())
    result['enriched_hybrid_counts'] = snapshot['counts']
    result['verified_snapshot_context'] = context
    result['comparison_sha256'] = hybrid.digest(result)
    for name, value in zip(names, [baseline_new, enriched_old, enriched_new, result]):
        with (export_dir / name).open('x', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Раздельно сравнить изменение корпуса и политики, без подмены точности числом кандидатов')
    parser.add_argument('--baseline-report', type=Path, required=True)
    parser.add_argument('--new-snapshot', required=True)
    parser.add_argument('--export-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    result = run(args.baseline_report, args.new_snapshot, args.export_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
