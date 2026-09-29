"""Read-only, honest visual projection of one immutable candidate snapshot.

No scores, maturity, status changes, or missing measurements are inferred here.
Unknown dynamics remain outside the two-dimensional map, not at zero.
"""
from __future__ import annotations

import math
from collections import Counter

from saia.hybrid import digest
from saia.runs import code_version


GATE_DESCRIPTIONS = {
    'G0_volume': ('Количество публикаций', 'Достаточно ли отдельных канонических публикаций по теме.'),
    'G0_concentration': ('Зависимость от одной организации', 'Не сосредоточено ли большинство работ в одной организации.'),
    'G1_publication_prevalence': ('Проверка массовости темы', 'Проверяется публикационная распространённость, а не рынок. Высокий процентиль сам по себе не доказывает массовость.'),
    'G2_novelty': ('Новизна в сохранённом корпусе', 'Отличается ли тема от более ранних тем в сопоставимом корпусе.'),
    'G3_persistence': ('Повторение во времени', 'Встречается ли тема в достаточном числе полных временных окон.'),
    'G3_independent_teams': ('Разные исследовательские команды', 'Есть ли основания считать авторские группы разными; число статей не равно числу команд.'),
    'G4_momentum': ('Рост относительно других тем', 'Выделяется ли динамика темы среди сопоставимых тем.'),
    'G6_primary_sources': ('Первичные исследования', 'Есть ли подтверждённые первичные исследования, а не только обзоры и пересказы.'),
    'G0_independent_orgs': ('Разные организации', 'Подтверждены ли разные организации; это не автоматически независимые лаборатории.'),
    'G5_age': ('Возраст наблюдаемой линии', 'Не слишком ли давно линия наблюдается в корпусе; это не установленная дата её рождения.'),
    'G4_positive_share_slope': ('Направление изменения доли', 'Растёт ли доля темы среди публикаций корпуса по полным сопоставимым окнам.'),
    'G4_positive_share_change': ('Изменение доли между окнами', 'Увеличилась ли доля темы, а не только абсолютное число статей.'),
    'G3_consecutive': ('Последовательное повторение', 'Есть ли несколько последовательных полных окон с работами по теме.'),
    'G_coherence': ('Смысловая связность', 'Достаточно ли похожи публикации по сохранённым векторам одной модели.'),
    'G_coherence_calibration': ('Проверка порога связности', 'Проверен ли порог этой модели на размеченных примерах; одного высокого сходства недостаточно.'),
    'G_coverage': ('Сопоставимость источников во времени', 'Можно ли сравнивать окна без изменения правил и полноты сбора.'),
    'G_embedding_coverage': ('Полнота векторов', 'Достаточно ли работ корпуса представлено векторами выбранной модели.'),
}


def present_gate(gate: dict, observed: dict | None = None, policy: dict | None = None) -> dict:
    """Explain a saved check without changing its outcome or original rule note."""
    label, question = GATE_DESCRIPTIONS.get(gate['gate'], (gate['gate'], 'Проверяется условие сохранённой методики.'))
    if gate['passed'] is None:
        explanation = 'Недостаточно данных для вывода. Это не означает, что условие нарушено. ' + question
    elif gate['passed']:
        explanation = 'Условие этой проверки выполнено; это не подтверждение сигнала в целом. ' + question
    else:
        explanation = 'Условие этой проверки не выполнено. ' + question + ' ' + gate['reason']
    floor = (policy or {}).get('min_last_full_window_share_for_widespread')
    if gate['gate'] == 'G1_publication_prevalence' and floor is not None:
        share = (observed or {}).get('publication_share')
        share_text = f'{share * 100:.3f}%' if share is not None else 'неизвестна'
        rank_text = str(gate.get('observed')) if gate.get('observed') is not None else 'неизвестен'
        explanation += (f" Для отнесения к массовой публикационной теме нужны оба условия: "
                        f"процентиль выше {gate['threshold']} и доля не ниже {floor * 100:g}%. "
                        f"Здесь процентиль {rank_text}, доля {share_text}. "
                        "Пройденная проверка означает, что правило массовости не сработало, а не что все проверки новизны пройдены.")
    return {**gate, 'display_name': label, 'display_explanation': explanation}


