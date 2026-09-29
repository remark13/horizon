"""Загрузка и валидация паспорта методики.

Принцип: ни один порог и ни один вес не живёт в коде. Всё приходит отсюда,
и каждый прогон записывает, какой именно версией конфигурации он посчитан.

Хеш конфигурации — часть provenance. Если кто-то поменял порог и не сказал,
хеш это покажет: два прогона с одинаковым as_of_date, но разными хешами
методики несопоставимы.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

WEIGHT_SUM_TOLERANCE = 1e-6

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "methodology.v0.4.yaml"

# События родословной, которые КОД умеет порождать. Паспорт не может обещать
# больше, чем реализовано: до ревизии он объявлял merge, которого не было,
# и это выглядело как работающая функция.
IMPLEMENTED_LINEAGE_EVENTS = {"split", "merge", "new_core"}

ALLOWED_DECAY_SHAPES = {"exponential", "gaussian", "none"}


class MethodologyError(ValueError):
    """Паспорт методики некорректен. Запуск с таким конфигом запрещён."""


@dataclass(frozen=True)
class Methodology:
    """Разрешённая конфигурация методики для одного прогона."""

    version: str
    raw: dict[str, Any]
    config_hash: str
    path: Path

    # --- активные наборы -------------------------------------------------

    @property
    def gates(self) -> dict[str, Any]:
        name = self.raw["gates"]["active"]
        return self.raw["gates"]["configurations"][name]

    @property
    def gates_name(self) -> str:
        return self.raw["gates"]["active"]

    @property
    def score_weights(self) -> dict[str, float]:
        name = self.raw["scoring"]["active"]
        return self.raw["scoring"]["configurations"][name]["weights"]

    @property
    def score_name(self) -> str:
        return self.raw["scoring"]["active"]

    @property
    def penalties(self) -> dict[str, float]:
        return self.raw["scoring"].get("penalties", {})

    @property
    def confidence_weights(self) -> dict[str, float]:
        """Веса того этапа, на котором мы сейчас находимся.

        До экспертной проверки согласие эксперта неизвестно и не может
        входить в уверенность, которую эксперт увидит перед вердиктом.
        """
        stage = self.confidence_stage
        return self.raw["confidence"][f"weights_{stage}"]

    @property
    def window_step(self) -> str:
        return self.raw["windows"]["step"]

    @property
    def lifecycle(self) -> dict[str, Any]:
        return self.raw["topic_lifecycle"]

    @property
    def clustering(self) -> dict[str, Any]:
        return self.raw["clustering"]

    @property
    def min_topic_size(self) -> int:
        clustering = self.clustering
        return int(clustering["scales"][clustering["active_scale"]]["min_topic_size"])

    @property
    def link_threshold(self) -> float:
        """Единственный порог связывания тем между окнами."""
        return float(self.lifecycle["linking"]["similarity_threshold"])

    @property
    def decay(self) -> dict[str, Any]:
        return self.lifecycle["popularity"]["decay"]

    @property
    def confidence_stage(self) -> str:
        return self.raw["confidence"].get("stage", "pre_review")

    def imprecise_date_policy(self, source: str) -> bool:
        """Считается ли 1 января заглушкой для этого источника."""
        by_source = self.raw["as_of_discipline"]["imprecise_dates"]["by_source"]
        return bool(by_source.get(source, {}).get("january_first_is_placeholder", False))

    @property
    def top_n(self) -> int:
        return self.raw["output"]["top_n"]

    # --- provenance ------------------------------------------------------

    def provenance(self) -> dict[str, Any]:
        """То, что записывается в каждый прогон рядом с as_of_date."""
        return {
            "methodology_version": self.version,
            "methodology_hash": self.config_hash,
            "gates_configuration": self.gates_name,
            "scoring_configuration": self.score_name,
            "window_step": self.window_step,
        }


def _hash_config(data: dict[str, Any]) -> str:
    """Стабильный хеш: порядок ключей не влияет, комментарии YAML не влияют."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MethodologyError(message)


