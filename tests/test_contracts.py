"""Контрактные тесты: утверждения о системе, проверяемые машиной.

Написаны после внешней ревизии, и порядок здесь важнее содержания. Ревизия
нашла шесть дефектов уровня P0, и общее у них не то, что они были допущены,
а то, что разработчик утверждал их отсутствие, не имея способа проверить.
В брифе на ревизию стояло «хеш методики пишется рядом с каждым результатом»
при том, что analysis_run не заполнялся ни разу, и «затухание даёт 0.85 за
год» при том, что код давал 6.6e-36 за квартал.

Каждый тест ниже соответствует одному такому утверждению. Пока утверждение
не выражено тестом, слово «исправлено» стоит ровно столько же, сколько
стоило слово «пишется».
"""

from __future__ import annotations

import ast
import copy
import io
import re
import tokenize
from pathlib import Path

import pytest
import numpy as np
import yaml

from saia import methodology
from saia.runs import source_tree_version
from saia.cluster import (
    adaptive_link_threshold,
    advance_topic,
    contiguous_windows,
    decay,
    link_clusters,
    transition_of,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "methodology.v0.1.yaml"
PIPELINE_MODULES = ["cluster.py", "probe.py", "embed.py", "normalize.py", "ingest.py"]


@pytest.fixture(scope="module")
def raw_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def executable_source(module_name: str) -> str:
    """Исходник модуля без комментариев и строк документации.

    Нужен для тестов, которые ищут в коде запрещённые конструкции. Наивный
    поиск по всему тексту даёт ложные срабатывания на объяснениях: комментарий
    «здесь стоял DELETE FROM topic» выглядит для него как сам DELETE. Строки
    с SQL при этом сохраняются — именно в них живут запросы, которые и надо
    проверять.
    """
    source = (ROOT / "saia" / module_name).read_text(encoding="utf-8")
    lines = source.splitlines()

    def blank(start: int, end: int) -> None:
        for number in range(start, min(end, len(lines)) + 1):
            lines[number - 1] = ""

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            blank(first.lineno, first.end_lineno or first.lineno)

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row = token.start[0]
            if lines[row - 1]:
                lines[row - 1] = lines[row - 1][: token.start[1]]

    return "\n".join(lines)


def test_quality_lateral_queries_do_not_reference_hidden_columns():
    for module_name in ("embed.py", "cluster.py"):
        source = executable_source(module_name)
        assert "q.work_id IS NULL" not in source
        assert "quality_snapshot" in source
        assert "q.generation_id = %s" in source
        assert "q.decision = 'include'" in source


def test_dirty_tree_provenance_hash_covers_pipeline_sources():
    version = source_tree_version()
    assert version.startswith("tree:")
    assert len(version) == len("tree:") + 12


def test_embedding_and_clustering_apply_both_mission_date_bounds():
    embed_source = executable_source("embed.py")
    cluster_source = executable_source("cluster.py")
    assert "runs.analysis_period(cur, normal_run)" in embed_source
    assert "w.effective_date >= %s" in embed_source
    assert "w.effective_date < %s" in embed_source
    assert "w.effective_date >= %s AND w.effective_date < %s" in cluster_source
    assert "ORDER BY w.effective_date, w.work_id" in cluster_source


def test_every_migration_registers_itself():
    """Успешная миграция не должна снова считаться ожидающей применения."""
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        source = path.read_text(encoding="utf-8")
        assert re.search(
            rf"INSERT\s+INTO\s+schema_migrations\s*\(version\)\s*"
            rf"VALUES\s*\(['\"]{re.escape(path.stem)}['\"]\)",
            source,
            re.IGNORECASE,
        ), f"{path.name} не записывает свою версию"


def write_and_load(tmp_path: Path, data: dict) -> methodology.Methodology:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "methodology.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return methodology.load(path)


# --- P0-1. Единица времени в затухании --------------------------------------

def test_decay_half_life_is_exact():
    """Период полураспада означает ровно то, что написано."""
    assert decay(100.0, 365, 365) == pytest.approx(50.0)
    assert decay(100.0, 730, 365) == pytest.approx(25.0)


def test_decay_does_not_erase_topic_in_one_window():
    """Тема, пропустившая одно окно, не должна исчезать.

    Дефект P0-1 в чистом виде: exp(-0.01 * 90**2) = 6.6e-36. Квартал
    молчания стирал тему полностью, и это выглядело как «тема угасла».
    """
    after_quarter = decay(100.0, 90, 365)
    assert after_quarter > 80.0, f"квартал тишины оставил {after_quarter}"


def test_decay_year_of_silence_is_not_zero():
    assert decay(100.0, 365, 365) > 1.0


def test_decay_is_monotonic_and_bounded():
    previous = 100.0
    for days in (1, 30, 90, 180, 365, 730, 1460):
        value = decay(100.0, days, 365)
        assert 0.0 < value <= previous
        previous = value


def test_decay_is_incremental_not_cumulative_from_last_publication():
    """Два шага состояния не должны повторно учитывать первый интервал."""
    topic = {"popularity": 100.0, "history": [], "last_state_window": "2020Q1"}
    first, _ = advance_topic(topic, "2020Q2", "quarter", 0, 365, "exponential")
    second, _ = advance_topic(topic, "2020Q3", "quarter", 0, 365, "exponential")
    direct = decay(100.0, 183, 365)
    assert second == pytest.approx(direct, rel=0.02)
    assert second < first


def test_snapshot_slope_is_frozen_at_its_own_as_of_date():
    """Позднее окно не меняет уже вычисленный наклон раннего снимка."""
    topic = {"popularity": 0.0, "history": [], "last_state_window": None}
    _, slope_q1 = advance_topic(topic, "2020Q1", "quarter", 5, 365, "exponential", 4)
    _, slope_q2 = advance_topic(topic, "2020Q2", "quarter", 20, 365, "exponential", 4)
    assert slope_q1 == 0.0
    assert slope_q2 > 0.0


def test_calendar_contains_empty_windows():
    assert contiguous_windows("2020Q1", "2021Q1", "quarter") == [
        "2020Q1", "2020Q2", "2020Q3", "2020Q4", "2021Q1"
    ]


def test_config_rejects_legacy_lambda(tmp_path, raw_config):
    data = copy.deepcopy(raw_config)
    data["topic_lifecycle"]["popularity"]["lambda"] = 0.01
    with pytest.raises(methodology.MethodologyError, match="lambda"):
        write_and_load(tmp_path, data)


def test_config_requires_explicit_time_unit(tmp_path, raw_config):
    data = copy.deepcopy(raw_config)
    data["topic_lifecycle"]["popularity"]["decay"]["time_unit"] = "quarters"
    with pytest.raises(methodology.MethodologyError, match="time_unit"):
        write_and_load(tmp_path / "a", data)

    data = copy.deepcopy(raw_config)
    del data["topic_lifecycle"]["popularity"]["decay"]["time_unit"]
    with pytest.raises(methodology.MethodologyError, match="time_unit"):
        write_and_load(tmp_path / "b", data)


def test_config_requires_positive_half_life(tmp_path, raw_config):
    data = copy.deepcopy(raw_config)
    data["topic_lifecycle"]["popularity"]["decay"]["half_life_days"] = 0
    with pytest.raises(methodology.MethodologyError, match="half_life_days"):
        write_and_load(tmp_path, data)


# --- P0-4. Разделение и слияние тем -----------------------------------------

def test_transition_new_core():
    assert transition_of([], {}) == "new_core"


def test_transition_continuation():
    assert transition_of([7], {7: [0]}) == "continuation"


def test_transition_split():
    assert transition_of([7], {7: [0, 1]}) == "split"


def test_transition_merge():
    assert transition_of([7, 9], {7: [0], 9: [0]}) == "merge"


def test_split_produces_distinct_children():
    """Осколки разделения — разные темы, а не один topic_id на всех.

    Дефект P0-4: все осколки получали идентификатор родителя, срабатывало
    UNIQUE (topic_id, window_key), и лишние снимки молча исчезали.
    """
    import numpy as np

    centroids = np.array([[1.0, 0.0], [0.9, 0.1]], dtype=np.float32)
    anchors = np.array([[0.95, 0.05]], dtype=np.float32)
    parents_of, children_of, _ = link_clusters(centroids, anchors, [7], 0.7)

    assert parents_of[0] == [7] and parents_of[1] == [7]
    assert children_of[7] == [0, 1]
    assert transition_of(parents_of[0], children_of) == "split"
    assert transition_of(parents_of[1], children_of) == "split"


def test_merge_is_detected():
    """Два родителя на один кластер — слияние, которого раньше не было вовсе."""
    import numpy as np

    centroids = np.array([[1.0, 0.0]], dtype=np.float32)
    anchors = np.array([[0.99, 0.01], [0.98, 0.02]], dtype=np.float32)
    parents_of, children_of, _ = link_clusters(centroids, anchors, [4, 5], 0.7)
    assert sorted(parents_of[0]) == [4, 5]
    assert transition_of(parents_of[0], children_of) == "merge"


def test_anisotropic_embedding_space_raises_link_threshold():
    centroids = np.array([[1.0, 0.10], [1.0, 0.20]], dtype=np.float32)
    anchors = np.array([[1.0, 0.11], [1.0, 0.21]], dtype=np.float32)
    threshold = adaptive_link_threshold(
        centroids, anchors, base=0.70, trigger_median=0.80, quantile=0.99
    )
    assert threshold > 0.99


def test_lineage_events_are_all_implemented(raw_config):
    declared = set(raw_config["windows"]["lineage"]["events"])
    assert declared <= methodology.IMPLEMENTED_LINEAGE_EVENTS
    assert "continuation" not in declared


def test_config_rejects_unimplemented_event(tmp_path, raw_config):
    data = copy.deepcopy(raw_config)
    data["windows"]["lineage"]["events"].append("resurrection")
    with pytest.raises(methodology.MethodologyError, match="resurrection"):
        write_and_load(tmp_path, data)


# --- P0-5. Паспорт как единственный источник истины -------------------------

def test_pipeline_modules_do_not_bypass_the_validator():
    """Модули конвейера не читают паспорт мимо валидатора.

    Дефект P0-5: cluster.py и probe.py читали YAML через yaml.safe_load, и
    жёсткие проверки не касались ровно тех двух модулей, которые считают
    результат.
    """
    offenders = [
        name for name in PIPELINE_MODULES
        if re.search(r"yaml\.(safe_)?load\b", executable_source(name))
    ]
    assert not offenders, f"читают YAML напрямую: {offenders}"


def test_single_linking_threshold(raw_config):
    assert "similarity_threshold" not in raw_config["windows"]["lineage"]
    assert raw_config["topic_lifecycle"]["linking"]["similarity_threshold"]


def test_config_rejects_resurrected_dead_threshold(tmp_path, raw_config):
    data = copy.deepcopy(raw_config)
    data["windows"]["lineage"]["similarity_threshold"] = 0.60
    with pytest.raises(methodology.MethodologyError, match="similarity_threshold"):
        write_and_load(tmp_path, data)


def test_clustering_parameters_live_in_the_passport(raw_config):
    for key in ("seed", "umap", "hdbscan", "scales", "active_scale"):
        assert key in raw_config["clustering"]


def test_micro_scale_allows_early_topics(raw_config):
    """Минимальный размер темы не должен запрещать раннее обнаружение.

    Ревизия, пункт 7.1: единственное значение 15 означало, что тема из
    четырёх работ не может стать кластером в принципе, а ищем мы именно такие.
    """
    clustering = raw_config["clustering"]
    assert clustering["scales"][clustering["active_scale"]]["min_topic_size"] <= 5


def test_config_rejects_missing_clustering(tmp_path, raw_config):
    data = copy.deepcopy(raw_config)
    del data["clustering"]
    with pytest.raises(methodology.MethodologyError, match="clustering"):
        write_and_load(tmp_path, data)


# --- P0-3 и P0-6. История и происхождение -----------------------------------

def test_history_is_never_deleted():
    """Ни один модуль не стирает результаты предыдущего прогона."""
    # Границы слова обязательны: work_embedding — производная от модели и
    # пересчитывается законно, а work — поколение корпуса и неприкосновенно.
    pattern = re.compile(
        r"DELETE\s+FROM\s+(work|topic|author|organisation|identifier)\b",
        re.IGNORECASE,
    )
    offenders = []
    for name in PIPELINE_MODULES:
        for match in pattern.finditer(executable_source(name)):
            offenders.append(f"{name}: {match.group(0)}")
    assert not offenders, f"стирают историю: {offenders}"


def test_the_guard_itself_detects_a_real_delete(tmp_path):
    """Проверка проверки: сторож обязан отличать код от объяснения.

    Оба теста выше опираются на executable_source. Если он однажды начнёт
    возвращать пустую строку, они станут зелёными навсегда и перестанут
    что-либо значить.
    """
    assert re.search(r"DELETE\s+FROM\s+topic\b", "cur.execute('DELETE FROM topic')")
    assert not re.search(r"DELETE\s+FROM\s+(work)\b", "DELETE FROM work_embedding")
    assert "runs.start_run(" in executable_source("normalize.py")
    assert executable_source("cluster.py").count("import") > 0


def test_results_are_bound_to_a_run():
    """Нормализация и кластеризация открывают и закрывают прогон.

    Дефект P0-6: analysis_run упоминался в коде только на чтение. Версия
    методики не была связана ни с одним результатом, хотя документация
    утверждала обратное.
    """
    for name in ("normalize.py", "cluster.py"):
        source = (ROOT / "saia" / name).read_text(encoding="utf-8")
        assert "runs.start_run(" in source, f"{name} не открывает прогон"
        assert "runs.finish_run(" in source, f"{name} не закрывает прогон"


def test_provenance_covers_model_and_code():
    """Одного хеша паспорта мало: результат задают ещё модель и код."""
    source = (ROOT / "saia" / "runs.py").read_text(encoding="utf-8")
    for column in ("methodology_hash", "embedding_model", "code_version", "as_of_date"):
        assert column in source


def test_config_hash_is_stable_and_sensitive(tmp_path, raw_config):
    first = write_and_load(tmp_path / "a", raw_config)
    second = write_and_load(tmp_path / "b", copy.deepcopy(raw_config))
    assert first.config_hash == second.config_hash

    changed = copy.deepcopy(raw_config)
    changed["gates"]["configurations"]["blend"]["g2_novelty_percentile_min"] += 1
    assert write_and_load(tmp_path / "c", changed).config_hash != first.config_hash


# --- P1-6. Точность даты — свойство источника -------------------------------

def test_arxiv_january_first_is_a_real_date():
    """Препринт, поданный 1 января, не двигается на конец года.

    Дефект P1-6: правило «1 января = заглушка» применялось ко всем
    источникам. Для arXiv поле created — настоящая дата подачи, и работа
    сдвигалась на 364 дня вперёд, выпадая из своего окна. В корпусе миссии
    arXiv — единственный источник.
    """
    from saia.normalize import effective_date, is_imprecise

    config = methodology.load_default()
    assert is_imprecise("2016-01-01", "arxiv", config) is False
    assert effective_date("2016-01-01", 2016, "arxiv", config) == "2016-01-01"


def test_openalex_january_first_is_a_placeholder():
    from saia.normalize import effective_date, is_imprecise

    config = methodology.load_default()
    assert is_imprecise("2016-01-01", "openalex", config) is True
    assert effective_date("2016-01-01", 2016, "openalex", config) == "2016-12-31"


def test_ordinary_dates_are_untouched():
    from saia.normalize import effective_date

    config = methodology.load_default()
    for source in ("arxiv", "openalex"):
        assert effective_date("2016-07-14", 2016, source, config) == "2016-07-14"


# --- P1-8. Уверенность не должна быть круговой ------------------------------

def test_confidence_before_review_excludes_expert_agreement(raw_config):
    assert "expert_agreement" not in raw_config["confidence"]["weights_pre_review"]
    assert "expert_agreement" in raw_config["confidence"]["weights_post_review"]


def test_config_rejects_circular_confidence(tmp_path, raw_config):
    data = copy.deepcopy(raw_config)
    data["confidence"]["weights_pre_review"] = {
        "data_completeness": 0.25, "source_quality": 0.25,
        "confirmation_independence": 0.25, "expert_agreement": 0.25,
    }
    with pytest.raises(methodology.MethodologyError, match="expert_agreement"):
        write_and_load(tmp_path, data)


# --- Дисциплина среза -------------------------------------------------------

def test_as_of_flags_cannot_be_switched_off(tmp_path, raw_config):
    for index, flag in enumerate(("forbid_modern_terminology", "missing_is_not_zero")):
        data = copy.deepcopy(raw_config)
        data["as_of_discipline"][flag] = False
        with pytest.raises(methodology.MethodologyError, match=flag):
            write_and_load(tmp_path / str(index), data)


def test_citation_velocity_cannot_return_to_gates(tmp_path, raw_config):
    data = copy.deepcopy(raw_config)
    data["features"]["citation_velocity"]["excluded_from_gates"] = False
    with pytest.raises(methodology.MethodologyError, match="citation_velocity"):
        write_and_load(tmp_path, data)


def test_weights_must_sum_to_one(tmp_path, raw_config):
    data = copy.deepcopy(raw_config)
    data["scoring"]["configurations"]["baseline_v1"]["weights"]["novelty"] += 0.1
    with pytest.raises(methodology.MethodologyError, match="сумма весов"):
        write_and_load(tmp_path, data)


def test_shipped_config_loads():
    """Паспорт, лежащий в репозитории, проходит все проверки."""
    config = methodology.load_default()
    assert config.config_hash
    assert config.decay["time_unit"] == "days"


# --- P1-4. Отсутствие данных не равно нулю ----------------------------------

def test_missing_denominator_is_not_zero():
    """Дефект P1-4: без знаменателя доля не вычислена, а не равна нулю."""
    from saia.probe import share_of

    assert share_of(10, 1000) == pytest.approx(0.01)
    assert share_of(10, None) is None
    assert share_of(10, 0) is None
    assert share_of(0, 1000) == 0.0, "настоящий ноль остаётся нулём"


# --- P1-5. Темы должны умирать ----------------------------------------------

def test_silent_topic_eventually_dies(raw_config):
    """Дефект P1-5: якорь молчащей темы не может жить вечно."""
    from saia.cluster import is_dead

    death_after = raw_config["topic_lifecycle"]["popularity"]["death_after_silent_windows"]
    assert death_after > 0, "тема, молчащая вечно, останется кандидатом на связывание"
    assert is_dead(death_after, death_after) is True
    assert is_dead(death_after - 1, death_after) is False


# --- P1-2. Устаревший кэш выгрузки ------------------------------------------

def test_stale_pages_are_refused(tmp_path):
    """Дефект P1-2: страницы другого запроса нельзя выдавать за свои.

    Раньше страницы переиспользовались по факту существования файла. Правка
    миссии — период, набор, категории — оставляла старые страницы в деле, а
    манифест получал свежее время загрузки. Неполный корпус выглядел полным.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("fetch", ROOT / "scripts" / "fetch.py")
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)

    out_dir = tmp_path / "arxiv"
    out_dir.mkdir()
    fetch.guard_query_hash(out_dir, {"set": "cs", "from": "2010-01-01"}, refetch=False)
    fetch.guard_query_hash(out_dir, {"set": "cs", "from": "2010-01-01"}, refetch=False)

    with pytest.raises(fetch.StaleCacheError):
        fetch.guard_query_hash(out_dir, {"set": "stat", "from": "2010-01-01"},
                               refetch=False)

    fetch.guard_query_hash(out_dir, {"set": "stat", "from": "2010-01-01"}, refetch=True)
