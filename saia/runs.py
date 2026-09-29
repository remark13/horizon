"""Прогон как поколение результата.

Добавлено после внешней ревизии, дефекты P0-3 и P0-6.

P0-6. Таблица analysis_run существовала с первой миграции и не заполнялась
никогда — код обращался к ней только на чтение, ради вывода статистики.
Хеш методики, объявленный основой прослеживаемости, не был связан ни с одним
результатом. Утверждение «по результату видно, какой версией методики он
получен» было ложным с самого начала.

P0-3. Нормализация и кластеризация начинались с DELETE по mission_id.
Доказать, что система знала на прежнем срезе, было невозможно: прошлый
ответ физически стирался следующим запуском. Главная заявленная цель —
прослеживаемость — нарушалась самой механикой пересчёта.

Здесь прогон становится поколением: результаты принадлежат прогону, прогоны
не удаляют друг друга, текущим считается последний ЗАВЕРШЁННЫЙ.
"""

from __future__ import annotations

import os
import hashlib
import subprocess
import sys
import platform
from importlib import metadata
from pathlib import Path
from datetime import date, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from saia.methodology import Methodology


def runtime_snapshot() -> dict:
    versions = {}
    for package in ('numpy', 'scipy', 'scikit-learn', 'torch', 'transformers', 'adapters',
                    'sentence-transformers', 'bertopic', 'hdbscan', 'umap-learn',
                    'psycopg', 'pyyaml', 'pyarrow', 'fastapi'):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return {'python': sys.version.split()[0], 'system': platform.system(),
            'architecture': platform.machine(), 'packages': versions}


def history(mission_id: str, limit: int = 100) -> dict:
    from saia import db
    if not 1 <= limit <= 500:
        raise ValueError('Лимит истории должен быть от 1 до 500.')
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT title FROM mission WHERE mission_id = %s', (mission_id,))
        mission = cur.fetchone()
        if not mission:
            raise ValueError('Миссия отсутствует.')
        cur.execute('SELECT r.run_id, r.kind, r.status, r.as_of_date, r.methodology_version, '
                    'r.methodology_hash, r.query_version_id, r.upstream_run_id, r.embedding_model, '
                    'r.started_at, r.finished_at, r.error, r.notes, r.code_version '
                    'FROM analysis_run r WHERE r.mission_id = %s ORDER BY r.run_id DESC LIMIT %s',
                    (mission_id, limit))
        items = []
        for row in cur.fetchall():
            (run, kind, status, as_of, version, digest, query, upstream, model,
             started, finished, error, notes, code) = row
            items.append({'run_id': run, 'kind': kind, 'status': status,
                          'as_of_date': as_of.isoformat(), 'methodology_version': version,
                          'methodology_hash': digest, 'query_version_id': query,
                          'upstream_run_id': upstream, 'embedding_model': model,
                          'started_at': started.isoformat(),
                          'finished_at': finished.isoformat() if finished else None,
                          'error': error, 'code_version': code,
                          'quality_generation_id': (notes or {}).get('quality_generation_id')})
    return {'mission_id': mission_id, 'title': mission[0], 'runs': items, 'limit': limit}