def _validate_active_reference(data: dict[str, Any], section: str) -> None:
    _require(section in data, f"отсутствует секция '{section}'")
    block = data[section]
    _require("active" in block, f"'{section}.active' не задан")
    _require("configurations" in block, f"'{section}.configurations' не задан")
    active = block["active"]
    available = sorted(block["configurations"])
    _require(
        active in block["configurations"],
        f"'{section}.active' = '{active}', но такой конфигурации нет. Доступны: {available}",
    )


def _validate_weights(data: dict[str, Any]) -> None:
    for name, cfg in data["scoring"]["configurations"].items():
        weights = cfg.get("weights")
        _require(isinstance(weights, dict) and weights, f"scoring.{name}: веса не заданы")
        for key, value in weights.items():
            _require(
                isinstance(value, (int, float)) and 0.0 <= value <= 1.0,
                f"scoring.{name}.{key}: вес {value!r} вне диапазона [0, 1]",
            )
        total = sum(weights.values())
        _require(
            abs(total - 1.0) < WEIGHT_SUM_TOLERANCE,
            f"scoring.{name}: сумма весов {total:.6f}, должна быть 1.0",
        )

    confidence = data["confidence"]
    _require(
        "weights" not in confidence,
        "confidence.weights удалён при исправлении дефекта P1-8: наборов теперь два, "
        "weights_pre_review и weights_post_review. Единый набор включал expert_agreement, "
        "который до вердикта эксперта неизвестен.",
    )
    stage = confidence.get("stage")
    _require(
        stage in ("pre_review", "post_review"),
        f"confidence.stage = {stage!r}, допустимо pre_review или post_review",
    )
    for key in ("weights_pre_review", "weights_post_review"):
        _require(key in confidence, f"confidence.{key} не задан")
        total = sum(confidence[key].values())
        _require(
            abs(total - 1.0) < WEIGHT_SUM_TOLERANCE,
            f"confidence.{key}: сумма весов {total:.6f}, должна быть 1.0",
        )
    _require(
        "expert_agreement" not in confidence["weights_pre_review"],
        "confidence.weights_pre_review не может содержать expert_agreement: "
        "до ревью его значение неизвестно, и уверенность стала бы круговой",
    )


def _validate_percentiles(data: dict[str, Any]) -> None:
    for name, cfg in data["gates"]["configurations"].items():
        for key, value in cfg.items():
            if not key.endswith("_percentile_min") and not key.endswith("_percentile_max"):
                continue
            _require(
                isinstance(value, (int, float)) and 0 <= value <= 100,
                f"gates.{name}.{key}: процентиль {value!r} вне диапазона [0, 100]",
            )


def _validate_statuses(data: dict[str, Any]) -> None:
    values = data["statuses"]["values"]
    _require(isinstance(values, list) and values, "statuses.values пуст")
    for status in data["statuses"].get("management_view", {}):
        _require(
            status in values,
            f"statuses.management_view ссылается на неизвестный статус '{status}'",
        )


def _validate_as_of_discipline(data: dict[str, Any]) -> None:
    """Эти флаги — не настройки удобства. Выключенный флаг означает утечку будущего."""
    discipline = data.get("as_of_discipline", {})
    for flag in ("forbid_modern_terminology", "missing_is_not_zero"):
        _require(
            discipline.get(flag) is True,
            f"as_of_discipline.{flag} должен быть true: это защита от утечки будущего, а не опция",
        )
    excluded = set(discipline.get("exclude_after_cutoff", []))
    required = {"publications", "versions", "citations"}
    missing = required - excluded
    _require(
        not missing,
        f"as_of_discipline.exclude_after_cutoff не покрывает {sorted(missing)}",
    )


def _validate_citation_velocity(data: dict[str, Any]) -> None:
    """KISTI: задержка цитирования измеряется годами, поэтому признак не может решать судьбу кандидата."""
    cv = data.get("features", {}).get("citation_velocity", {})
    _require(
        cv.get("excluded_from_gates") is True,
        "features.citation_velocity.excluded_from_gates должен быть true: "
        "цитирования запаздывают на годы и не могут работать шлюзом",
    )


