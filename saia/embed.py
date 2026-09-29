"""Эмбеддинги работ и семантическая новизна.

    python -m saia.embed gnn-fraud
    python -m saia.embed gnn-fraud --novelty

Провайдер по умолчанию — Ollama на хосте: модель уже стоит локально,
данные корпуса не уходят наружу, и прогон тридцати тысяч работ ничего не
стоит. Альтернативный провайдер sentence-transformers включается через
SAIA_EMBEDDING_PROVIDER=sentence_transformers и тянет torch — он нужен
только ради SPECTER, обученного на графе цитирования.

Смена модели меняет смысл всех расстояний. Поэтому имя модели входит в
первичный ключ таблицы, и новизна считается только по векторам одной
модели: сравнить SPECTER с BGE-M3 можно лишь как два отдельных прогона.
"""

from __future__ import annotations

import json
import math
import os
import hashlib
import re
import sys
import urllib.error
import urllib.request

from saia import db, methodology, runs

DEFAULT_OLLAMA_MODEL = "bge-m3"
BATCH = 16
SPECTER2_BASE = "allenai/specter2_base"
SPECTER2_ADAPTER = "allenai/specter2"
SPECTER2_BASE_REVISION = "3447645e1def9117997203454fa4495937bfbd83"
SPECTER2_ADAPTER_REVISION = "2081559630a80fc5851d8f798a05ba81e9468089"


class EmbeddingError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Провайдеры
# ---------------------------------------------------------------------------

class OllamaEmbedder:
    """Локальная Ollama. Никаких зависимостей сверх стандартной библиотеки."""

    def __init__(self, model: str | None = None, url: str | None = None) -> None:
        self.model = model or os.environ.get("SAIA_EMBEDDING_MODEL") or DEFAULT_OLLAMA_MODEL
        self.url = (url or os.environ.get("SAIA_OLLAMA_URL")
                    or "http://host.docker.internal:11434").rstrip("/")
        self.name = f"ollama/{self.model}"

    def _post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise EmbeddingError(
                f"Ollama ответила {error.code} на {path}: "
                f"{error.read()[:300].decode('utf-8', 'replace')}"
            ) from error
        except urllib.error.URLError as error:
            raise EmbeddingError(
                f"Ollama недоступна по адресу {self.url} ({error.reason}).\n"
                "  Проверьте, что она запущена на хосте и что контейнер видит "
                "host.docker.internal."
            ) from error

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Новый пакетный endpoint; на старых сборках его нет — тогда
        # поштучно через /api/embeddings.
        try:
            payload = self._post("/api/embed", {"model": self.model, "input": texts})
            vectors = payload.get("embeddings")
            if vectors:
                return vectors
        except EmbeddingError:
            pass

        vectors = []
        for text in texts:
            payload = self._post("/api/embeddings", {"model": self.model, "prompt": text})
            vector = payload.get("embedding")
            if not vector:
                raise EmbeddingError(f"Ollama вернула пустой вектор для модели {self.model}")
            vectors.append(vector)
        return vectors


class SentenceTransformersEmbedder:
    """SPECTER через sentence-transformers. Требует extra ml (torch)."""

    def __init__(self, model: str | None = None) -> None:
        self.model_name = model or os.environ.get("SAIA_EMBEDDING_MODEL") or "allenai-specter"
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise EmbeddingError(
                "sentence-transformers не установлен.\n"
                "  Либо соберите образ с extra ml, либо используйте "
                "SAIA_EMBEDDING_PROVIDER=ollama."
            ) from error
        self.model = SentenceTransformer(self.model_name)
        self.name = f"st/{self.model_name}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self.model.encode(texts, show_progress_bar=False)]


