"""Descriptive screening, separate from immutable scientific assessments.

Recompute only arithmetic from frozen window counts. Never set source
coverage to true, replace unknown by zero, or promote an expert validation.
"""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import yaml

from saia.hybrid import digest

POLICY_PATH = Path(__file__).resolve().parents[1] / 'config' / 'publication-observation.v0.4.3.yaml'
LABELS = {
    'publication_signal_candidate': 'Публикационный кандидат в слабый сигнал',
    'widespread_topic': 'Распространённая публикационная тема',
    'declining_activity': 'Затухание в загруженном корпусе',
    'single_group_growth': 'Рост преимущественно одной авторской группы',
    'growth_watch': 'Наблюдаемый рост: нужна проверка устойчивости',
    'stable_or_mixed': 'Стабильная или разнонаправленная динамика',
    'insufficient_history': 'Недостаточно данных для динамики',
}


def load_policy() -> dict:
    return yaml.safe_load(POLICY_PATH.read_text(encoding='utf-8'))


def _slope(values) -> float:
    midpoint = (len(values) - 1) / 2
    mean = sum(values) / len(values)
    return sum((i - midpoint) * (value - mean) for i, value in enumerate(values)) / sum(
        (i - midpoint) ** 2 for i in range(len(values)))


def observe(candidate: dict, policy: dict, measured: dict | None = None) -> dict:
    history = policy['history_windows']
    active_min = policy['min_active_windows']
    if (not isinstance(history, int) or isinstance(history, bool) or history < 2
            or not isinstance(active_min, int) or isinstance(active_min, bool) or not 1 <= active_min <= history):
        raise ValueError('Invalid descriptive window policy')
    for name in ('min_document_support', 'min_last_window_works', 'max_calendar_duration_difference_days'):
        value = policy[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError('Invalid descriptive threshold')
    floor = policy['max_small_topic_share']
    if not math.isfinite(floor) or not 0 < floor <= 1:
        raise ValueError('Invalid descriptive share threshold')
    support = candidate['document_support']
    if not isinstance(support, int) or isinstance(support, bool) or support < 0:
        raise ValueError('Invalid document support')
    full = []
    previous_end = None
    points = candidate['publication_series']['points']
    for index, point in enumerate(points):
        start, end = date.fromisoformat(point['start']), date.fromisoformat(point['end'])
        if start >= end or (previous_end is not None and start != previous_end):
            raise ValueError('Descriptive windows must be nonempty and consecutive')
        previous_end = end
        if point['complete'] is not True:
            if point['complete'] is not False or index != len(points) - 1:
                raise ValueError('Only the last descriptive window may be partial')
            continue
        count, total = point['topic_works'], point.get('corpus_works')
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError('Invalid topic count')
        if total is not None and (not isinstance(total, int) or isinstance(total, bool) or total < count):
            raise ValueError('Invalid corpus denominator')
        share = count / total if total else None
        stored = point.get('share')
        if (stored is None) != (share is None) or (stored is not None and (
                not math.isfinite(stored) or not math.isclose(stored, share, abs_tol=1e-12, rel_tol=1e-9))):
            raise ValueError('Descriptive share must match frozen counts')
        full.append({'start': point['start'], 'end': point['end'], 'topic_works': count,
                     'corpus_works': total, 'share': share,
                     'coverage_comparable': point.get('coverage_comparable'),
                     'duration_days': (end - start).days})
    recent = full[-history:]
    durations = [p['duration_days'] for p in recent]
    valid = (len(recent) == history and all(p['share'] is not None for p in recent)
             and max(durations) - min(durations) <= policy['max_calendar_duration_difference_days'])
    observed = {
        'used_full_windows': len(recent), 'required_full_windows': history,
        'last_window_works': recent[-1]['topic_works'] if recent else None,
        'last_window_share': recent[-1]['share'] if recent else None,
        'count_slope_per_window': None, 'count_change': None,
        'share_slope_per_window': None, 'share_change': None,
        'consecutive_active_windows': None,
    }
    limits = ['Это отбор по наблюдениям в загруженном корпусе, не подтверждение слабого сигнала, статистической значимости или рынка.',
              'Первичность исследований, новизна и порог связности требуют отдельной проверки.']
    coverage_verified = bool(recent) and all(p['coverage_comparable'] is True for p in recent)
    if not coverage_verified:
        limits.append('Сопоставимость покрытия не подтверждена: динамика может отражать изменение представленности источника.')
    measured = measured or {}
    teams, orgs = measured.get('independent_teams_proxy'), measured.get('independent_orgs_proxy')
    if teams is None:
        limits.append('Диффузия по авторским группам не установлена; неизвестность не заменена нулём.')
    else:
        limits.append('Число различимых авторских групп — приближение, не доказательство независимости лабораторий или роста диффузии во времени.')
    stage = 'insufficient_history'
    trajectory = {'version': 'publication-trajectory-0.4.4',
                  'last_count_change': None, 'last_share_change': None,
                  'count_drawdown_from_peak': None, 'share_drawdown_from_peak': None,
                  'positive_long_window_but_recent_count_decline': None,
                  'positive_long_window_but_recent_share_decline': None,
                  'baseline_window_has_zero_topic_works': None,
                  'interpretation': 'Последнее изменение и спад от пика в выбранных полных окнах; не доказательство провала технологии.'}
    if valid:
        counts = [p['topic_works'] for p in recent]
        shares = [p['share'] for p in recent]
        streak = 0
        for count in reversed(counts):
            if count == 0:
                break
            streak += 1
        observed.update(count_slope_per_window=_slope(counts), count_change=counts[-1] - counts[0],
                        share_slope_per_window=_slope(shares), share_change=shares[-1] - shares[0],
                        consecutive_active_windows=streak)
        increasing = (observed['count_slope_per_window'] > 0 and observed['count_change'] > 0
                      and observed['share_slope_per_window'] > 0 and observed['share_change'] > 0)
        trajectory.update(
            last_count_change=counts[-1] - counts[-2],
            last_share_change=shares[-1] - shares[-2],
            count_drawdown_from_peak=max(counts) - counts[-1],
            share_drawdown_from_peak=max(shares) - shares[-1],
            positive_long_window_but_recent_count_decline=increasing and counts[-1] < counts[-2],
            positive_long_window_but_recent_share_decline=increasing and shares[-1] < shares[-2],
            baseline_window_has_zero_topic_works=counts[0] == 0)
        if trajectory['positive_long_window_but_recent_count_decline'] or trajectory['positive_long_window_but_recent_share_decline']:
            limits.append('Недавний спад: положительное сравнение за длинный период не означает рост в последнем окне. Старое правило отбора оставлено для сопоставления, это аргумент против текущего роста.')
        if trajectory['baseline_window_has_zero_topic_works']:
            limits.append('В первом сравниваемом окне нет работ темы. Нулевая база может завышать впечатление роста; это не доказательство появления новой идеи.')
        decreasing = (observed['count_slope_per_window'] < 0 and observed['count_change'] < 0
                      and observed['share_slope_per_window'] < 0 and observed['share_change'] < 0)
        if decreasing:
            stage = 'declining_activity'
        elif shares[-1] >= floor:
            stage = 'widespread_topic'
        elif increasing and teams == 1:
            stage = 'single_group_growth'
        elif (increasing and streak >= active_min and support >= policy['min_document_support']
              and counts[-1] >= policy['min_last_window_works']):
            stage = 'publication_signal_candidate'
        elif increasing:
            stage = 'growth_watch'
        else:
            stage = 'stable_or_mixed'
    else:
        limits.append('Не хватает полных окон, знаменателя или одинаковой длительности; динамика не вычислена.')
    return {
        'version': policy['version'], 'policy_hash': digest(policy),
        'stage': stage, 'display_name': LABELS[stage], 'observed': observed,
        'recent_trajectory': trajectory,
        'diffusion': {'distinct_author_groups_proxy': teams, 'distinct_organisations_proxy': orgs,
                      'temporal_diffusion_direction': None},
        'coverage_verified': coverage_verified,
        'validation': {'state': 'not_validated_by_this_layer', 'confirmed_weak_signal': None},
        'map_position': {'x_share': observed['last_window_share'], 'y_share_slope': observed['share_slope_per_window']}
                        if valid else None,
        'limitations': limits,
    }
