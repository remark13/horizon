"""Управляемое расширение: предложение → выбор → неизменяемая версия.

Без скрытого целевого словаря, LLM-перевода или автоматического изменения
корпуса. Контексты ограничены датой до извлечения терминов.
"""
from __future__ import annotations

import copy
import argparse
import hashlib
import json
import re
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import yaml
from psycopg.types.json import Jsonb

from saia import db, runs, terminology
from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION

POLICY_PATH = Path(__file__).resolve().parents[1] / 'config' / 'query-expansion.v0.4.yaml'


class QueryConflict(ValueError):
    pass


def policy() -> dict:
    return yaml.safe_load(POLICY_PATH.read_text())


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def literal(value: str, cfg: dict | None = None) -> str:
    cfg = cfg or policy()
    # Не принимать пользовательский Boolean как термин и не исправлять
    # его молча: OR/кавычки/поля должны создаваться только компилятором.
    if not isinstance(value, str) or re.search(r'["\\\x00-\x1f:*?()~]', value):
        raise ValueError('Нужна обычная фраза без операторов, кавычек или управляющих символов.')
    value = ' '.join(value.split())
    if not 2 <= len(value) <= cfg['max_literal_chars'] or not re.search(r'[^\W\d_]', value):
        raise ValueError(f"Фраза должна содержать буквы и от 2 до {cfg['max_literal_chars']} символов.")
    return value


def compile_plan(original_query: str, selected_terms: list[str], exclusions: list[str],
                 date_from: date, as_of: date, cfg: dict | None = None) -> dict:
    cfg = cfg or policy()
    original = literal(original_query, cfg)
    if date_from >= as_of or as_of == date.min:
        raise ValueError('Начало периода должно предшествовать дате анализа.')
    if len(selected_terms) > cfg['max_selected_terms'] or len(exclusions) > cfg['max_exclusions']:
        raise ValueError('Превышено допустимое число включений или исключений.')
    included = list(dict.fromkeys([original, *[literal(t, cfg) for t in selected_terms]]))
    excluded = list(dict.fromkeys(literal(t, cfg) for t in exclusions))
    if {t.casefold() for t in included} & {t.casefold() for t in excluded}:
        raise ValueError('Одна фраза не может одновременно включаться и исключаться.')
    openalex = '(' + ' OR '.join(f'"{t}"' for t in included) + ')'
    arxiv = '(' + ' OR '.join(f'all:"{t}"' for t in included) + ')'
    if excluded:
        openalex += ' NOT (' + ' OR '.join(f'"{t}"' for t in excluded) + ')'
        arxiv += ' ANDNOT (' + ' OR '.join(f'all:"{t}"' for t in excluded) + ')'
    if any(len(urlencode({'q': e}).encode()) > cfg['max_encoded_expression_bytes']
           for e in (openalex, arxiv)):
        raise ValueError('Поисковое выражение слишком длинное; сократите список фраз.')
    return {'version': cfg['version'], 'original_query': original,
            'matching_version': ORTHOGRAPHIC_MATCHING_VERSION,
            'effective_policy': cfg, 'policy_hash': digest(cfg),
            'included_terms': included, 'exclusions': excluded,
            'date_from': date_from.isoformat(), 'as_of_date': as_of.isoformat(),
            'logical_expressions': {'openalex': openalex, 'arxiv': arxiv},
            'documentation': cfg['source_documentation'],
            'interpretation': cfg['interpretation']}


def suggest(documents: list[terminology.PublicationText], query: str, as_of: date,
            period_from: date | None = None, period_end: date | None = None) -> dict:
    cfg = policy()
    query = literal(query, cfg)
    end = period_end or as_of
    if end > as_of or (period_from and period_from >= end):
        raise ValueError('Период противоречит дате анализа.')
    docs = [d for d in documents if d.published_at < end
            and (period_from is None or d.published_at >= period_from)]
    pattern = re.compile(r'(?<!\w)' + re.escape(query) + r'(?!\w)', re.IGNORECASE)
    seeds = [d for d in docs if any(pattern.search(text) for text in (d.title, d.abstract or ''))]
    phrase_policy = yaml.safe_load(terminology.POLICY_PATH.read_text())
    phrase_policy['stopwords'] = list(dict.fromkeys(phrase_policy['stopwords'] + cfg['non_technical_tokens']))
    phrase_policy['version'] += '+query-context-clean-1'
    phrase_policy['min_document_support'] = cfg['min_document_support']
    phrase_policy['max_candidates'] = cfg['max_suggestions'] + 1
    terms = terminology.generate(seeds, as_of, policy=phrase_policy,
                                 period_from=period_from, period_end=end)
    terms['candidates'] = [t for t in terms['candidates'] if t['phrase'].casefold() != query.casefold()][:cfg['max_suggestions']]
    for item in terms['candidates']:
        item['suggestion_id'] = digest(item['phrase'])[:16]
        item['relation'] = cfg['relation']
        item['available_before'] = end.isoformat()
        # Этот ряд относится лишь к seed-подкорпусу, не доле всей области.
        item['series_denominator'] = 'publications_containing_original_literal'
    terms['low_support_review'] = []
    return {'original_query': query, 'as_of_date': as_of.isoformat(),
            'period_from': period_from.isoformat() if period_from else None,
            'period_end_exclusive': end.isoformat(), 'eligible_corpus_works': len(docs),
            'seed_works': len(seeds), 'suggestions': terms['candidates'],
            'input_text_hash': terms['input_text_hash'], 'term_policy_hash': terms['policy_hash'],
            'term_generator_version': terms['generator_version'],
            'policy_hash': digest(cfg), 'effective_policy': cfg,
            'warnings': [cfg['interpretation'],
                         'Это предложения по совместной встречаемости, не доказанные синонимы или переводы.',
                         'Точные совпадения могут пропустить другие формы слов и сокращения.',
                         'Загрузка и пересчёт результатов автоматически не запускаются.'] + (
                ['Исходная фраза не найдена; автоматический перевод или выдуманные термины не предложены.'] if not seeds else [])}


