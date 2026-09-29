"""Версионированные относительные ряды; НЕ классификатор слабых сигналов.

Компонент предназначен для сравнения генераторов на сопоставимом корпусе.
Изменение доли и неопределённость показываются отдельно от статуса темы.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from saia.observed_series import describe_observed

POLICY_PATH = Path(__file__).resolve().parents[1] / 'config' / 'measurement.v0.4.yaml'


@dataclass(frozen=True)
class WindowCounts:
    start: date
    end: date  # исключительная верхняя граница
    topic_works: int
    corpus_works: int | None
    complete: bool = True
    coverage_comparable: bool | None = None


def wilson_interval(n: int, total: int | None, z: float) -> tuple[float, float] | None:
    if total is None or total == 0:
        return None
    p = n / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0., centre - radius), min(1., centre + radius)


def analyze_series(windows: list[WindowCounts], as_of: date, policy: dict | None = None) -> dict:
    policy = policy if policy is not None else yaml.safe_load(POLICY_PATH.read_text())
    z = float(policy['wilson_z'])
    history = int(policy['history_windows'])
    if history < 2 or z <= 0:
        raise ValueError('Некорректная конфигурация измерений.')
    if not windows:
        raise ValueError('Нужны окна наблюдения.')
    points = []
    for index, w in enumerate(windows):
        if w.start >= w.end or w.end > as_of:
            raise ValueError('Окно должно закончиться не позже даты расчёта.')
        if index and windows[index - 1].end != w.start:
            raise ValueError('Окна должны идти подряд, включая пустые периоды.')
        if w.topic_works < 0 or (w.corpus_works is not None and (
                w.corpus_works < w.topic_works or w.corpus_works < 0)):
            raise ValueError('Число работ темы не может превышать корпус или быть отрицательным.')
        # Неполным может быть только последнее окно; сравнивать его с полным нельзя.
        if not w.complete and index != len(windows) - 1:
            raise ValueError('Неполное окно допустимо только в конце ряда.')
        points.append({
            'start': w.start.isoformat(), 'end': w.end.isoformat(),
            'topic_works': w.topic_works, 'corpus_works': w.corpus_works,
            'share': w.topic_works / w.corpus_works if w.corpus_works else None,
            'share_interval': wilson_interval(w.topic_works, w.corpus_works, z),
            'complete': w.complete, 'coverage_comparable': w.coverage_comparable,
        })
    full = [p for p in points if p['complete']]
    recent = full[-history:]
    # A missing passport is unknown, not a successful coverage check. An
    # explicit failed check dominates unknown; preserve all three states.
    coverage_states = [p['coverage_comparable'] for p in recent]
    comparable = (False if any(s is False for s in coverage_states) else
                  True if coverage_states and all(s is True for s in coverage_states) else None)
    durations = {(date.fromisoformat(p['end']) - date.fromisoformat(p['start'])).days
                 for p in recent}
    # Календарные кварталы/годы слегка отличаются длиной. Более крупная
    # разница означает несопоставимые окна, а не технологический рост.
    same_scale = bool(durations) and max(durations) - min(durations) <= int(
        policy['max_calendar_duration_difference_days'])
    valid = (len(recent) == history and comparable is True and same_scale
             and all(p['share'] is not None for p in recent))
    observed_streak = 0
    for p in reversed(full):
        if p['topic_works'] == 0:
            break
        observed_streak += 1
    # Ноль найденных активных окон и неизвестная устойчивость — разные
    # результаты. Не превращать пропуск покрытия в отрицательное измерение.
    streak = 0 if full else None
    for p in reversed(full):
        if p['coverage_comparable'] is not True or p['corpus_works'] is None or p['corpus_works'] == 0:
            streak = None
            break
        if p['topic_works'] == 0:
            break
        streak += 1
    slope = change = change_interval = count_change = None
    direction = 'not_established'
    if valid:
        values = [p['share'] for p in recent]
        midpoint = (len(values) - 1) / 2
        average = sum(values) / len(values)
        slope = sum((i - midpoint) * (v - average) for i, v in enumerate(values)) / sum(
            (i - midpoint) ** 2 for i in range(len(values)))
        change = values[-1] - values[0]
        count_change = recent[-1]['topic_works'] - recent[0]['topic_works']
        first_ci, last_ci = recent[0]['share_interval'], recent[-1]['share_interval']
        change_interval = (last_ci[0] - first_ci[1], last_ci[1] - first_ci[0])
        # Direction — описание точечной оценки, не статистическая значимость.
        direction = 'increasing' if slope > 0 and change > 0 else (
            'decreasing' if slope < 0 and change < 0 else 'flat_or_mixed')
    digest = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()[:16]
    return {
        'measurement_version': policy['version'], 'policy_hash': digest,
        'as_of_date': as_of.isoformat(), 'points': points,
        'required_history_windows': history, 'used_full_windows': len(recent),
        'consecutive_active_full_windows': streak,
        'observed_consecutive_active_full_windows': observed_streak,
        'share_slope_per_window': slope, 'share_change': change,
        'share_change_interval': change_interval,
        'absolute_count_change': count_change, 'direction': direction,
        'partial_window_excluded_from_comparison': not windows[-1].complete,
        'coverage_comparable': comparable, 'window_scale_comparable': same_scale,
        'observed_sample': describe_observed(recent, history, same_scale, comparable,
                                            int(policy['max_calendar_duration_difference_days'])),
        'interpretation': 'Измерение публикационного ряда, не доказательство слабого сигнала или рынка.',
        'uncertainty_note': 'Wilson использует биномиальное приближение; работы могут быть зависимы. Интервал разности описательный, не тест прогноза.',
    }