def _validate_decay(data: dict[str, Any]) -> None:
    """Дефект P0-1: единица времени должна быть объявлена, а не подразумеваться.

    Прежняя форма exp(-lambda * dt^2) с lambda 0.01 означала разное на шкале
    дней и кварталов — 6.6e-36 против 0.85 за год. Расхождение на тридцать
    пять порядков не ловилось ничем, потому что единица нигде не называлась.
    """
    popularity = data["topic_lifecycle"]["popularity"]
    _require(
        "lambda" not in popularity,
        "topic_lifecycle.popularity.lambda удалена при исправлении дефекта P0-1. "
        "Задавайте decay.half_life_days: смысл периода полураспада проверяем, "
        "смысл безымянной lambda — нет.",
    )
    _require("decay" in popularity, "topic_lifecycle.popularity.decay не задан")
    decay = popularity["decay"]

    _require(
        decay.get("time_unit") == "days",
        f"decay.time_unit = {decay.get('time_unit')!r}; код считает молчание в днях, "
        "и единица обязана быть объявлена явно",
    )
    shape = decay.get("shape")
    _require(
        shape in ALLOWED_DECAY_SHAPES,
        f"decay.shape = {shape!r}, допустимо {sorted(ALLOWED_DECAY_SHAPES)}",
    )
    if shape != "none":
        half_life = decay.get("half_life_days")
        _require(
            isinstance(half_life, (int, float)) and half_life > 0,
            f"decay.half_life_days = {half_life!r}, требуется положительное число дней",
        )


def _validate_lineage(data: dict[str, Any]) -> None:
    """Дефект P0-5: один смысл — один ключ. И обещать можно только реализованное."""
    lineage = data["windows"]["lineage"]
    _require(
        "similarity_threshold" not in lineage,
        "windows.lineage.similarity_threshold возвращён в паспорт. Этот ключ не читается "
        "никаким кодом и противоречит действующему topic_lifecycle.linking.similarity_threshold. "
        "Порог связывания должен быть ровно один.",
    )
    threshold = data["topic_lifecycle"]["linking"].get("similarity_threshold")
    _require(
        isinstance(threshold, (int, float)) and 0.0 < threshold <= 1.0,
        f"topic_lifecycle.linking.similarity_threshold = {threshold!r}, ожидается (0, 1]",
    )
    declared = set(lineage.get("events", []))
    unimplemented = declared - IMPLEMENTED_LINEAGE_EVENTS
    _require(
        not unimplemented,
        f"windows.lineage.events обещает события, которых код не порождает: "
        f"{sorted(unimplemented)}. Паспорт не может объявлять нереализованное.",
    )


def _validate_clustering(data: dict[str, Any]) -> None:
    """Дефект P0-5: параметры кластеризации жили в коде, а не в паспорте."""
    _require("clustering" in data, "секция clustering отсутствует")
    clustering = data["clustering"]
    for key in ("seed", "scales", "active_scale", "umap", "hdbscan"):
        _require(key in clustering, f"clustering.{key} не задан")
    if "global_hdbscan_selection_method" in clustering:
        _require(
            clustering["global_hdbscan_selection_method"] in {"eom", "leaf"},
            "clustering.global_hdbscan_selection_method должен быть eom или leaf",
        )
    active = clustering["active_scale"]
    _require(
        active in clustering["scales"],
        f"clustering.active_scale = {active!r}, но такого масштаба нет: "
        f"{sorted(clustering['scales'])}",
    )
    for name, scale in clustering["scales"].items():
        size = scale.get("min_topic_size")
        _require(
            isinstance(size, int) and size >= 2,
            f"clustering.scales.{name}.min_topic_size = {size!r}, ожидается целое >= 2",
        )