class Specter2Embedder:
    """SPECTER2 proximity с официальным адаптером AllenAI.

    Обычный ``SentenceTransformer('specter2_base')`` здесь недостаточен:
    карточка модели требует proximity-adapter и CLS pooling. Ревизии базы и
    адаптера закреплены, чтобы повторный прогон не поменял модель молча.
    """

    def __init__(self) -> None:
        try:
            import torch
            from adapters import AutoAdapterModel
            from transformers import AutoTokenizer
        except ImportError as error:
            raise EmbeddingError(
                "SPECTER2 требует extra ml: pip install -e '.[ml]'"
            ) from error
        thread_limit = os.environ.get("SAIA_TORCH_NUM_THREADS")
        if thread_limit:
            try:
                threads = int(thread_limit)
            except ValueError as error:
                raise EmbeddingError("SAIA_TORCH_NUM_THREADS должен быть целым числом") from error
            if not 1 <= threads <= 16:
                raise EmbeddingError("SAIA_TORCH_NUM_THREADS должен быть от 1 до 16")
            torch.set_num_threads(threads)
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            SPECTER2_BASE, revision=SPECTER2_BASE_REVISION
        )
        self.model = AutoAdapterModel.from_pretrained(
            SPECTER2_BASE, revision=SPECTER2_BASE_REVISION
        )
        adapter_name = self.model.load_adapter(
            SPECTER2_ADAPTER, source="hf", load_as="proximity",
            set_active=True, revision=SPECTER2_ADAPTER_REVISION,
        )
        self.model.set_active_adapters(adapter_name)
        self.adapter_name = str(adapter_name)
        self.active_adapters = repr(self.model.active_adapters)
        if "proximity" not in self.active_adapters:
            raise EmbeddingError("SPECTER2 proximity adapter was loaded but is not active")
        requested = os.environ.get("SAIA_TORCH_DEVICE")
        if requested:
            self.device = requested
        elif torch.backends.mps.is_available():
            self.device = "mps"
        else:
            self.device = "cpu"
        self.model.to(self.device)
        self.model.eval()
        self.name = (
            f"specter2/proximity@{SPECTER2_BASE_REVISION[:8]}+"
            f"{SPECTER2_ADAPTER_REVISION[:8]}"
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        prepared = []
        for text in texts:
            title, separator, abstract = text.partition("\n\n")
            prepared.append(
                title + self.tokenizer.sep_token + abstract if separator else title
            )
        inputs = self.tokenizer(
            prepared, padding=True, truncation=True, return_tensors="pt",
            return_token_type_ids=False, max_length=512,
        )
        inputs = {name: value.to(self.device) for name, value in inputs.items()}
        with self.torch.inference_mode():
            vectors = self.model(**inputs).last_hidden_state[:, 0, :]
        return vectors.detach().cpu().float().tolist()


class HashingNgramEmbedder:
    """Воспроизводимый CPU-baseline без скачивания весов.

    Вектор строится из униграмм и биграмм фиксированным хешем. Здесь нет
    обучаемого словаря и IDF по всему корпусу, поэтому поздние документы не
    меняют представление ранних — важное свойство для as-of ретротеста.
    Это baseline для вертикального среза, а не замена SPECTER.
    """

    TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or int(os.environ.get("SAIA_HASHING_DIM", "384"))
        self.name = f"hashing-ngram/v2-d{self.dim}"

    def _vector(self, text: str) -> list[float]:
        title, separator, abstract = text.partition("\n\n")
        # Название — наиболее концентрированное описание вклада. Тройной
        # вес не зависит от корпуса и не использует будущие документы.
        weighted = f"{title} {title} {title} {abstract}" if separator else text
        tokens = self.TOKEN.findall(weighted.casefold())
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        vector = [0.0] * self.dim
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            number = int.from_bytes(digest, "big")
            index = number % self.dim
            sign = 1.0 if (number >> 8) & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]


def make_embedder():
    provider = (os.environ.get("SAIA_EMBEDDING_PROVIDER") or "ollama").lower()
    if provider in ("hashing", "hashing_ngram"):
        return HashingNgramEmbedder()
    if provider == "ollama":
        return OllamaEmbedder()
    if provider in ("sentence_transformers", "st"):
        return SentenceTransformersEmbedder()
    if provider in ("specter2", "specter2_proximity"):
        return Specter2Embedder()
    raise EmbeddingError(f"неизвестный провайдер эмбеддингов: {provider}")