def create_proposal(mission_id: str, query: str,
                    quality_generation_id: int | None = None) -> dict:
    with db.connect() as conn, conn.cursor() as cur:
        docs, as_of, start, end, _, provenance = terminology.read_corpus(cur, mission_id, quality_generation_id)
        result = suggest(docs, query, as_of, start, end)
        terminology.attach_context_sources(cur, {'candidates': result['suggestions'], 'low_support_review': []})
        result['provenance'] = provenance
        result['runtime'] = runs.runtime_snapshot()
        cur.execute('SELECT query_version_id FROM query_version WHERE mission_id = %s ORDER BY version DESC LIMIT 1',
                    (mission_id,))
        result['base_query_version_id'] = cur.fetchone()[0]
        proposal_id = str(uuid.uuid4())
        result['proposal_id'] = proposal_id
        cur.execute('INSERT INTO query_expansion_proposal (proposal_id, mission_id, base_query_version_id, '
                    'normalize_run_id, quality_generation_id, payload, content_sha256) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                    (proposal_id, mission_id, result['base_query_version_id'], provenance['normalize_run_id'],
                     provenance['quality_generation_id'], Jsonb(result), digest(result)))
    return result


def approve(proposal_id: str, selected_ids: list[str], exclusions: list[str], reviewed_by: str) -> dict:
    try:
        uuid.UUID(proposal_id)
    except (ValueError, TypeError):
        raise ValueError('Некорректный номер предложения.') from None
    if not reviewed_by.strip() or len(reviewed_by) > 120:
        raise ValueError('Укажите автора решения (не более 120 символов).')
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT mission_id FROM query_expansion_proposal WHERE proposal_id = %s', (proposal_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError('Предложение не найдено.')
        mission_id = row[0]
        # Одна миссия — один сериализованный выбор номера новой версии.
        cur.execute('SELECT 1 FROM mission WHERE mission_id = %s FOR UPDATE', (mission_id,))
        cur.execute('SELECT payload, content_sha256 FROM query_expansion_proposal WHERE proposal_id = %s FOR UPDATE', (proposal_id,))
        proposal, proposal_hash = cur.fetchone()
        if digest(proposal) != proposal_hash:
            raise ValueError('Отпечаток сохранённого предложения не совпал; решение не сохранено.')
        # Подтверждение следует паспорту предложения, а не новой конфигурации.
        cfg = proposal['effective_policy']
        if not selected_ids or len(selected_ids) > cfg['max_selected_terms'] or len(set(selected_ids)) != len(selected_ids):
            raise ValueError(f"Выберите от одного до {cfg['max_selected_terms']} разных предложений.")
        selected_ids = sorted(selected_ids)
        exclusions = sorted(set(literal(t, cfg) for t in exclusions))
        signature = digest([selected_ids, exclusions, reviewed_by.strip()])
        cur.execute('SELECT query_version_id, selection_sha256 FROM query_expansion_decision WHERE proposal_id = %s', (proposal_id,))
        existing = cur.fetchone()
        if existing:
            if existing[1] != signature:
                raise QueryConflict('Решение уже сохранено с другим выбором; создайте новое предложение.')
            return {'query_version_id': existing[0], 'already_saved': True}
        by_id = {t['suggestion_id']: t for t in proposal['suggestions']}
        if any(i not in by_id for i in selected_ids):
            raise ValueError('Выбран термин, которого нет в сохранённом предложении.')
        provenance = proposal['provenance']
        cur.execute('SELECT query_version_id, version FROM query_version WHERE mission_id = %s '
                    'ORDER BY version DESC LIMIT 1', (mission_id,))
        latest_id, latest_version = cur.fetchone()
        if latest_id != proposal['base_query_version_id']:
            raise QueryConflict('После предложения уже создана новая версия запроса; пересоздайте предложение.')
        cur.execute('SELECT payload FROM query_version WHERE query_version_id = %s', (latest_id,))
        payload = copy.deepcopy(cur.fetchone()[0])
        start = date.fromisoformat(proposal['period_from']) if proposal['period_from'] else None
        if start is None:
            raise ValueError('Неизвестно начало периода: сначала закрепите границы корпуса.')
        # Дата периода может быть раньше даты миссии; ограничение берётся
        # из сохранённого входа, а не из изменяемой текущей миссии.
        end = date.fromisoformat(proposal['period_end_exclusive'])
        plan = compile_plan(proposal['original_query'], [by_id[i]['phrase'] for i in selected_ids], exclusions, start, end, proposal['effective_policy'])
        new_id = f'{mission_id}/v{latest_version + 1}'
        payload.update({'query_version': new_id, 'expansion_source': 'mixed',
                        'controlled_search_plan': plan,
                        'query_expansion': {'proposal_id': proposal_id, 'base_query_version_id': latest_id,
                                            'selection_sha256': signature, 'selected_ids': selected_ids,
                                            'reviewed_by': reviewed_by.strip(), 'provenance': provenance}})
        payload['query'] = {'terms': plan['included_terms'], 'exclusions': plan['exclusions']}
        payload['as_of_date'] = plan['as_of_date']
        from datetime import timedelta
        payload['period'] = {'from': plan['date_from'],
                             'to': (end - timedelta(days=1)).isoformat()}
        cur.execute('INSERT INTO query_version (query_version_id, mission_id, version, terms, exclusions, '
                    'expansion_source, payload, content_sha256) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                    (new_id, mission_id, latest_version + 1, plan['included_terms'], plan['exclusions'], 'mixed', Jsonb(payload), digest(payload)))
        cur.execute('INSERT INTO query_expansion_decision (proposal_id, query_version_id, selection_sha256, '
                    'selected_ids, exclusions, reviewed_by) VALUES (%s, %s, %s, %s, %s, %s)',
                    (proposal_id, new_id, signature, selected_ids, plan['exclusions'], reviewed_by.strip()))
    return {'query_version_id': new_id, 'already_saved': False, 'search_plan': plan,
            'warning': 'Корпус и старые карточки не изменены; новый поиск пока является ограниченным предпросмотром.'}


def saved_plan(query_version_id: str) -> dict:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT q.payload FROM query_version q JOIN query_expansion_decision d USING (query_version_id) '
                    'WHERE query_version_id = %s', (query_version_id,))
        row = cur.fetchone()
    if not row:
        raise ValueError('Нет подтверждённой управляемой версии запроса.')
    return row[0]['controlled_search_plan']