def analysis_period(cur, run_id: int) -> tuple[date | None, date, str]:
    """Границы корпуса конкретного прогона, не последнего запроса миссии."""
    cur.execute('SELECT r.as_of_date, q.payload, m.period_from, m.period_to, r.notes '
                'FROM analysis_run r JOIN query_version q USING (query_version_id) '
                'JOIN mission m ON m.mission_id = r.mission_id WHERE r.run_id = %s', (run_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError('Прогон для определения границ корпуса отсутствует.')
    as_of, payload, legacy_start, legacy_to, notes = row
    pinned = (notes or {}).get('input_period')
    period = pinned or (payload or {}).get('period')
    if period:
        start = date.fromisoformat(period['from']) if period.get('from') else None
        inclusive_to = date.fromisoformat(period['to']) if period.get('to') else None
        origin = 'run_snapshot' if pinned else 'pinned_query_version'
    else:
        start, inclusive_to = legacy_start, legacy_to
        origin = 'legacy_mission_fallback'
    end = min(as_of, inclusive_to + timedelta(days=1)) if inclusive_to else as_of
    if start and start >= end:
        raise ValueError('Период корпуса пуст или противоречит дате расчёта.')
    return start, end, origin


def source_tree_version() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    paths: list[Path] = []
    for directory in ("saia", "scripts", "config", "missions", "migrations", 'infra'):
        paths.extend(
            path for path in (root / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    paths.append(root / "pyproject.toml")
    if (root / '.dockerignore').exists():
        paths.append(root / '.dockerignore')
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return f"tree:{digest.hexdigest()[:12]}"


def code_version() -> str:
    """Отпечаток кода. Одного паспорта мало: поведение задаёт и реализация."""
    env = os.environ.get("SAIA_CODE_VERSION")
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        commit = out.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--",
             "saia", "scripts", "config", "missions", "migrations", "infra", ".dockerignore", "pyproject.toml"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        if dirty:
            return f"{commit}+{source_tree_version()}"
        return commit or source_tree_version()
    except Exception:
        # Архив без .git всё равно получает воспроизводимый отпечаток кода.
        return source_tree_version()


def start_run(cur, mission_id: str, kind: str, config: Methodology,
              embedding_model: str | None = None,
              notes: dict[str, Any] | None = None,
              upstream_run_id: int | None = None,
              window_step: str | None = None,
              clustering_scale: str | None = None,
              query_version_id: str | None = None) -> int:
    """Открыть прогон и вернуть его идентификатор."""
    cur.execute("SELECT as_of_date FROM mission WHERE mission_id = %s", (mission_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"миссии {mission_id} нет в базе")
    as_of = row[0]

    # Производный прогон наследует запрос конкретного входа. Новый запрос
    # миссии не имеет отношения к уже построенному старому корпусу.
    if upstream_run_id is not None:
        cur.execute(
            "SELECT query_version_id, as_of_date FROM analysis_run "
            "WHERE run_id = %s AND mission_id = %s AND status = 'done'",
            (upstream_run_id, mission_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('Входной прогон отсутствует, не завершён или принадлежит другой миссии.')
        inherited_query, upstream_as_of = row
        if query_version_id is not None and query_version_id != inherited_query:
            raise ValueError('Нельзя заменить версию запроса входного прогона.')
        query_version_id = inherited_query
        if upstream_as_of != as_of:
            raise ValueError('Дата входного прогона не совпадает с датой миссии; пересоберите цепочку.')
    elif query_version_id is None:
        cur.execute(
            "SELECT query_version_id FROM query_version WHERE mission_id = %s "
            "ORDER BY version DESC LIMIT 1",
            (mission_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"у миссии {mission_id} нет ни одной версии запроса")
        query_version_id = row[0]
    else:
        cur.execute('SELECT 1 FROM query_version WHERE query_version_id = %s AND mission_id = %s',
                    (query_version_id, mission_id))
        if not cur.fetchone():
            raise ValueError('Версия запроса не принадлежит миссии.')

    record_notes = dict(notes or {})
    record_notes['runtime'] = runtime_snapshot()
    provenance = config.provenance()
    cur.execute(
        """
        INSERT INTO analysis_run
            (mission_id, query_version_id, as_of_date, kind,
             methodology_version, methodology_hash,
             gates_configuration, scoring_configuration, window_step,
             embedding_model, code_version, status, notes, upstream_run_id,
             collection_batch_id, clustering_scale)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'created', %s, %s, %s, %s)
        RETURNING run_id
        """,
        (mission_id, query_version_id, as_of, kind,
         provenance["methodology_version"], provenance["methodology_hash"],
         provenance["gates_configuration"], provenance["scoring_configuration"],
         window_step or provenance["window_step"], embedding_model, code_version(),
         Jsonb(record_notes), upstream_run_id, record_notes.get('collection_batch_id'), clustering_scale),
    )
    return cur.fetchone()[0]


def finish_run(cur, run_id: int, status: str = "done", error: str | None = None) -> None:
    """Закрыть прогон. Только завершённый прогон становится текущим."""
    cur.execute(
        "UPDATE analysis_run SET status = %s, finished_at = now(), error = %s "
        "WHERE run_id = %s",
        (status, error, run_id),
    )


def current_run_id(cur, mission_id: str, kind: str) -> int | None:
    """Последний завершённый прогон этого вида."""
    cur.execute(
        "SELECT run_id FROM current_run WHERE mission_id = %s AND kind = %s",
        (mission_id, kind),
    )
    row = cur.fetchone()
    return row[0] if row else None