# ---------------------------------------------------------------------------
# Расчёт
# ---------------------------------------------------------------------------

def build_text(title: str, abstract: str | None) -> tuple[str, str]:
    """Текст для эмбеддинга и пометка, из чего он собран."""
    if abstract:
        return f"{title}\n\n{abstract}", "title_abstract"
    return title, "title_only"


def validate_vector_batch(vectors, count: int, expected_dim: int | None = None) -> int:
    if not isinstance(vectors, list) or len(vectors) != count or count < 1:
        raise EmbeddingError('Модель вернула неправильное количество векторов; пакет не сохранён.')
    try:
        dimensions = {len(v) for v in vectors}
        if len(dimensions) != 1 or not next(iter(dimensions)):
            raise ValueError
        dimension = next(iter(dimensions))
        if expected_dim is not None and dimension != expected_dim:
            raise EmbeddingError('Размерность модели изменилась между пакетами; пакет не сохранён.')
        if any(any(isinstance(x, bool) or not math.isfinite(x) for x in v) or sum(x*x for x in v) == 0 for v in vectors):
            raise ValueError
    except (TypeError, ValueError):
        raise EmbeddingError('Нулевой, пустой, нечисловой или несогласованный вектор; пакет не сохранён.') from None
    return dimension


def reuse_exact_text_embeddings(cur, pending: list[tuple], model: str,
                                expected_dim: int | None) -> tuple[set[int], int | None]:
    """Copy a prior vector only for identical title, abstract and model.

    The indexed MD5 of ``title_key`` narrows the lookup. It is never accepted
    as identity: the full canonical title, abstract, text source, length and
    vector shape are verified before an immutable work-specific row is added.
    """
    if not pending:
        return set(), expected_dim
    by_text = {}
    title_hashes = sorted({hashlib.md5(title_key.encode("utf-8")).hexdigest()
                           for _, title_key, _, _ in pending})
    for start in range(0, len(title_hashes), 500):
        cur.execute(
            "SELECT w.canonical_title,w.abstract,e.dim,e.embedding::text,"
            "e.text_source,e.char_count FROM work w "
            "JOIN work_embedding e USING(work_id) "
            "WHERE md5(w.title_key) = ANY(%s::text[]) AND e.model=%s "
            "ORDER BY e.work_id",
            (title_hashes[start:start + 500], model),
        )
        for title, abstract, dim, vector_text, source, char_count in cur.fetchall():
            key = (title, abstract)
            if key in by_text:
                continue
            text, expected_source = build_text(title, abstract)
            if source != expected_source or char_count != len(text):
                continue
            try:
                vector = [float(value) for value in vector_text.strip("[]").split(",")]
                if validate_vector_batch([vector], 1) != dim:
                    continue
            except (TypeError, ValueError, EmbeddingError):
                continue
            by_text[key] = (dim, vector_text, source, char_count)

    reused = set()
    for work_id, _title_key, title, abstract in pending:
        cached = by_text.get((title, abstract))
        if cached is None:
            continue
        dim, vector_text, source, char_count = cached
        if expected_dim is not None and dim != expected_dim:
            continue
        cur.execute(
            "INSERT INTO work_embedding (work_id,model,dim,embedding,text_source,char_count) "
            "VALUES (%s,%s,%s,%s::vector,%s,%s) ON CONFLICT DO NOTHING",
            (work_id, model, dim, vector_text, source, char_count),
        )
        if cur.rowcount:
            reused.add(work_id)
            expected_dim = dim
    return reused, expected_dim