def project(snapshot: dict, saved_assessment: dict | None = None) -> dict:
    assessed = {}
    if saved_assessment is not None:
        from saia.assessment_store import validate_report
        validate_report(snapshot, saved_assessment['report'])
        assessed = {c['candidate_id']: c for c in saved_assessment['report']['candidates']}
    rows = []
    seen = set()
    for c in snapshot['candidates']:
        candidate_id = c['candidate_id']
        if candidate_id in seen:
            raise ValueError('Повтор идентификатора кандидата в снимке.')
        seen.add(candidate_id)
        channels = c['channels']
        if not channels or set(channels) - {'lexical', 'semantic'} or len(set(channels)) != len(channels):
            raise ValueError('Неизвестный или повторённый канал кандидата.')
        series = c['publication_series']
        full = [p for p in series['points'] if p['complete']]
        last = full[-1] if full else None
        share = last['share'] if last else None
        if share is not None and (not math.isfinite(share) or not 0 <= share <= 1):
            raise ValueError('Некорректная доля публикаций.')
        reasons = []
        slope = series['share_slope_per_window']
        if not series.get('coverage_comparable'):
            slope = None
            reasons.append('Сопоставимость покрытия по окнам не подтверждена.')
        if not series.get('window_scale_comparable'):
            slope = None
            reasons.append('Сопоставимость длительности окон не подтверждена.')
        if slope is not None and not math.isfinite(slope):
            raise ValueError('Некорректное изменение доли.')
        if share is None:
            reasons.append('Нет доли в последнем полном окне; раннее окно не подставляется.')
        if slope is None and not reasons:
            reasons.append('Недостаточно пригодных полных окон для изменения доли.')
        # A hybrid snapshot has no assessed status yet. Do not inherit a
        # semantic line's forming/watch status onto a different composition.
        if c['status'] != 'unassessed_candidate':
            raise ValueError('Эта проекция принимает только неоценённые гибридные составы.')
        sector = 'both' if len(channels) == 2 else channels[0]
        rows.append({
            'candidate_id': candidate_id, 'label': c['label'],
            'channels': channels, 'radar_sector': sector,
            'radar_ring': 'unassessed', 'status': c['status'],
            'document_support': c['document_support'],
            'first_observed_in_corpus': c['first_observed_in_corpus'],
            'last_full_window': {'start': last['start'], 'end': last['end'],
                                 'topic_works': last['topic_works'],
                                 'corpus_works': last['corpus_works']} if last else None,
            'share': share, 'share_interval': last['share_interval'] if last else None,
            'share_slope_per_window': slope,
            'map_position': {'x_share': share, 'y_share_slope': slope}
                            if share is not None and slope is not None else None,
            'map_missing_reasons': reasons,
            'unknown_checks': c['unknown_checks'],
            'publication_series': series, 'contexts': c['contexts'],
            'overlap_links': c['overlap_links'], 'work_ids': c['work_ids'],
        })
        if assessed:
            assessment = assessed[candidate_id]
            displayed_gates = [present_gate(g, assessment['observed'], saved_assessment['report']['effective_policy'])
                               for g in assessment['assessment']['gates']]
            rows[-1].update(status=assessment['assessment']['status'],
                            radar_ring=assessment['assessment']['status'], assessment=assessment['assessment'],
                            display_gates=displayed_gates,
                            measured=assessment['observed'], normalized=assessment['normalized'],
                            blocking_unknowns=assessment['blocking_unknowns'], blocking_failures=assessment['blocking_failures'],
                            unknown_checks=[g['display_explanation'] for g in displayed_gates if g['passed'] is None])
    from saia.publication_observation import load_policy, observe
    observation_policy = load_policy()
    for row in rows:
        row['publication_observation'] = observe(row, observation_policy, row.get('measured'))
    rows.sort(key=lambda c: (c['label'].casefold(), c['candidate_id']))
    result = {
        'projection_version': 'candidate-portfolio-0.4-experimental',
        'code_version': code_version(),
        'snapshot_id': snapshot['snapshot_id'], 'snapshot_content_sha256': digest(snapshot),
        'provenance': snapshot['provenance'], 'as_of_date': snapshot['as_of_date'],
        'period_from': snapshot['period_from'], 'period_end_exclusive': snapshot['period_end_exclusive'],
        'window_step': snapshot['window_step'],
        'counts': {'candidates': len(rows), 'map_positioned': sum(c['map_position'] is not None for c in rows),
                   'map_unpositioned': sum(c['map_position'] is None for c in rows)},
        'map_axes': {'x': 'Доля в загруженном корпусе в последнем полном окне',
                     'y': 'Наклон доли за сопоставимые полные окна'},
        'radar': {'sectors': {'lexical': 'Буквальный канал', 'semantic': 'Семантический канал',
                              'both': 'Оба канала: одинаковый состав'},
                  'rings': {'unassessed': 'Состав ещё не оценён'},
                  'interpretation': 'Радар происхождения кандидатов, не рыночной зрелости или силы сигнала.'},
        'candidates': rows,
        'publication_observations': {
            'version': observation_policy['version'], 'policy_hash': digest(observation_policy),
            'trajectory_version': 'publication-trajectory-0.4.4',
            'screening_policy_binding': 'live_policy_projection_not_saved_screen',
            'effective_policy': observation_policy,
            'diffusion_evidence_assessment_id': saved_assessment['assessment_id'] if saved_assessment else None,
            'diffusion_evidence_sha256': saved_assessment['report']['assessment_content_sha256'] if saved_assessment else None,
            'stages': dict(Counter(c['publication_observation']['stage'] for c in rows)),
            'long_window_candidates_with_recent_decline': sum(
                c['publication_observation']['stage'] == 'publication_signal_candidate' and (
                    c['publication_observation']['recent_trajectory']['positive_long_window_but_recent_count_decline'] or
                    c['publication_observation']['recent_trajectory']['positive_long_window_but_recent_share_decline'])
                for c in rows),
            'map_positioned': sum(c['publication_observation']['map_position'] is not None for c in rows),
            'interpretation': 'Отдельный описательный слой: его кандидаты не являются forming старой оценки или подтверждёнными экспертами сигналами.',
        },
        'limitations': snapshot['limitations'] + [
            'Неизвестная динамика не заменяется нулём и не размещается на двумерной карте.',
            'Нулевая доля последнего окна допустима; она не означает отсутствия всей исследовательской линии.',
            'Доли относятся к загруженному корпусу; два канала не являются двумя независимыми источниками.',
            'Пересекающиеся кандидаты не дают аддитивных долей; суммировать их доли нельзя.',
            'Визуализация не меняет статус, не подтверждает новизну и не рекомендует инвестиции.',
        ],
    }
    if saved_assessment is not None:
        result['saved_assessment'] = {k: saved_assessment[k] for k in ('assessment_id', 'created_at')}
        result['saved_assessment'].update(version=saved_assessment['report']['version'],
                                          policy_hash=saved_assessment['report']['policy_hash'],
                                          content_sha256=saved_assessment['report']['assessment_content_sha256'])
        result['counts']['assessment_statuses'] = dict(Counter(c['status'] for c in rows))
        result['radar']['rings'] = {'candidate': 'Кандидат: есть непройденные проверки',
                                    'watch': 'Наблюдать: обязательные основания не подтверждены',
                                    'forming': 'Формируется по выбранной экспериментальной методике',
                                    'widespread': 'Распространён в корпусе', 'mature': 'Статус прежней методики'}
        result['radar']['interpretation'] = 'Статусы выбранной сохранённой оценки, не вероятность истинности или рыночная зрелость.'
        result['limitations'].extend(saved_assessment['report']['limitations'])
    return result
