"""Тематическая кластеризация по окнам, связывание тем и жизненный цикл.

    python -m saia.cluster ml-area-2017
    python -m saia.cluster ml-area-2017 --step year --min-topic-size 20

Это тот кусок, без которого воронка пропускала всё. На корпусе из одной
темы новизна и рост были невычислимы: первая определена как расстояние до
кластеров прошлого, второй — как перцентиль среди соседних тем. Здесь
появляются и кластеры, и соседи.

Механика жизненного цикла взята у BERTrend (RTE, ACL FuturED 2024):
накопленная популярность с затуханием при молчании темы, классификация по
скользящим перцентилям и якорный эмбеддинг первого появления. Заимствуется
способ измерения, не определение сигнала — их слабый сигнал это средняя
популярность плюс рост, без проверки независимости и новизны.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

import numpy as np

from saia import db, methodology, runs


# ---------------------------------------------------------------------------
# Окна
# ---------------------------------------------------------------------------

def window_of(day: date, step: str) -> str:
    if step == "month":
        return f"{day.year:04d}-{day.month:02d}"
    if step not in ("year", "quarter"):
        raise ValueError("Unknown calendar window step")
    return f"{day.year}" if step == "year" else f"{day.year}Q{(day.month - 1) // 3 + 1}"


def window_bounds(key: str, step: str) -> tuple[date, date]:
    if step == "month":
        if not re.fullmatch(r"\d{4}-\d{2}", key):
            raise ValueError("Invalid monthly window key")
        year, month = map(int, key.split("-"))
        start = date(year, month, 1)
        end = date(year + (month == 12), month % 12 + 1, 1)
        return start, end - timedelta(days=1)
    if step == "year":
        year = int(key)
        return date(year, 1, 1), date(year, 12, 31)
    if step != "quarter":
        raise ValueError("Unknown calendar window step")
    year, quarter = int(key[:4]), int(key[-1])
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    end = date(year + 1, 1, 1) if quarter == 4 else date(year, start_month + 3, 1)
    return start, date.fromordinal(end.toordinal() - 1)


def window_midpoint(key: str, step: str) -> date:
    start, end = window_bounds(key, step)
    return date.fromordinal((start.toordinal() + end.toordinal()) // 2)


def next_window(key: str, step: str) -> str:
    """Следующее календарное окно, в том числе если в нём нет публикаций."""
    start, end = window_bounds(key, step)
    return window_of(end + timedelta(days=1), step)


def contiguous_windows(first: str, last: str, step: str) -> list[str]:
    """Полная календарная шкала без пропуска пустых окон."""
    if window_bounds(first, step)[0] > window_bounds(last, step)[0]:
        raise ValueError("Calendar windows are reversed")
    result = []
    current = first
    while True:
        result.append(current)
        if current == last:
            return result
        current = next_window(current, step)


# ---------------------------------------------------------------------------
# Кластеризация одного окна
# ---------------------------------------------------------------------------

def cluster_window(texts: list[str], vectors: np.ndarray, min_topic_size: int,
                   seed: int, umap_cfg: dict, hdbscan_cfg: dict,
                   small_window_similarity_threshold: float
                   ) -> tuple[np.ndarray, dict[int, list[str]]]:
    """Вернуть метки кластеров и верхние термины по каждому кластеру.

    Плотностная кластеризация, а не k-means: нужен класс «шум» как
    отдельный исход. k-means обязан разложить по кластерам всё, включая
    мусор выборки, и тогда мусор становится темой.
    """
    from bertopic import BERTopic
    from hdbscan import HDBSCAN
    from sklearn.feature_extraction.text import CountVectorizer
    from umap import UMAP

    if len(texts) < min_topic_size:
        return np.full(len(texts), -1), {}
    if len(texts) < max(4, min_topic_size + 2):
        # UMAP/HDBSCAN не определены устойчиво на двух-трёх объектах.
        # Не расширяем окно данными из будущего: используем для него
        # детерминированный граф сходства с порогом из паспорта методики.
        return graph_cluster_window(
            texts, vectors, min_topic_size, small_window_similarity_threshold
        )

    # random_state фиксируется: без него UMAP даёт разное разбиение на тех
    # же данных, и воспроизводимость прогона, которой требует методика,
    # превращается в фикцию.
    # Конфигурационное n_neighbors может быть больше раннего микроокна.
    # UMAP требует значение строго меньше числа объектов.
    effective_umap = dict(umap_cfg)
    effective_umap["n_neighbors"] = min(
        int(effective_umap.get("n_neighbors", 15)), max(2, len(texts) - 1)
    )
    configured_components = int(effective_umap.get("n_components", 5))
    effective_umap["n_components"] = min(
        configured_components, max(1, len(texts) - 2)
    )
    if len(texts) <= configured_components + 1:
        # Спектральная инициализация требует k < N и падает на ранних
        # окнах из 2–6 работ. Детерминированная random-init сохраняет такие
        # окна в тесте, не добавляя документов и не меняя порог кластера.
        effective_umap["init"] = "random"
    umap_model = UMAP(random_state=seed, **effective_umap)
    hdbscan_model = HDBSCAN(min_cluster_size=min_topic_size,
                            prediction_data=True, **hdbscan_cfg)
    # BERTopic применяет этот vectorizer не к исходным статьям, а к одному
    # агрегированному «документу» на кластер. При единственном кластере
    # min_df=2 математически недопустим и валит ранние микроокна — именно те,
    # которые нужны для weak-signal теста. Уникальные для одного кластера
    # термины здесь полезны, поэтому корректный минимум равен единице.
    vectorizer_model = CountVectorizer(
        stop_words="english", ngram_range=(1, 2), min_df=1
    )

    model = BERTopic(
        umap_model=umap_model,
        hdbscan_model=hdbscan_model,
        vectorizer_model=vectorizer_model,
        embedding_model=None,
        calculate_probabilities=False,
        verbose=False,
    )
    labels, _ = model.fit_transform(texts, embeddings=vectors)
    labels = np.asarray(labels)

    terms: dict[int, list[str]] = {}
    for label in set(labels.tolist()):
        if label == -1:
            continue
        words = model.get_topic(label) or []
        terms[label] = [w for w, _ in words[:8]]
    return labels, terms


_TERM = re.compile(r"[a-zа-яё][a-zа-яё0-9-]+", re.IGNORECASE)
_STOP = {
    "the", "and", "for", "with", "from", "this", "that", "are", "using",
    "based", "network", "networks", "neural", "graph", "graphs", "model",
    "models", "learning", "data", "method", "methods", "approach", "paper",
    "of", "to", "in", "is", "by", "on", "we", "a", "an", "as", "be",
    "our", "it", "can", "or", "which", "these", "their", "has", "have",
}


def _top_terms(texts: list[str], limit: int = 8) -> list[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        words = [w.casefold() for w in _TERM.findall(text) if w.casefold() not in _STOP]
        counts.update(words)
        counts.update(f"{a} {b}" for a, b in zip(words, words[1:]))
    return [term for term, _ in counts.most_common(limit)]


def graph_cluster_window(texts: list[str], vectors: np.ndarray,
                         min_topic_size: int, similarity_threshold: float
                         ) -> tuple[np.ndarray, dict[int, list[str]]]:
    """Детерминированная плотностная кластеризация графа сходства.

    Узел становится ядром, если у него есть как минимум
    ``min_topic_size`` близких документов вместе с ним самим. Компоненты
    ядер — темы, остальные документы остаются шумом. В отличие от k-means,
    алгоритм не обязан разложить по темам весь мусор.
    """
    n = len(texts)
    labels = np.full(n, -1, dtype=int)
    if n < min_topic_size:
        return labels, {}
    similarities = cosine_matrix(vectors, vectors)
    adjacent = similarities >= similarity_threshold
    core = adjacent.sum(axis=1) >= min_topic_size
    visited: set[int] = set()
    components: list[list[int]] = []
    for start in np.flatnonzero(core).tolist():
        if start in visited:
            continue
        stack, component = [start], []
        visited.add(start)
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbour in np.flatnonzero(adjacent[node] & core).tolist():
                if neighbour not in visited:
                    visited.add(neighbour)
                    stack.append(neighbour)
        if len(component) >= min_topic_size:
            components.append(sorted(component))

    # Пограничный документ присоединяется к наиболее похожему ядру только
    # если сам имеет ребро к этой теме; все остальные остаются -1 (шум).
    for label, component in enumerate(components):
        labels[component] = label
    for index in np.flatnonzero(~core).tolist():
        candidates = []
        for label, component in enumerate(components):
            links = similarities[index, component]
            if links.max(initial=-1.0) >= similarity_threshold:
                candidates.append((float(links.mean()), label))
        if candidates:
            labels[index] = max(candidates)[1]

    terms = {
        label: _top_terms([texts[i] for i in np.flatnonzero(labels == label).tolist()])
        for label in range(len(components))
    }
    return labels, terms


# ---------------------------------------------------------------------------
# Жизненный цикл
# ---------------------------------------------------------------------------

def decay(previous: float, days_silent: float, half_life_days: float,
          shape: str = "exponential") -> float:
    """Затухание популярности темы, переставшей пополняться.

    Единица времени — ДНИ, и это единственная допустимая единица; паспорт
    обязан объявить её явно, иначе загрузчик не стартует.

    Здесь был дефект P0-1. Стояло exp(-lambda * dt^2) при lambda 0.01, а
    dt приходило в днях: квартал молчания давал множитель 6.6e-36, год —
    ноль. Любая тема, пропустившая окно, стиралась. Документация описывала
    ту же формулу на шкале кварталов, где она даёт 0.85 за год, и никакая
    проверка расхождения не замечала, потому что единица нигде не звучала.

    Форма задаётся периодом полураспада: столько дней тишины — половина
    популярности. Это величина, которую можно назвать вслух и проверить.
    """
    if days_silent <= 0 or shape == "none":
        return previous
    ratio = days_silent / half_life_days
    if shape == "gaussian":
        ratio = ratio ** 2
    return previous * (0.5 ** ratio)


def link_clusters(centroids: np.ndarray, anchors: np.ndarray | None,
                  anchor_keys: list[int], threshold: float
                  ) -> tuple[dict[int, list[int]], dict[int, list[int]], dict]:
    """Двустороннее сопоставление кластеров окна с живыми темами.

    Возвращает (родители каждого кластера, дети каждой темы, сходства).
    Кластер может иметь несколько родителей, тема — несколько детей: без
    этого слияние и разделение неразличимы. Прежний код брал единственного
    лучшего родителя и терял оба события (дефект P0-4).
    """
    parents_of: dict[int, list[int]] = {i: [] for i in range(len(centroids))}
    children_of: dict[int, list[int]] = defaultdict(list)
    similarity_to: dict[tuple[int, int], float] = {}

    if anchors is None or not anchor_keys:
        return parents_of, children_of, similarity_to

    similarity = cosine_matrix(centroids, anchors)
    for i in range(len(centroids)):
        for a, key in enumerate(anchor_keys):
            if similarity[i][a] >= threshold:
                parents_of[i].append(key)
                children_of[key].append(i)
                similarity_to[(i, key)] = float(similarity[i][a])
    return parents_of, children_of, similarity_to


def adaptive_link_threshold(centroids: np.ndarray, anchors: np.ndarray | None,
                            base: float, trigger_median: float,
                            quantile: float) -> float:
    """Скорректировать абсолютный порог при анизотропии пространства.

    Порог выводится из всей матрицы перехода до выбора конкретного родителя,
    поэтому известный проверяемый тренд не участвует в калибровке.
    """
    if anchors is None or not len(centroids) or not len(anchors):
        return base
    similarities = cosine_matrix(centroids, anchors)
    if float(np.median(similarities)) < trigger_median:
        return base
    return max(base, float(np.quantile(similarities, quantile)))


def transition_of(parents: list[int], children_of: dict[int, list[int]]) -> str:
    """Что произошло с личностью темы в этом окне, с точки зрения кластера.

    Событий ровно четыре, и они взаимно исключающи:
      нет родителей                                   -> new_core
      несколько родителей                             -> merge
      один родитель, у которого несколько детей       -> split
      один родитель с единственным ребёнком           -> continuation

    continuation не порождает записи в родословной: продолжение — это
    отсутствие смены личности, оно выражено двумя последовательными
    снимками одного topic_id. Прежний код писал его ребром parent = child,
    которое не несёт информации.
    """
    if not parents:
        return "new_core"
    if len(parents) > 1:
        return "merge"
    return "split" if len(children_of[parents[0]]) > 1 else "continuation"


def is_dead(silent_windows: int, death_after: int) -> bool:
    """Пора ли снять тему с учёта как кандидата для связывания.

    Дефект P1-5: раньше якоря жили вечно, alive никогда не становился false,
    и новая тема могла приклеиться к линии, угасшей несколько лет назад.
    Ноль означает «не умирать никогда» и оставлен ради обратной совместимости
    конфигурации, но в паспорте задано положительное число.
    """
    return bool(death_after) and silent_windows >= death_after


def slope_of(series: list[float]) -> float:
    """Наклон линейной регрессии по последним значениям популярности."""
    n = len(series)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    y = np.asarray(series, dtype=float)
    x_mean, y_mean = x.mean(), y.mean()
    denominator = ((x - x_mean) ** 2).sum()
    return float(((x - x_mean) * (y - y_mean)).sum() / denominator) if denominator else 0.0


def advance_topic(topic: dict, window: str, step: str, added_docs: int,
                  half_life_days: float, decay_shape: str,
                  slope_window: int | None = None) -> tuple[float, float]:
    """Продвинуть состояние темы ровно на одно наблюдаемое окно.

    Затухание считается от предыдущего СОСТОЯНИЯ, а не снова от последней
    публикации. Поэтому два последовательных квартала молчания эквивалентны
    одному затуханию на полгода и не возводят прошедшее время в квадрат.
    Возвращаем значение и наклон именно на этом срезе; будущие окна в него
    попасть не могут.
    """
    previous_window = topic.get("last_state_window")
    value = float(topic.get("popularity", 0.0))
    if previous_window is not None and previous_window != window:
        elapsed = (
            window_midpoint(window, step) - window_midpoint(previous_window, step)
        ).days
        value = decay(value, max(elapsed, 1), half_life_days, decay_shape)
    value += added_docs
    topic["popularity"] = value
    topic["last_state_window"] = window
    topic["history"].append(value)
    history = topic["history"][-slope_window:] if slope_window else topic["history"]
    return value, slope_of(history)


def project_global_topics(items: list[tuple], labels: np.ndarray,
                          terms: dict[int, list[str]], windows: list[str], step: str,
                          half_life_days: float, decay_shape: str, slope_window: int,
                          death_after: int) -> tuple[dict, list[dict], list[tuple], int]:
    """Project fixed full-period semantic clusters onto calendar windows.

    ``items`` contain ``(work_id, text, vector, window)``. Fixed labels make
    current-period trajectories interpretable, but their definition uses the
    whole period and therefore must never be described as a historical as-of
    detector.
    """
    if len(items) != len(labels):
        raise ValueError("Global labels must match the item count")
    valid = sorted({int(label) for label in labels.tolist() if int(label) != -1})
    window_index = {window: index for index, window in enumerate(windows)}
    topics, snapshots, memberships = {}, [], []
    for key in valid:
        member_indexes = np.flatnonzero(labels == key).tolist()
        member_items = [items[index] for index in member_indexes]
        present = sorted({item[3] for item in member_items}, key=window_index.__getitem__)
        first_window, last_window = present[0], present[-1]
        first_vectors = np.vstack([item[2] for item in member_items if item[3] == first_window])
        anchor = first_vectors.mean(axis=0)
        state = {
            "topic_key": key, "anchor": anchor, "anchor_window": first_window,
            "first_window": first_window, "last_window": last_window,
            "alive": True, "terms": terms.get(key, []),
            "popularity": 0.0, "history": [],
        }
        topics[key] = state
        by_window = defaultdict(list)
        for work_id, _text, vector, window in member_items:
            by_window[window].append((work_id, vector))
            memberships.append((key, work_id, window))
        for window in windows[window_index[first_window]:]:
            members = by_window.get(window, [])
            popularity, current_slope = advance_topic(
                state, window, step, len(members), half_life_days,
                decay_shape, slope_window,
            )
            centroid = np.vstack([vector for _, vector in members]).mean(axis=0) if members else None
            drift = (1.0 - float(cosine_matrix(centroid[None, :], anchor[None, :])[0][0])
                     if centroid is not None else None)
            snapshots.append({
                "topic_key": key, "window": window, "doc_count": len(members),
                "popularity": popularity, "slope": current_slope,
                "centroid": centroid, "drift": drift,
            })
        silent = window_index[windows[-1]] - window_index[last_window]
        state["alive"] = not is_dead(silent, death_after)
        if not state["alive"]:
            state["died_in"] = windows[min(window_index[last_window] + death_after, len(windows) - 1)]
    return topics, snapshots, memberships, int((labels == -1).sum())


def classify(popularity: float, low: float, high: float, slope: float) -> str:
    """Классификация BERTrend по уровню популярности и знаку наклона.

    Это НЕ статус сигнала. Статус присваивает воронка после проверки
    независимости, новизны и связности. Здесь измеряется только положение
    темы в распределении и направление движения — то, что даёт вычислимый
    вход для шлюза роста.
    """
    if popularity < low:
        return "noise"
    if popularity > high:
        return "strong"
    return "weak" if slope > 0 else "noise"


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-12)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-12)
    return a_norm @ b_norm.T


def resolve_quality_input(cur, mission_id: str, generation_id: int | None):
    if generation_id is not None:
        if not isinstance(generation_id, int) or isinstance(generation_id, bool) or generation_id < 1:
            raise ValueError('Номер поколения качества должен быть положительным целым.')
        cur.execute('SELECT q.normalize_run_id,q.generation_id FROM quality_generation q '
                    'JOIN analysis_run n ON n.run_id=q.normalize_run_id '
                    "WHERE q.generation_id=%s AND q.status='done' AND n.status='done' "
                    "AND n.kind='normalize' AND n.mission_id=%s", (generation_id, mission_id))
        row = cur.fetchone()
        if not row:
            raise ValueError('Нужен точный завершённый вход качества этой миссии.')
        return row
    root = runs.current_run_id(cur, mission_id, 'normalize')
    if root is None:
        raise ValueError('Нет завершённого прогона нормализации.')
    cur.execute("SELECT generation_id FROM quality_generation WHERE normalize_run_id=%s "
                "AND status='done' ORDER BY generation_id DESC LIMIT 1", (root,))
    row = cur.fetchone()
    if not row:
        raise ValueError('Сначала требуется завершённая оценка качества.')
    return root, row[0]


def require_vector_count(expected: int, actual: int) -> None:
    if expected < 1:
        raise ValueError('Нет допущенных работ в заданном календарном периоде.')
    if actual != expected:
        raise ValueError(f'Векторы не готовы: {actual} из {expected}; частичный корпус не запускает кластеризацию.')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Кластеризация по окнам и жизненный цикл тем")
    parser.add_argument("mission_id")
    parser.add_argument("--step", default=None, help="year | quarter (по умолчанию из паспорта)")
    parser.add_argument("--scale", default=None,
                        help="масштаб кластеризации из паспорта: micro | established")
    parser.add_argument("--model", default=None, help="имя модели эмбеддингов")
    parser.add_argument('--quality-generation', type=int, default=None, help='Точное завершённое поколение входа, не latest')
    parser.add_argument('--require-complete-vectors', action='store_true', help='Не запускать семантический анализ частичного корпуса')
    parser.add_argument(
        "--backend", choices=("graph", "bertopic", "global_bertopic"), default=None,
        help="движок кластеризации (по умолчанию prototype_backend из паспорта)",
    )
    parser.add_argument(
        "--current-period-reconstruction", action="store_true",
        help=("разрешить global_bertopic по всему текущему периоду; "
              "этот режим запрещён для исторического backtest"),
    )
    args = parser.parse_args(argv)
    result = generate(args)
    return result if isinstance(result, int) else 0


def analyze(mission_id: str, quality_generation_id: int, model: str,
            backend: str = 'bertopic', step: str | None = None,
            scale: str | None = None, config=None,
            current_period_reconstruction: bool = False) -> dict:
    if not isinstance(quality_generation_id, int) or isinstance(quality_generation_id, bool) or quality_generation_id < 1:
        raise ValueError('Долгий анализ требует точного положительного номера поколения качества.')
    if not isinstance(model, str) or not model.strip():
        raise ValueError('Долгий анализ требует точной версии модели.')
    args = argparse.Namespace(mission_id=mission_id, quality_generation=quality_generation_id,
                              model=model, backend=backend, step=step, scale=scale,
                              require_complete_vectors=True,
                              current_period_reconstruction=current_period_reconstruction)
    result = generate(args, config)
    if not isinstance(result, dict):
        raise ValueError('Семантический прогон не создан; вход не объявлен готовым.')
    return result


def generate(args, config=None):

    # Паспорт читается через валидирующий загрузчик. Прямой yaml.safe_load
    # был дефектом P0-5: все жёсткие проверки — сумма весов, запрет
    # современной терминологии, исключение цитирований из шлюзов — не
    # касались ровно тех модулей, которые считают результат.
    config = config or methodology.load_default()
    lifecycle = config.lifecycle
    clustering = config.clustering
    step = args.step or config.window_step

    scale_name = args.scale or clustering["active_scale"]
    if scale_name not in clustering["scales"]:
        print(f"масштаба '{scale_name}' нет в паспорте; доступны: "
              f"{sorted(clustering['scales'])}")
        return 1
    min_topic_size = int(clustering["scales"][scale_name]["min_topic_size"])
    seed = int(clustering["seed"])
    backend = args.backend or clustering.get("prototype_backend") or "bertopic"
    if backend == "global_bertopic" and not getattr(args, "current_period_reconstruction", False):
        print("global_bertopic использует весь период для задания микротем и запрещён без "
              "--current-period-reconstruction; для ретротеста используйте bertopic/graph")
        return 1
    graph_threshold = float(clustering.get("graph", {}).get("similarity_threshold", 0.18))

    decay_cfg = config.decay
    half_life = float(decay_cfg.get("half_life_days") or 0.0)
    decay_shape = str(decay_cfg["shape"])
    death_after = int(lifecycle["popularity"].get("death_after_silent_windows", 0))

    thresholds = lifecycle["dynamic_thresholds"]
    low_pct = float(thresholds["low_percentile"])
    high_pct = float(thresholds["high_percentile"])
    roll = int(thresholds["window_size"])
    link_threshold = config.link_threshold
    adaptive_quantile = float(lifecycle["linking"].get("adaptive_quantile", 0.99))
    anisotropy_trigger = float(
        lifecycle["linking"].get("anisotropy_trigger_median", 1.0)
    )

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT as_of_date, period_from FROM mission WHERE mission_id = %s",
            (args.mission_id,),
        )
        row = cur.fetchone()
        if not row:
            print(f"миссии {args.mission_id} нет в базе")
            return 1
        as_of, period_from = row

        try:
            normalize_run_id, quality_generation_id = resolve_quality_input(cur, args.mission_id, args.quality_generation)
        except ValueError as error:
            print(str(error))
            return 1
        period_from, as_of, period_origin = runs.analysis_period(cur, normalize_run_id)
        # Старая схема могла не задавать нижнюю границу.
        period_from = period_from or date.min

        model_name = args.model
        if not model_name:
            cur.execute(
                """
                SELECT e.model FROM work_embedding e JOIN work w USING (work_id)
                WHERE w.run_id = %s GROUP BY e.model ORDER BY count(*) DESC LIMIT 1
                """,
                (normalize_run_id,),
            )
            got = cur.fetchone()
            if not got:
                print("эмбеддингов нет — сначала python -m saia.embed")
                return 1
            model_name = got[0]

        # Только то, что видно на дату среза. Дисциплина среза применяется
        # здесь, а не при загрузке: одна выгрузка обслуживает много дат.
        cur.execute(
            """
            SELECT w.work_id, w.effective_date, w.canonical_title, w.abstract,
                   e.embedding::text
            FROM work w JOIN work_embedding e USING (work_id)
            JOIN quality_snapshot q ON q.work_id = w.work_id AND q.generation_id = %s
            WHERE w.run_id = %s AND e.model = %s
              AND w.effective_date >= %s AND w.effective_date < %s
              AND (q.decision IS NULL OR q.decision = 'include')
            ORDER BY w.effective_date, w.work_id
            """,
            (quality_generation_id, normalize_run_id, model_name, period_from, as_of),
        )
        rows = cur.fetchall()
        if args.require_complete_vectors:
            cur.execute(
                "SELECT count(*) FROM work w JOIN quality_snapshot q ON q.work_id=w.work_id "
                "AND q.generation_id=%s WHERE w.run_id=%s AND q.decision = 'include' "
                "AND w.effective_date >= %s AND w.effective_date < %s",
                (quality_generation_id, normalize_run_id, period_from, as_of),
            )
            require_vector_count(cur.fetchone()[0], len(rows))

    if not rows:
        print("на дату среза нет работ с эмбеддингами")
        return 0

    print(f"модель: {model_name}")
    print(f"работ на срезе {as_of}: {len(rows)}")
    print(f"паспорт: v{config.version} (hash {config.config_hash})")
    print(f"шаг окна: {step}, масштаб '{scale_name}', минимальный размер темы: {min_topic_size}")
    print(f"движок кластеризации: {backend}")
    print(f"затухание: полураспад {half_life:.0f} дн., форма {decay_shape}")

    by_window: dict[str, list[tuple]] = defaultdict(list)
    for work_id, day, title, abstract, vector_text in rows:
        vector = np.fromstring(vector_text.strip("[]"), sep=",", dtype=np.float32)
        text = f"{title} {abstract or ''}".strip()
        by_window[window_of(day, step)].append((work_id, text, vector))

    first_window = min(by_window)
    last_window = window_of(as_of - timedelta(days=1), step)
    windows = contiguous_windows(first_window, last_window, step)
    window_index = {key: i for i, key in enumerate(windows)}
    print(f"окон: {len(windows)}  ({windows[0]} .. {windows[-1]})\n")

    # topic_key -> состояние темы между окнами
    topics: dict[int, dict] = {}
    next_topic = 0
    snapshots: list[dict] = []
    lineage: list[dict] = []
    memberships: list[tuple] = []
    realised_link_thresholds: dict[str, float] = {}

    if backend == "global_bertopic":
        all_items = [item + (window,) for window in windows for item in by_window.get(window, [])]
        all_vectors = np.vstack([item[2] for item in all_items])
        all_texts = [item[1] for item in all_items]
        global_hdbscan = dict(clustering["hdbscan"])
        global_hdbscan["cluster_selection_method"] = clustering.get(
            "global_hdbscan_selection_method", global_hdbscan["cluster_selection_method"]
        )
        labels, global_terms = cluster_window(
            all_texts, all_vectors, min_topic_size, seed,
            dict(clustering["umap"]), global_hdbscan, link_threshold,
        )
        topics, snapshots, memberships, noise_count = project_global_topics(
            all_items, labels, global_terms, windows, step, half_life,
            decay_shape, roll, death_after,
        )
        print(f"  весь период: работ {len(all_items)}, устойчивых микротем {len(topics)}, "
              f"в шуме {noise_count} ({100 * noise_count / len(all_items):.0f}%)")
        by_window_snaps: dict[str, list[dict]] = defaultdict(list)
        for snap in snapshots:
            by_window_snaps[snap["window"]].append(snap)
        for window, snaps in by_window_snaps.items():
            values = np.array([s["popularity"] for s in snaps])
            low = float(np.percentile(values, low_pct))
            high = float(np.percentile(values, high_pct))
            for snap in snaps:
                snap["percentile"] = float((values <= snap["popularity"]).mean() * 100)
                snap["lifecycle"] = classify(snap["popularity"], low, high, snap["slope"])
        classes = defaultdict(int)
        for snap in snapshots:
            classes[snap["lifecycle"]] += 1
        print(f"снимков по классам: {dict(classes)}")
        run_id = write_results(
            args.mission_id, step, topics, snapshots, [], memberships, config,
            model_name, normalize_run_id, scale_name, min_topic_size, backend,
            {}, quality_generation_id,
        )
        print(f"готово, прогон #{run_id}")
        return {"run_id": run_id, "normalize_run_id": normalize_run_id,
                "quality_generation_id": quality_generation_id,
                "embedding_model": model_name, "eligible_vector_works": len(rows),
                "backend": backend, "window_step": step,
                "temporal_definition_scope": "full_period_current_reconstruction_not_backtest"}

    for window in windows:
        items = by_window.get(window, [])
        vectors = np.vstack([v for _, _, v in items]) if items else np.empty((0, 0))
        texts = [t for _, t, _ in items]

        if not items:
            labels, terms = np.array([], dtype=int), {}
        elif backend == "graph":
            labels, terms = graph_cluster_window(
                texts, vectors, min_topic_size, graph_threshold
            )
        else:
            labels, terms = cluster_window(
                texts, vectors, min_topic_size, seed,
                dict(clustering["umap"]), dict(clustering["hdbscan"]),
                link_threshold,
            )
        clusters = sorted({int(l) for l in labels.tolist() if l != -1})
        noise_count = int((labels == -1).sum())

        noise_share = 100 * noise_count / len(items) if items else 0.0
        print(f"  {window}: работ {len(items)}, кластеров {len(clusters)}, "
              f"в шуме {noise_count} ({noise_share:.0f}%)")

        centroids = (np.vstack([vectors[labels == c].mean(axis=0) for c in clusters])
                     if clusters else np.empty((0, 0)))

        # Сопоставление с существующими темами по ЯКОРНОМУ эмбеддингу.
        # Сравнение со скользящим центроидом дало бы дрейф: тема медленно
        # уходит от себя, а цепочка совпадений остаётся непрерывной.
        #
        # ПЕРЕПИСАНО ПОСЛЕ РЕВИЗИИ (дефект P0-4). Прежняя версия брала для
        # каждого кластера ЕДИНСТВЕННОГО лучшего родителя и при разделении
        # отдавала всем осколкам один и тот же topic_id. Дальше срабатывало
        # UNIQUE (topic_id, window_key), и лишние снимки молча исчезали:
        # разделение темы было не событием в журнале, а потерей данных.
        # Родословная при этом записывалась рёбрами parent = child, то есть
        # не несла никакой информации.
        #
        # Теперь связь двусторонняя: кластер может иметь несколько родителей
        # (слияние), родитель — несколько детей (разделение).
        active = [t for t in topics.values() if t["alive"]]
        anchors = np.vstack([t["anchor"] for t in active]) if active and clusters else None
        anchor_keys = [t["topic_key"] for t in active]
        realised_threshold = adaptive_link_threshold(
            centroids, anchors, link_threshold, anisotropy_trigger,
            adaptive_quantile,
        )
        realised_link_thresholds[window] = realised_threshold
        parents_of, children_of, similarity_to = link_clusters(
            centroids, anchors, anchor_keys, realised_threshold)

        seen_now: set[int] = set()
        closed_now: set[int] = set()

        def open_topic(centroid, cluster_label, window) -> int:
            nonlocal next_topic
            key = next_topic
            next_topic += 1
            topics[key] = {
                "topic_key": key, "anchor": centroid, "anchor_window": window,
                "first_window": window, "last_window": window,
                "terms": terms.get(cluster_label, []), "popularity": 0.0,
                "history": [], "last_state_window": None,
                "alive": True, "died_in": None,
            }
            return key

        for i, cluster in enumerate(clusters):
            members = [items[j][0] for j in range(len(items)) if labels[j] == cluster]
            centroid = centroids[i]
            ps = parents_of[i]

            event = transition_of(ps, children_of)

            if event == "new_core":
                key = open_topic(centroid, cluster, window)
                lineage.append({"parent": None, "child": key, "from": None,
                                "to": window, "event": "new_core", "similarity": None})

            elif event == "continuation":
                # Один родитель, один ребёнок — тема просто продолжается.
                # Продолжение не порождает записи в родословной: оно и есть
                # отсутствие смены личности, и выражено двумя последовательными
                # снимками одного topic_id.
                key = ps[0]

            else:
                # Личность темы меняется: либо родитель распался на несколько
                # кластеров, либо несколько родителей сошлись в один. В обоих
                # случаях рождается НОВАЯ тема со своим якорем, а родители
                # закрываются. Иначе история теряет момент, в котором смысл
                # под прежним идентификатором стал другим.
                key = open_topic(centroid, cluster, window)
                for parent_key in ps:
                    lineage.append({
                        "parent": parent_key, "child": key,
                        "from": topics[parent_key]["last_window"], "to": window,
                        "event": event, "similarity": similarity_to.get((i, parent_key)),
                    })
                    closed_now.add(parent_key)

            topic = topics[key]
            popularity, current_slope = advance_topic(
                topic, window, step, len(members), half_life, decay_shape, roll
            )
            topic["last_window"] = window
            topic["alive"] = True
            if not topic["terms"]:
                topic["terms"] = terms.get(cluster, [])
            seen_now.add(key)

            drift = 1.0 - float(cosine_matrix(centroid[None, :], topic["anchor"][None, :])[0][0])
            snapshots.append({
                "topic_key": key, "window": window, "doc_count": len(members),
                "popularity": popularity, "slope": current_slope,
                "centroid": centroid, "drift": drift,
            })
            memberships.extend((key, work_id, window) for work_id in members)

        # Родители, распавшиеся или слившиеся, закрываются: их линия
        # продолжена детьми, и держать их в кандидатах на связывание значит
        # разрешить будущей теме приклеиться к уже закончившейся истории.
        for parent_key in closed_now:
            if parent_key not in seen_now:
                topics[parent_key]["alive"] = False
                topics[parent_key]["died_in"] = window

        # Темы, не пополнившиеся в этом окне, затухают.
        for topic in topics.values():
            if topic["topic_key"] in seen_now or not topic["alive"]:
                continue
            popularity, current_slope = advance_topic(
                topic, window, step, 0, half_life, decay_shape, roll
            )

            # P1-5: тема, молчавшая слишком долго, перестаёт быть кандидатом
            # для связывания. Раньше якоря жили вечно, и новая тема могла
            # приклеиться к линии, угасшей несколько лет назад.
            silent_windows = window_index[window] - window_index[topic["last_window"]]
            if is_dead(silent_windows, death_after):
                topic["alive"] = False
                topic["died_in"] = window

            snapshots.append({
                "topic_key": topic["topic_key"], "window": window, "doc_count": 0,
                "popularity": popularity, "slope": current_slope,
                "centroid": None, "drift": None,
            })

    # --- перцентили и классификация -------------------------------------
    # Перцентиль считается по распределению ВСЕХ тем окна. Абсолютная
    # популярность между окнами несопоставима, перцентиль — сопоставим.
    by_window_snaps: dict[str, list[dict]] = defaultdict(list)
    for snap in snapshots:
        by_window_snaps[snap["window"]].append(snap)

    for window, snaps in by_window_snaps.items():
        values = np.array([s["popularity"] for s in snaps])
        low = float(np.percentile(values, low_pct))
        high = float(np.percentile(values, high_pct))
        for snap in snaps:
            # Эмпирический CDF, а не доля строго меньших. При двух темах
            # прежняя формула давала максимум 50, поэтому шлюз >=65 был
            # математически непроходим независимо от роста кандидата.
            snap["percentile"] = float((values <= snap["popularity"]).mean() * 100)
            snap["lifecycle"] = classify(snap["popularity"], low, high, snap["slope"])

    print(f"\nтем всего: {len(topics)}")
    classes = defaultdict(int)
    for snap in snapshots:
        classes[snap["lifecycle"]] += 1
    print(f"снимков по классам: {dict(classes)}")

    last = windows[-1]
    weak_now = [s for s in by_window_snaps[last] if s["lifecycle"] == "weak"]
    print(f"\nв последнем окне {last}: линий класса lifecycle=weak {len(weak_now)}")
    print('Это эвристика популярности; допуск слабого сигнала ещё не проверен.')
    for snap in sorted(weak_now, key=lambda s: -s["popularity"])[:10]:
        terms = ", ".join(topics[snap["topic_key"]]["terms"][:6])
        print(f"  p={snap['popularity']:7.1f}  наклон {snap['slope']:+7.2f}  "
              f"перц {snap['percentile']:5.1f}  {terms}")

    print("\nзапись в базу ...")
    run_id = write_results(args.mission_id, step, topics, snapshots, lineage,
                           memberships, config, model_name, normalize_run_id,
                           scale_name, min_topic_size, backend,
                           realised_link_thresholds, quality_generation_id)
    print(f"готово, прогон #{run_id}")
    return {"run_id": run_id, "normalize_run_id": normalize_run_id,
            "quality_generation_id": quality_generation_id,
            "embedding_model": model_name, "eligible_vector_works": len(rows),
            "backend": backend, "window_step": step}


def write_results(mission_id: str, step: str, topics: dict, snapshots: list,
                  lineage: list, memberships: list,
                  config: methodology.Methodology, model_name: str,
                  normalize_run_id: int, scale_name: str,
                  min_topic_size: int, backend: str = "bertopic",
                  realised_link_thresholds: dict[str, float] | None = None,
                  quality_generation_id: int | None = None) -> int:
    """Записать результат новым поколением.

    Здесь был дефект P0-3: функция начиналась с DELETE FROM topic по всей
    миссии, то есть каждый прогон уничтожал ответ предыдущего. Теперь темы
    принадлежат прогону, прежние поколения остаются на месте и доступны
    для сравнения.
    """
    def vec(array) -> str | None:
        if array is None:
            return None
        return "[" + ",".join(f"{v:.6f}" for v in array) + "]"

    with db.connect() as conn:
        with conn.cursor() as cur:
            run_id = runs.start_run(
                cur, mission_id, "cluster", config, embedding_model=model_name,
                notes={"window_step": step, "topics": len(topics),
                       "snapshots": len(snapshots), "lineage_events": len(lineage),
                       "min_topic_size": min_topic_size,
                       "clustering_backend": backend,
                       "temporal_definition_scope": (
                           "full_period_current_reconstruction_not_backtest"
                           if backend == "global_bertopic"
                           else "window_local_as_of_compatible"
                       ),
                       'quality_generation_id': quality_generation_id,
                       'effective_config': config.raw,
                       "realised_link_thresholds": realised_link_thresholds or {}},
                upstream_run_id=normalize_run_id,
                window_step=step,
                clustering_scale=f"{scale_name}:{backend}",
            )

            ids: dict[int, int] = {}
            for key, topic in topics.items():
                cur.execute(
                    """
                    INSERT INTO topic (mission_id, run_id, label, top_terms,
                                       anchor_embedding, anchor_window,
                                       first_window, last_window, alive, died_in_window)
                    VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s, %s, %s)
                    RETURNING topic_id
                    """,
                    (mission_id, run_id, " / ".join(topic["terms"][:4]) or None,
                     topic["terms"], vec(topic["anchor"]), topic["anchor_window"],
                     topic["first_window"], topic["last_window"],
                     topic["alive"], topic.get("died_in")),
                )
                ids[key] = cur.fetchone()[0]

            for snap in snapshots:
                start, end = window_bounds(snap["window"], step)
                cur.execute(
                    """
                    INSERT INTO topic_snapshot
                        (topic_id, window_key, window_start, window_end, doc_count,
                         popularity, slope, centroid, drift_from_anchor,
                         popularity_percentile, lifecycle_class)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s)
                    """,
                    (ids[snap["topic_key"]], snap["window"], start, end, snap["doc_count"],
                     snap["popularity"], snap.get("slope"), vec(snap.get("centroid")),
                     snap.get("drift"), snap.get("percentile"), snap.get("lifecycle")),
                )

            for link in lineage:
                cur.execute(
                    """
                    INSERT INTO topic_lineage (mission_id, parent_topic, child_topic,
                                               from_window, to_window, event, similarity)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (mission_id,
                     ids.get(link["parent"]) if link["parent"] is not None else None,
                     ids[link["child"]], link["from"], link["to"],
                     link["event"], link["similarity"]),
                )

            for key, work_id, window in memberships:
                cur.execute(
                    "INSERT INTO topic_membership (topic_id, work_id, window_key) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (ids[key], work_id, window),
                )

            runs.finish_run(cur, run_id, "done")
        conn.commit()
    return run_id


if __name__ == "__main__":
    sys.exit(main())