def embed_mission(mission_id: str, refresh: bool = False,
                  quality_generation_id: int | None = None) -> dict:
    if refresh:
        raise EmbeddingError('Refresh удалял вход старых результатов и запрещён. '
                             'Создайте новый корпус или новую версию модели; старые векторы сохраняются.')
    batch_size = int(os.environ.get("SAIA_EMBEDDING_BATCH", str(BATCH)))
    if batch_size < 1:
        raise EmbeddingError("SAIA_EMBEDDING_BATCH должен быть положительным")
    with db.connect() as conn:
        with conn.cursor() as cur:
            if quality_generation_id is None:
                normal_run = runs.current_run_id(cur, mission_id, 'normalize')
                cur.execute("SELECT generation_id FROM quality_generation WHERE normalize_run_id = %s "
                            "AND status = 'done' ORDER BY generation_id DESC LIMIT 1", (normal_run,))
                row = cur.fetchone()
                if not row:
                    raise EmbeddingError('Нет завершённого поколения качества; сначала выполните quality.')
                quality_generation_id = row[0]
            cur.execute('SELECT r.run_id FROM quality_generation q JOIN analysis_run r '
                        'ON r.run_id = q.normalize_run_id WHERE q.generation_id = %s '
                        "AND q.status = 'done' AND r.status = 'done' AND r.mission_id = %s",
                        (quality_generation_id, mission_id))
            row = cur.fetchone()
            if not row:
                raise EmbeddingError('Поколение качества отсутствует или принадлежит другой миссии.')
            normal_run = row[0]
            period_from, period_end, period_origin = runs.analysis_period(cur, normal_run)
            # Проверки корпуса предшествуют возможной загрузке модели.
            embedder = make_embedder()
            summary = {"model": embedder.name, "computed": 0, "reused_exact_text": 0,
                       "skipped": 0, "title_only": 0,
                       'normalize_run_id': normal_run, 'quality_generation_id': quality_generation_id,
                       'period_origin': period_origin, 'persistence_policy': 'atomic_committed_batches_v1'}
            cur.execute('SELECT DISTINCT e.dim, vector_dims(e.embedding) FROM work_embedding e '
                        'JOIN work w USING(work_id) WHERE w.run_id=%s AND e.model=%s',
                        (normal_run, embedder.name))
            stored_dims = cur.fetchall()
            if len(stored_dims) > 1 or any(d != actual or d < 1 for d, actual in stored_dims):
                raise EmbeddingError('Существующие векторы имеют несогласованную размерность; данные не изменены.')
            expected_dim = stored_dims[0][0] if stored_dims else None

            cur.execute(
                """
                SELECT w.work_id, w.title_key, w.canonical_title, w.abstract
                FROM work w JOIN quality_snapshot q USING (work_id)
                WHERE w.run_id = %s AND q.generation_id = %s
                  AND (%s::date IS NULL OR w.effective_date >= %s)
                  AND w.effective_date < %s
                  AND q.decision = 'include'
                  AND NOT EXISTS (
                      SELECT 1 FROM work_embedding e
                      WHERE e.work_id = w.work_id AND e.model = %s
                  )
                ORDER BY w.work_id
                """,
                (normal_run, quality_generation_id, period_from, period_from, period_end, embedder.name),
            )
            pending = cur.fetchall()
            cur.execute('SELECT count(*) FROM work w JOIN quality_snapshot q USING(work_id) '
                        'JOIN work_embedding e USING(work_id) WHERE w.run_id = %s '
                        "AND q.generation_id = %s AND q.decision = 'include' AND e.model = %s "
                        'AND (%s::date IS NULL OR w.effective_date >= %s) AND w.effective_date < %s',
                        (normal_run, quality_generation_id, embedder.name, period_from, period_from, period_end))
            summary['skipped'] = cur.fetchone()[0]

            reused, expected_dim = reuse_exact_text_embeddings(
                cur, pending, embedder.name, expected_dim
            )
            if reused:
                summary["reused_exact_text"] = len(reused)
                pending = [row for row in pending if row[0] not in reused]
                # The next model batch may fail or be interrupted. Exact
                # reused vectors remain committed, just like computed batches.
                conn.commit()

            if not pending:
                print("  все работы уже посчитаны этой моделью")

            for start in range(0, len(pending), batch_size):
                chunk = pending[start:start + batch_size]
                prepared = [build_text(title, abstract)
                            for _, _, title, abstract in chunk]
                vectors = embedder.embed([text for text, _ in prepared])
                expected_dim = validate_vector_batch(vectors, len(chunk), expected_dim)

                for (work_id, _, _, _), (text, source), vector in zip(chunk, prepared, vectors):
                    cur.execute(
                        """
                        INSERT INTO work_embedding
                            (work_id, model, dim, embedding, text_source, char_count)
                        VALUES (%s, %s, %s, %s::vector, %s, %s)
                        ON CONFLICT (work_id, model) DO NOTHING
                        """,
                        (work_id, embedder.name, len(vector),
                         "[" + ",".join(f"{v:.6f}" for v in vector) + "]",
                         source, len(text)),
                    )
                    if cur.rowcount:
                        summary["computed"] += 1
                        if source == "title_only":
                            summary["title_only"] += 1
                    else:
                        summary['skipped'] += 1

                # Commit only complete validated batches. An interrupted long
                # job keeps earlier immutable vectors and can resume by exact
                # work ID/model; this does NOT declare the whole corpus ready.
                conn.commit()
                done = min(start + batch_size, len(pending))
                if done == len(pending) or done % 512 == 0:
                    print(f"  посчитано {done} из {len(pending)}", flush=True)

        conn.commit()

    return summary


