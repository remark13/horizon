"""Объяснимые шлюзы, score и confidence для одного тематического кандидата."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Mapping

from saia.methodology import Methodology


@dataclass(frozen=True)
class CandidateMetrics:
    doc_count: int
    independent_orgs: int | None
    independent_teams: int | None
    single_org_share: float | None
    maturity_percentile: float | None
    novelty_percentile: float | None
    windows_present: int | None
    momentum_percentile: float | None
    primary_sources: int | None
    age_years: float | None
    normalized: Mapping[str, float | None] = field(default_factory=dict)
    penalties: Mapping[str, float] = field(default_factory=dict)
    confidence_components: Mapping[str, float | None] = field(default_factory=dict)
    share_slope: float | None = None
    share_change: float | None = None
    consecutive_active_windows: int | None = None
    coverage_comparable: bool | None = None
    coherence_calibrated: bool | None = None
    embedding_coverage: float | None = None
    publication_share: float | None = None


@dataclass(frozen=True)
class GateResult:
    gate: str
    passed: bool | None
    severity: str
    observed: float | int | None
    threshold: float | int | None
    reason: str


@dataclass(frozen=True)
class Assessment:
    status: str
    emergence_score: float | None
    evidence_confidence: float
    gates: tuple[GateResult, ...]
    missing_metrics: tuple[str, ...]
    applied_penalties: Mapping[str, float]

    def to_dict(self) -> dict:
        return asdict(self)


def _minimum(name: str, observed, threshold, reason: str) -> GateResult:
    passed = None if observed is None else observed >= threshold
    return GateResult(name, passed, "block", observed, threshold, reason)


def _maximum(name: str, observed, threshold, reason: str, severity: str = "block") -> GateResult:
    passed = None if observed is None else observed <= threshold
    return GateResult(name, passed, severity, observed, threshold, reason)


def evaluate_gates(metrics: CandidateMetrics, config: Methodology,
                   prevalence_share_floor: float | None = None) -> tuple[GateResult, ...]:
    gate_cfg = config.gates
    results = [
        _minimum("G0_volume", metrics.doc_count, gate_cfg["g0_min_canonical_works"],
                 "Недостаточно канонических публикаций."),
        _maximum("G0_concentration", metrics.single_org_share,
                 gate_cfg["g0_max_single_org_share"],
                 "Слишком большая доля одной организации."),
        _maximum("G1_publication_prevalence" if config.version == '0.4' else 'G1_maturity', metrics.maturity_percentile,
                 gate_cfg["g1_maturity_percentile_max"],
                 "Тема уже слишком распространена для слабого сигнала."),
        _minimum("G2_novelty", metrics.novelty_percentile,
                 gate_cfg["g2_novelty_percentile_min"],
                 "Новизна недостаточна относительно сопоставимых тем."),
        _minimum("G3_persistence", metrics.windows_present,
                 gate_cfg["g3_min_windows"],
                 "Тема не удержалась в необходимом числе окон."),
        _minimum("G3_independent_teams", metrics.independent_teams,
                 gate_cfg["g3_min_independent_teams"],
                 "Недостаточно независимых исследовательских команд."),
        _minimum("G4_momentum", metrics.momentum_percentile,
                 gate_cfg["g4_momentum_percentile_min"],
                 "Темп роста недостаточен относительно сопоставимых тем."),
        _minimum("G6_primary_sources", metrics.primary_sources,
                 gate_cfg["g6_min_primary_sources"],
                 "Недостаточно первичных источников."),
    ]
    if prevalence_share_floor is not None:
        if not math.isfinite(prevalence_share_floor) or not 0 < prevalence_share_floor <= 1:
            raise ValueError('Минимальная доля для распространённости должна быть в (0, 1].')
        share = metrics.publication_share
        if share is not None and (not math.isfinite(share) or not 0 <= share <= 1):
            raise ValueError('Доля публикаций должна быть в [0, 1].')
        pct = metrics.maturity_percentile
        limit = gate_cfg['g1_maturity_percentile_max']
        # Three-valued conjunction: high rank alone is insufficient. A
        # known low share or known low rank refutes widespread even if the
        # other measurement is missing. Otherwise missing evidence stays unknown.
        passed = (True if (share is not None and share < prevalence_share_floor)
                  or (pct is not None and pct <= limit) else
                  None if share is None or pct is None else False)
        results[2] = GateResult(results[2].gate, passed, 'block', pct, limit,
                                'Распространённость требует одновременно высокого ранга '
                                f'и доли не ниже {prevalence_share_floor:g}; наблюдаемая доля: {share}. '
                                'Численный порог экспериментален, не рыночная зрелость.')
    if gate_cfg.get("g0_min_independent_orgs") is not None:
        results.append(_minimum(
            "G0_independent_orgs", metrics.independent_orgs,
            gate_cfg["g0_min_independent_orgs"],
            "Недостаточно независимых организаций.",
        ))
    if gate_cfg.get("g5_age_years_max") is not None:
        results.append(_maximum(
            "G5_age", metrics.age_years, gate_cfg["g5_age_years_max"],
            "Исследовательская линия слишком старая; требуется проверка re-emergence.",
            "warn" if gate_cfg.get("g5_mode") == "warn" else "block",
        ))
    for name, observed, key in (
        ('G4_positive_share_slope', metrics.share_slope, 'g4_min_share_slope'),
        ('G4_positive_share_change', metrics.share_change, 'g4_min_share_change'),
    ):
        if key in gate_cfg:
            threshold = gate_cfg[key]
            results.append(GateResult(name, None if observed is None else observed > threshold,
                                      'block', observed, threshold,
                                      'Требуется положительное изменение доли, а не только высокий процентиль.'))
    if gate_cfg.get('require_consecutive_windows'):
        results.append(_minimum('G3_consecutive', metrics.consecutive_active_windows,
                                gate_cfg['g3_min_windows'], 'Нужны последовательные полные активные окна.'))
    if 'g_coherence_min' in gate_cfg:
        results.append(_minimum('G_coherence', metrics.normalized.get('coherence'),
                                gate_cfg['g_coherence_min'], 'Связность ниже экспериментального допуска.'))
    if gate_cfg.get('require_coherence_calibration'):
        results.append(_minimum('G_coherence_calibration', metrics.coherence_calibrated, True,
                                'Для этой модели порог связности ещё не откалиброван.'))
    if gate_cfg.get('require_comparable_coverage'):
        results.append(_minimum('G_coverage', metrics.coverage_comparable, True,
                                'Сопоставимость покрытия не подтверждена.'))
    if 'min_embedding_coverage' in gate_cfg:
        results.append(_minimum('G_embedding_coverage', metrics.embedding_coverage,
                                gate_cfg['min_embedding_coverage'],
                                'Недостаточное покрытие корпуса векторами в последних полных окнах.'))
    return tuple(results)


def _score(metrics: CandidateMetrics, config: Methodology) -> tuple[float | None, tuple[str, ...]]:
    weights = config.score_weights
    missing = tuple(name for name in weights if metrics.normalized.get(name) is None)
    if missing:
        return None, missing
    raw = sum(float(weights[name]) * float(metrics.normalized[name]) for name in weights)
    allowed_penalties = config.raw["scoring"].get("penalties", {})
    penalty = sum(
        float(value) for name, value in metrics.penalties.items()
        if name in allowed_penalties
    )
    return round(max(0.0, min(1.0, raw - penalty)) * 100, 2), missing


def _confidence(metrics: CandidateMetrics, config: Methodology) -> float:
    stage = config.raw["confidence"]["stage"]
    weights = config.raw["confidence"][f"weights_{stage}"]
    # Пропуск даёт ноль и тем самым снижает уверенность; веса не
    # перенормируются, иначе отсутствие данных ошибочно не наказывалось бы.
    value = sum(
        float(weight) * float(metrics.confidence_components.get(name) or 0.0)
        for name, weight in weights.items()
    )
    return round(max(0.0, min(1.0, value)) * 100, 2)


def assess_candidate(metrics: CandidateMetrics, config: Methodology,
                     prevalence_share_floor: float | None = None) -> Assessment:
    gates = evaluate_gates(metrics, config, prevalence_share_floor)
    blocking_failures = [gate for gate in gates if gate.severity == "block" and gate.passed is False]
    unknown = [gate for gate in gates if gate.severity == "block" and gate.passed is None]

    if any(gate.gate in ('G1_maturity', 'G1_publication_prevalence') for gate in blocking_failures):
        status = "widespread" if config.version == '0.4' else 'mature'
    elif blocking_failures:
        status = "candidate"
    elif unknown:
        status = "watch"
    else:
        status = "forming"

    score, missing = _score(metrics, config)
    confidence = _confidence(metrics, config)
    if status == 'forming' and (missing or confidence < config.raw['confidence']['confirmed_signal_min']):
        status = 'watch'
    if status != "forming":
        score = None
    return Assessment(
        status=status,
        emergence_score=score,
        evidence_confidence=confidence,
        gates=gates,
        missing_metrics=missing,
        applied_penalties=dict(metrics.penalties),
    )
