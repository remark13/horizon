"""CPU-генератор буквальных фраз с проверяемыми контекстами.

Открытый собственный baseline, не реализация закрытой iFORA. Фразы
считаются по документам, а не по числу повторов в длинной аннотации.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

from saia.measurement import WindowCounts, analyze_series
from saia.cluster import contiguous_windows, window_bounds, window_of

POLICY_PATH = Path(__file__).resolve().parents[1] / 'config' / 'terminology.v0.4.yaml'
TOKEN = re.compile(r'[^\W\d_]+(?:-[^\W\d_]+)*', re.UNICODE)


@dataclass(frozen=True)
class PublicationText:
    work_id: int
    published_at: date
    title: str
    abstract: str | None = None


@contextmanager
def phrase_index(docs: list[PublicationText], policy: dict):
    """Exact document frequencies, optionally on disk; no sampling or pruning.

    The temporary database stores aggregate counts only. It is discarded after
    selection; final selected memberships and original offsets use a second pass.
    """
    storage = policy.get('frequency_storage', 'memory')
    if storage not in ('memory', 'sqlite'):
        raise ValueError('Неизвестный способ хранения частот фраз.')
    def records(doc):
        title = {p for p, _, _ in phrase_spans(doc.title, policy)}
        phrases = title | {p for p, _, _ in phrase_spans(doc.abstract or '', policy)}
        return title, phrases
    if storage == 'memory':
        index = {}
        for doc in docs:
            title, phrases = records(doc)
            for p in phrases:
                row = index.setdefault(p, [0, 0, doc.published_at, doc.published_at])
                row[0] += 1
                row[1] += p in title
                row[3] = doc.published_at
        yield lambda: ((p, *v) for p, v in index.items())
        return
    with tempfile.TemporaryDirectory(prefix='saia-phrase-index-') as folder:
        conn = sqlite3.connect(str(Path(folder) / 'frequencies.sqlite'))
        try:
            conn.execute('CREATE TABLE frequency (phrase TEXT PRIMARY KEY, n INTEGER NOT NULL, '
                         'title_n INTEGER NOT NULL, first_day TEXT NOT NULL, last_day TEXT NOT NULL)')
            for doc in docs:
                title, phrases = records(doc)
                day = doc.published_at.isoformat()
                conn.executemany('INSERT INTO frequency VALUES (?, 1, ?, ?, ?) '
                                 'ON CONFLICT(phrase) DO UPDATE SET n=n+1, '
                                 'title_n=title_n+excluded.title_n, last_day=excluded.last_day',
                                 ((p, int(p in title), day, day) for p in phrases))
            conn.commit()
            def rows():
                for p, n, title_n, first_day, last_day in conn.execute('SELECT * FROM frequency'):
                    yield p, n, title_n, date.fromisoformat(first_day), date.fromisoformat(last_day)
            yield rows
        finally:
            conn.close()


def phrase_spans(text: str, policy: dict) -> list[tuple[str, int, int]]:
    """Не перескакивать через стоп-слова, числа и границы предложений."""
    tokens = list(TOKEN.finditer(text))
    stop = set(policy['stopwords'])
    lengths = policy['ngram_lengths']
    result = []
    for start_index in range(len(tokens)):
        for length in lengths:
            group = tokens[start_index:start_index + length]
            if len(group) != length:
                continue
            words = [m.group().lower() for m in group]
            if any(w in stop or len(w) > policy['max_token_chars'] for w in words):
                continue
            if any(not text[a.end():b.start()].isspace() for a, b in zip(group, group[1:])):
                continue
            result.append((' '.join(words), group[0].start(), group[-1].end()))
    return result


def generate(documents: list[PublicationText], as_of: date, step: str = 'year',
             coverage_comparable: bool | None = False, policy: dict | None = None,
             period_from: date | None = None, period_end: date | None = None) -> dict:
    policy = policy or yaml.safe_load(POLICY_PATH.read_text())
    if step not in ('year', 'quarter', 'month'):
        raise ValueError('Поддерживаются окна year, quarter или month.')
    if step == 'month' and policy.get('window_step') != 'month':
        raise ValueError('Для month нужна отдельная месячная policy; прежняя policy поддерживает year/quarter.')
    observation_end = period_end or as_of
    if observation_end > as_of or (period_from and period_from >= observation_end):
        raise ValueError('Период противоречит дате расчёта.')
    if step == 'month' and period_from and period_from.day != 1:
        raise ValueError('Месячный период должен начинаться с первого числа.')
    if any(not isinstance(w, str) for w in policy['stopwords']):
        raise ValueError('Стоп-слова должны быть строками; проверьте кавычки YAML.')
    if (not policy['ngram_lengths'] or any(n not in (2, 3, 4) for n in policy['ngram_lengths'])
            or policy['min_document_support'] < 1
            or any(policy[k] < 1 for k in ('max_candidates', 'max_singletons',
                                          'max_contexts_per_phrase', 'context_radius_chars'))):
        raise ValueError('Некорректный паспорт терминологического генератора.')
    # Проверка даты предшествует извлечению: будущие термины не попадают
    # ни в словарь, ни в подсчёт знаменателя, ни в ранжирование.
    docs = sorted((d for d in documents if d.published_at < observation_end
                   and (period_from is None or d.published_at >= period_from)),
                  key=lambda d: (d.published_at, d.work_id))
    if len(docs) > policy['max_corpus_works']:
        raise ValueError('Корпус превышает лимит CPU-baseline; нужен пакетный индекс, а не скрытая выборка.')
    if any(len(d.title) + len(d.abstract or '') > policy['max_document_chars'] for d in docs):
        raise ValueError('Текст документа превышает лимит; проверьте исходную запись.')
    if len({d.work_id for d in docs}) != len(docs):
        raise ValueError('Одна каноническая работа передана несколько раз.')
    # Первый проход хранит только документную частоту. Контексты миллиона
    # случайных сочетаний нельзя держать в памяти ради вывода 100 фраз.
    selection = policy.get('selection', 'support')
    if selection not in ('support', 'rare_recent_title'):
        raise ValueError('Неизвестный способ отбора фраз.')
    minimum = policy['min_document_support']
    if selection == 'rare_recent_title':
        maximum, recent_windows = policy['max_document_support'], policy['recent_birth_windows']
        if maximum < minimum or recent_windows < 1 or policy['min_title_support'] < 1:
            raise ValueError('Некорректные ограничения редких фраз.')
        last_key = window_of(observation_end - timedelta(days=1), step)
        def ordinal(day):
            return (day.year if step == 'year' else day.year * 12 + day.month - 1 if step == 'month'
                    else day.year * 4 + (day.month - 1) // 3)
        recent_start = ordinal(observation_end - timedelta(days=1)) - recent_windows + 1
        qualifies = lambda r: (minimum <= r[1] <= maximum
                              and r[2] >= policy['min_title_support']
                              and ordinal(r[3]) >= recent_start
                              and window_of(r[4], step) == last_key)
        key = lambda r: (-r[3].toordinal(), -r[2], -r[1], r[0])
    else:
        qualifies = lambda r: r[1] >= minimum
        key = lambda r: (-r[1], r[0])
    with phrase_index(docs, policy) as rows:
        candidate_total = low_support_total = selected_total = 0
        for r in rows():
            candidate_total += r[1] >= minimum
            low_support_total += r[1] < minimum
            selected_total += qualifies(r)
        chosen = heapq.nsmallest(policy['max_candidates'], (r for r in rows() if qualifies(r)), key=key)
        low_chosen = heapq.nsmallest(policy['max_singletons'], (r for r in rows() if r[1] < minimum),
                                   key=lambda r: (-r[1], r[0]))
    supported = [r[0] for r in chosen]
    singletons = [r[0] for r in low_chosen]
    title_support = {r[0]: r[2] for r in chosen + low_chosen}
    selected = set(supported + singletons)
    membership: dict[str, set[int]] = defaultdict(set)
    contexts: dict[str, list[dict]] = defaultdict(list)
    by_id = {d.work_id: d for d in docs}
    radius = policy['context_radius_chars']
    for doc in docs:
        seen = set()
        for field, text in (('title', doc.title), ('abstract', doc.abstract or '')):
            for phrase, start, end in phrase_spans(text, policy):
                if phrase not in selected:
                    continue
                membership[phrase].add(doc.work_id)
                if phrase in seen or len(contexts[phrase]) >= policy['max_contexts_per_phrase']:
                    continue
                seen.add(phrase)
                contexts[phrase].append({
                    'work_id': doc.work_id, 'published_at': doc.published_at.isoformat(),
                    'field': field, 'start': start, 'end': end,
                    'matched_text': text[start:end],
                    'context_start': max(0, start - radius),
                    'context': text[max(0, start - radius):min(len(text), end + radius)],
                })
    grid = (contiguous_windows(window_of(period_from if step == 'month' and period_from else docs[0].published_at, step),
                              window_of(observation_end - timedelta(days=1), step), step) if docs else [])
    corpus_counts = Counter(window_of(d.published_at, step) for d in docs)

    def card(phrase: str) -> dict:
        ids = membership[phrase]
        counts = Counter(window_of(by_id[i].published_at, step) for i in ids)
        windows = []
        for key in grid:
            start, inclusive_end = window_bounds(key, step)
            end = inclusive_end + timedelta(days=1)
            windows.append(WindowCounts(start, min(end, observation_end), counts[key], corpus_counts[key],
                                        end <= observation_end, coverage_comparable))
        result = {
            'phrase': phrase, 'document_support': len(ids), 'work_ids': sorted(ids),
            'first_observed_in_corpus': min(by_id[i].published_at for i in ids).isoformat(),
            'contexts': contexts[phrase], 'publication_series': analyze_series(windows, as_of),
            'status': 'term_candidate' if len(ids) >= policy['min_document_support'] else 'low_support_review',
        }
        if selection == 'rare_recent_title':
            result['title_document_support'] = title_support[phrase]
        return result

    digest = hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    input_hash = hashlib.sha256(json.dumps([
        [d.work_id, d.published_at.isoformat(), d.title, d.abstract] for d in docs
    ], ensure_ascii=False).encode()).hexdigest()
    return {
        'generator_version': policy['version'], 'policy_hash': digest,
        'input_text_hash': input_hash, 'effective_policy': policy,
        'as_of_date': as_of.isoformat(), 'window_step': step, 'corpus_works': len(docs),
        'period_from': period_from.isoformat() if period_from else None,
        'period_end_exclusive': observation_end.isoformat(),
        'corpus_work_ids': [d.work_id for d in docs],
        'candidate_phrases_total': candidate_total, 'low_support_phrases_total': low_support_total,
        'selection': selection, 'selection_eligible_phrases': selected_total,
        'candidates': [card(p) for p in supported],
        'low_support_review': [card(p) for p in singletons],
        'limitations': [policy['interpretation'],
                        'Поддержка не доказывает независимость групп или техническое преимущество.',
                        'Покрытие источников должно быть проверено отдельно; по умолчанию рост не установлен.',
                        'Список ограничен по числу фраз; порядок задаёт паспорт отбора, не перспективность.'],
    }


def read_corpus(cur, mission_id: str, quality_generation_id: int | None = None) -> tuple:
    """Общий неизменяемый вход терминов и управляемого расширения запроса."""
    from saia import runs
    if quality_generation_id is None:
        normal_run = runs.current_run_id(cur, mission_id, 'normalize')
        if normal_run is None:
            raise ValueError('Нет завершённого нормализованного корпуса.')
        cur.execute("SELECT generation_id FROM quality_generation WHERE normalize_run_id = %s "
                    "AND status = 'done' ORDER BY generation_id DESC LIMIT 1", (normal_run,))
        row = cur.fetchone()
        if not row:
            raise ValueError('Сначала выполните оценку качества корпуса.')
        quality_generation_id = row[0]
    cur.execute("SELECT r.run_id, r.as_of_date, r.query_version_id, r.notes, q.methodology_hash "
                "FROM quality_generation q JOIN analysis_run r ON r.run_id = q.normalize_run_id "
                "WHERE q.generation_id = %s AND q.status = 'done' AND r.mission_id = %s "
                "AND r.status = 'done' AND r.kind = 'normalize'", (quality_generation_id, mission_id))
    row = cur.fetchone()
    if not row:
        raise ValueError('Поколение качества отсутствует или принадлежит другой миссии.')
    normal_run, as_of, query_id, notes, quality_hash = row
    batch_id = (notes or {}).get('collection_batch_id')
    cur.execute('SELECT coverage FROM collection_batch WHERE batch_id = %s', (batch_id,))
    coverage_row = cur.fetchone()
    from saia.coverage_passport import resolve as resolve_coverage_passport
    comparable, coverage_passport_id = resolve_coverage_passport(
        cur, batch_id, coverage_row[0] if coverage_row else {}
    )
    comparable = comparable is True
    start, end, origin = runs.analysis_period(cur, normal_run)
    cur.execute('SELECT w.work_id, w.effective_date, w.canonical_title, w.abstract '
                'FROM work w JOIN quality_snapshot q USING (work_id) '
                "WHERE w.run_id = %s AND q.generation_id = %s AND q.decision = 'include' "
                'AND w.effective_date < %s AND (%s::date IS NULL OR w.effective_date >= %s)',
                (normal_run, quality_generation_id, end, start, start))
    docs = [PublicationText(*r) for r in cur.fetchall()]
    provenance = {'mission_id': mission_id, 'normalize_run_id': normal_run,
                  'quality_generation_id': quality_generation_id, 'query_version_id': query_id,
                  'collection_batch_id': batch_id, 'quality_methodology_hash': quality_hash,
                  'coverage_passport_id': coverage_passport_id,
                  'corpus_period_origin': origin, 'code_version': runs.code_version()}
    return docs, as_of, start, end, comparable, provenance


def attach_context_sources(cur, result: dict) -> None:
    from saia.candidates import source_links
    for item in result['candidates'] + result['low_support_review']:
        for context in item['contexts']:
            cur.execute('SELECT source, source_record_id FROM work_version WHERE work_id = %s '
                        'ORDER BY source, source_record_id', (context['work_id'],))
            context['source_records'] = [{'source': s, 'id': i} for s, i in cur.fetchall()]
            cur.execute('SELECT kind, value FROM identifier WHERE work_id = %s '
                        'ORDER BY kind, value', (context['work_id'],))
            context['sources'] = source_links(cur.fetchall())


def analyze_mission(mission_id: str, quality_generation_id: int | None = None,
                    policy: dict | None = None) -> dict:
    from saia import db, runs, methodology
    with db.connect() as conn, conn.cursor() as cur:
        docs, as_of, start, end, comparable, provenance = read_corpus(cur, mission_id, quality_generation_id)
        result = generate(docs, as_of, methodology.load_default().window_step, comparable,
                          policy=policy, period_from=start, period_end=end)
        result['provenance'] = provenance
        attach_context_sources(cur, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Фразы с контекстами, не автоматические тренды')
    parser.add_argument('mission_id')
    parser.add_argument('--quality-generation', type=int, default=None)
    parser.add_argument('--policy', type=Path, help='Явная версия паспорта генератора')
    parser.add_argument('--export', required=True)
    args = parser.parse_args()
    policy = yaml.safe_load(args.policy.read_text()) if args.policy else None
    result = analyze_mission(args.mission_id, args.quality_generation, policy)
    Path(args.export).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print({k: result[k] for k in ('corpus_works', 'candidate_phrases_total', 'low_support_phrases_total')})
    print(f'Контексты и паспорт: {args.export}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