def get_proposal(proposal_id: str) -> dict:
    try:
        uuid.UUID(proposal_id)
    except (ValueError, TypeError):
        raise ValueError('Некорректный номер предложения.') from None
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT payload, content_sha256 FROM query_expansion_proposal WHERE proposal_id = %s', (proposal_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError('Предложение не найдено.')
        result = row[0]
        if digest(result) != row[1]:
            raise ValueError('Отпечаток сохранённого предложения не совпал.')
        result['proposal_content_sha256'] = row[1]
        cur.execute('SELECT query_version_id, selected_ids, exclusions, reviewed_by, created_at '
                    'FROM query_expansion_decision WHERE proposal_id = %s', (proposal_id,))
        decision = cur.fetchone()
        result['decision'] = ({'query_version_id': decision[0], 'selected_ids': decision[1],
                               'exclusions': decision[2], 'reviewed_by': decision[3],
                               'created_at': decision[4].isoformat()} if decision else None)
    return result


def read_approved(query_version_id: str) -> dict:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT proposal_id FROM query_expansion_decision WHERE query_version_id = %s', (query_version_id,))
        row = cur.fetchone()
    if not row:
        raise ValueError('Подтверждённая версия запроса не найдена.')
    result = get_proposal(str(row[0]))
    result['search_plan'] = saved_plan(query_version_id)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Сохранить предложение терминов; не менять корпус и не запускать поиск')
    parser.add_argument('mission_id', nargs='?')
    parser.add_argument('--query', default=None)
    parser.add_argument('--read-version', default=None)
    parser.add_argument('--quality-generation', type=int, default=None)
    parser.add_argument('--export', default=None)
    args = parser.parse_args()
    if args.read_version:
        result = read_approved(args.read_version)
    else:
        if not args.mission_id or not args.query:
            parser.error('Для предложения нужны mission_id и --query; для чтения — --read-version.')
        result = create_proposal(args.mission_id, args.query, args.quality_generation)
    if args.export:
        Path(args.export).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print({k: result[k] for k in ('proposal_id', 'base_query_version_id', 'eligible_corpus_works', 'seed_works')})
    print(f"Предложений: {len(result['suggestions'])}; этот вызов не запускал поиск.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