def _validate_imprecise_dates(data: dict[str, Any]) -> None:
    """Дефект P1-6: точность даты — свойство источника, а не строки."""
    imprecise = data["as_of_discipline"]["imprecise_dates"]
    _require(
        imprecise.get("detect") == "source_declared_precision",
        "as_of_discipline.imprecise_dates.detect должен быть source_declared_precision: "
        "правило «1 января = заглушка» без разбора источника сдвигало настоящие "
        "даты подачи arXiv на 364 дня вперёд",
    )
    _require(
        isinstance(imprecise.get("by_source"), dict) and imprecise["by_source"],
        "as_of_discipline.imprecise_dates.by_source не задан",
    )


def load(path: str | Path) -> Methodology:
    """Загрузить и проверить паспорт методики.

    Все проверки жёсткие: при некорректном паспорте прогон не стартует —
    вместо того чтобы молча подставить значение по умолчанию.
    """
    path = Path(path)
    if not path.exists():
        raise MethodologyError(f"паспорт методики не найден: {path}")

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    return from_mapping(data, path)


def from_mapping(data: dict[str, Any], path: str | Path = '<saved-methodology>') -> Methodology:
    """Validate an embedded historical passport without reading today's defaults."""
    from copy import deepcopy
    path = Path(path)
    data = deepcopy(data)

    _require(isinstance(data, dict), f"{path}: ожидался YAML-объект")
    _require("version" in data, "поле 'version' обязательно")

    _validate_active_reference(data, "gates")
    _validate_active_reference(data, "scoring")
    _validate_weights(data)
    _validate_percentiles(data)
    _validate_statuses(data)
    _validate_as_of_discipline(data)
    _validate_citation_velocity(data)
    _validate_decay(data)
    _validate_lineage(data)
    _validate_clustering(data)
    _validate_imprecise_dates(data)

    return Methodology(
        version=str(data["version"]),
        raw=data,
        config_hash=_hash_config(data),
        path=path,
    )


def load_default() -> Methodology:
    """Загрузить паспорт по умолчанию.

    Единственная разрешённая точка входа для модулей конвейера. До ревизии
    cluster.py и probe.py читали YAML напрямую через yaml.safe_load, и все
    проверки выше их не касались — то есть не касались ровно тех двух
    модулей, которые считают результат.
    """
    return load(DEFAULT_CONFIG_PATH)


def describe(methodology: Methodology) -> str:
    """Человекочитаемая сводка активной конфигурации."""
    gates = methodology.gates
    lines = [
        f"Паспорт методики v{methodology.version}  (hash {methodology.config_hash})",
        f"  файл:            {methodology.path}",
        f"  шаг окна:        {methodology.window_step}",
        f"  выдача:          TOP-{methodology.top_n}"
        + (", с диверсификацией MMR" if methodology.raw["output"]["diversification"]["enabled"] else ""),
        "",
        f"Шлюзы: конфигурация '{methodology.gates_name}'",
    ]
    for key, value in gates.items():
        if key == "note":
            continue
        lines.append(f"  {key:<38} {value}")

    lines += ["", f"Score: конфигурация '{methodology.score_name}'"]
    for key, value in sorted(methodology.score_weights.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {key:<38} {value}")
    lines.append(f"  {'СУММА':<38} {sum(methodology.score_weights.values()):.2f}")

    lines += ["", "Штрафы"]
    for key, value in sorted(methodology.penalties.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {key:<38} -{value}")

    alt_gates = [n for n in methodology.raw["gates"]["configurations"] if n != methodology.gates_name]
    alt_score = [n for n in methodology.raw["scoring"]["configurations"] if n != methodology.score_name]
    lines += [
        "",
        f"Альтернативные конфигурации шлюзов:  {', '.join(alt_gates) or '—'}",
        f"Альтернативные конфигурации score:   {', '.join(alt_score) or '—'}",
    ]
    return "\n".join(lines)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Показать активную конфигурацию методики Horizon")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="путь к паспорту методики",
    )
    args = parser.parse_args()
    print(describe(load(args.config)))


if __name__ == "__main__":
    main()