# ---------------------------------------------------------------------------
# Семантическая новизна
# ---------------------------------------------------------------------------

def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def centroid(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    return [sum(v[i] for v in vectors) / n for i in range(len(vectors[0]))]


# ИСПРАВЛЕНО ПОСЛЕ РЕВИЗИИ (дефект P0-5). Здесь стояли константы 3 и 1.15,
# зашитые в код, при том что паспорт объявлен единственным источником истины.
# Одинаковый YAML не гарантировал одинаковый результат.
_CLUSTERING = methodology.load_default().clustering
MIN_WORKS_PER_WINDOW = int(_CLUSTERING["min_works_per_window"])
TITLE_ONLY_ARTIFACT_RATIO = float(_CLUSTERING["title_only_artifact_ratio"])


def novelty_table(by_year: dict[int, list[list[float]]], title_only: dict[int, int],
                  label: str) -> None:
    years = sorted(y for y in by_year if len(by_year[y]) >= MIN_WORKS_PER_WINDOW)
    dropped = sorted(y for y in by_year if len(by_year[y]) < MIN_WORKS_PER_WINDOW)
    if not years:
        print(f"\n  {label}: не осталось окон с {MIN_WORKS_PER_WINDOW}+ работами")
        return

    centroids = {y: centroid(by_year[y]) for y in years}
    print(f"\n  {label}")
    if dropped:
        print(f"    окна отброшены как слишком малые: {', '.join(str(y) for y in dropped)}")
    print(f"    {'год':<6}{'работ':>7}{'без аннотации':>16}{'novelty':>11}   ближайшее прошлое")
    for i, year in enumerate(years):
        n = len(by_year[year])
        weak = title_only.get(year, 0)
        if i == 0:
            print(f"    {year:<6}{n:>7}{weak:>16}{'—':>11}   первое окно")
            continue
        similarities = {p: cosine(centroids[year], centroids[p]) for p in years[:i]}
        nearest, best = max(similarities.items(), key=lambda kv: kv[1])
        print(f"    {year:<6}{n:>7}{weak:>16}{1 - best:>11.4f}   {nearest} (сходство {best:.4f})")


def novelty(mission_id: str, model: str | None = None, field: str | None = None) -> None:
    """Новизна окна как расстояние до центроидов всех прошлых окон.

    Методика определяет новизну через расстояние до центроидов зрелых
    кластеров предыдущего периода. Кластеров у нас пока нет — корпус
    слишком мал, чтобы кластеризация сошлась, — поэтому роль кластера
    играет всё окно целиком. Это упрощение, и оно названо: на широком
    корпусе окно заменится кластерами.
    """
    with db.connect() as conn, conn.cursor() as cur:
        if model is None:
            cur.execute(
                """
                SELECT e.model, count(*) FROM work_embedding e
                JOIN work_current w USING (work_id) WHERE w.mission_id = %s
                GROUP BY e.model ORDER BY count(*) DESC LIMIT 1
                """,
                (mission_id,),
            )
            row = cur.fetchone()
            if not row:
                print("эмбеддингов нет — сначала запустите без --novelty")
                return
            model, count = row
            print(f"модель: {model}  ({count} векторов)")

        # Фильтр по предметной области отсекает работы, попавшие в выборку
        # по случайным совпадениям. Он безопасен на любую дату среза:
        # область работы не меняется со временем, в отличие от темы.
        cur.execute(
            """
            SELECT w.publication_year, e.embedding::text, e.text_source,
                   w.canonical_title, w.work_id
            FROM work_embedding e JOIN work_current w USING (work_id)
            WHERE w.mission_id = %s AND e.model = %s AND w.publication_year IS NOT NULL
              AND (%s::text IS NULL OR w.primary_field = %s)
            ORDER BY w.publication_year
            """,
            (mission_id, model, field, field),
        )
        rows = []
        for year, vector_text, source, title, work_id in cur.fetchall():
            rows.append({
                "year": year,
                "vector": [float(x) for x in vector_text.strip("[]").split(",")],
                "source": source,
                "title": title,
                "work_id": work_id,
            })

    def group(selected: list[dict]) -> tuple[dict, dict]:
        by_year: dict[int, list[list[float]]] = {}
        title_only: dict[int, int] = {}
        for row in selected:
            by_year.setdefault(row["year"], []).append(row["vector"])
            if row["source"] == "title_only":
                title_only[row["year"]] = title_only.get(row["year"], 0) + 1
        return by_year, title_only

    print(f"\n{'-' * 72}")
    print("СЕМАНТИЧЕСКАЯ НОВИЗНА ПО ОКНАМ")
    print("novelty = 1 − max сходство центроида окна с центроидами прошлых окон")
    print("-" * 72)

    all_by_year, all_weak = group(rows)
    novelty_table(all_by_year, all_weak, "ВСЕ РАБОТЫ")

    # Работы без аннотации дают вектор по одному заголовку. Такие векторы
    # систематически смещены относительно полных, поэтому доля неполных
    # записей в окне сама по себе двигает центроид — и разница в полноте
    # метаданных читается как смысловой сдвиг. Второй расчёт снимает это
    # подозрение: если картина меняется, значит первая таблица меряла
    # качество данных, а не новизну.
    full = [r for r in rows if r["source"] == "title_abstract"]
    full_by_year, full_weak = group(full)
    novelty_table(full_by_year, full_weak,
                  f"ТОЛЬКО С АННОТАЦИЕЙ ({len(full)} из {len(rows)} работ)")

    # --- отдельные работы, а не центроиды -------------------------------
    # Центроид года усредняет прорывную работу с десятком рядовых. Если
    # SPECTER вообще различает новизну, она должна быть видна на уровне
    # конкретной работы относительно всего, что было раньше.
    print(f"\n{'-' * 72}")
    print("САМЫЕ УДАЛЁННЫЕ ОТ ПРОШЛОГО РАБОТЫ (по годам)")
    print("расстояние работы до центроида ВСЕХ работ предыдущих лет")
    print("-" * 72)

    by_year_rows: dict[int, list[dict]] = {}
    for row in rows:
        by_year_rows.setdefault(row["year"], []).append(row)

    years = sorted(by_year_rows)
    for i, year in enumerate(years):
        if i == 0:
            continue
        past = [r["vector"] for y in years[:i] for r in by_year_rows[y]]
        if len(past) < MIN_WORKS_PER_WINDOW:
            continue
        past_centroid = centroid(past)
        scored = sorted(
            ((1 - cosine(r["vector"], past_centroid), r) for r in by_year_rows[year]),
            key=lambda pair: -pair[0],
        )
        print(f"\n  {year}:")
        for distance, row in scored[:3]:
            mark = " (только заголовок)" if row["source"] == "title_only" else ""
            print(f"    {distance:.4f}  {row['title'][:58]}{mark}")

    # --- проверка артефакта длины текста --------------------------------
    # Если вектор по одному заголовку систематически дальше от центроида,
    # чем вектор по заголовку с аннотацией, то «новизна» частично меряет
    # полноту метаданных. Это проверяется прямо, а не по виду таблиц.
    distances = {"title_abstract": [], "title_only": []}
    for i, year in enumerate(years):
        if i == 0:
            continue
        past = [r["vector"] for y in years[:i] for r in by_year_rows[y]]
        if len(past) < MIN_WORKS_PER_WINDOW:
            continue
        past_centroid = centroid(past)
        for row in by_year_rows[year]:
            distances[row["source"]].append(1 - cosine(row["vector"], past_centroid))

    print(f"\n{'-' * 72}")
    print("АРТЕФАКТ ПОЛНОТЫ ТЕКСТА")
    print("-" * 72)
    for source, values in distances.items():
        if not values:
            print(f"  {source:<16} нет данных")
            continue
        mean = sum(values) / len(values)
        top = sorted(values, reverse=True)[:max(1, len(values) // 10)]
        print(f"  {source:<16} работ {len(values):>4}   среднее расстояние {mean:.4f}"
              f"   верхний дециль {sum(top) / len(top):.4f}")

    if distances["title_only"] and distances["title_abstract"]:
        only = sum(distances["title_only"]) / len(distances["title_only"])
        full = sum(distances["title_abstract"]) / len(distances["title_abstract"])
        ratio = only / full if full else 0
        print(f"\n  Отношение средних: {ratio:.2f}×")
        if ratio > TITLE_ONLY_ARTIFACT_RATIO:
            print("  Работы без аннотации систематически дальше от центроида.")
            print("  Значит часть «новизны» — это разница в полноте метаданных,")
            print("  а не смысловой сдвиг. Такие работы нельзя смешивать с полными")
            print("  в одном расчёте новизны.")
        else:
            print("  Систематического смещения не видно: расстояние не объясняется")
            print("  отсутствием аннотации.")

    print("\n  Значения абсолютные, а не перцентили: перцентиль требует выборки")
    print("  соседних тем, которой на корпусе из одной темы не существует.")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Эмбеддинги и семантическая новизна Horizon")
    parser.add_argument("mission_id")
    parser.add_argument("--novelty", action="store_true", help="посчитать новизну по окнам")
    parser.add_argument("--refresh", action="store_true", help="устарело: удаление старых векторов запрещено")
    parser.add_argument('--quality-generation', type=int, default=None,
                        help='закреплённое поколение качества; по умолчанию последнее завершённое')
    parser.add_argument("--field", default=None,
                        help="считать новизну только по работам этой предметной области")
    args = parser.parse_args()

    if args.novelty:
        if args.field:
            print(f"фильтр предметной области: {args.field}")
        novelty(args.mission_id, field=args.field)
        return 0

    print(f"Считаю эмбеддинги для миссии {args.mission_id} ...")
    summary = embed_mission(args.mission_id, refresh=args.refresh,
                            quality_generation_id=args.quality_generation)
    print(f"\nМодель: {summary['model']}")
    print(f"Посчитано векторов: {summary['computed']}")
    print(f"  из них только по заголовку: {summary['title_only']}"
          f"  <- у этих работ нет аннотации, вектор беднее")
    print("\nДальше: python -m saia.embed", args.mission_id, "--novelty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
