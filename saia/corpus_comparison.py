"""Compare exact normalized generations by arXiv identity, not topic names.

Read-only inventory before model reuse or a comparative experiment. Equal
identity does not establish equal model input: text equality is separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from saia import db, hybrid, runs
from saia.embed import build_text

VERSION = 'canonical-corpus-comparison-0.4.2'


def text_fingerprint(row):
    text, source = build_text(row['title'], row['abstract'])
    return {'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'text_source': source, 'char_count': len(text)}


def index(rows):
    by_id = defaultdict(list)
    for row in rows:
        for arxiv_id in row['arxiv_ids']:
            by_id[arxiv_id].append(row)
    return by_id


def compare(baseline, target):
    old, new = index(baseline), index(target)
    common = sorted(set(old) & set(new))
    observations = []
    ambiguous = []
    for identifier in common:
        if len(old[identifier]) != 1 or len(new[identifier]) != 1:
            ambiguous.append({'arxiv_id': identifier,
                              'baseline_work_ids': sorted(r['work_id'] for r in old[identifier]),
                              'target_work_ids': sorted(r['work_id'] for r in new[identifier])})
            continue
        a, b = old[identifier][0], new[identifier][0]
        af, bf = text_fingerprint(a), text_fingerprint(b)
        observations.append({'arxiv_id': identifier, 'arxiv_url': 'https://arxiv.org/abs/' + identifier,
                             'baseline_work_id': a['work_id'], 'target_work_id': b['work_id'],
                             'same_embedding_text': af == bf,
                             'baseline_text': af, 'target_text': bf,
                             'baseline_effective_date': a['effective_date'],
                             'target_effective_date': b['effective_date'],
                             'date_changed': a['effective_date'] != b['effective_date'],
                             'baseline_sources': a['sources'], 'target_sources': b['sources']})
    return {'baseline_canonical_works': len(baseline), 'target_canonical_works': len(target),
            'baseline_arxiv_ids': len(old), 'target_arxiv_ids': len(new),
            'shared_arxiv_ids': len(common), 'unambiguous_pairs': len(observations),
            'ambiguous_pairs': ambiguous, 'missing_in_target': sorted(set(old) - set(new)),
            'added_in_target': sorted(set(new) - set(old)),
            'same_embedding_text_pairs': sum(o['same_embedding_text'] for o in observations),
            'changed_embedding_text_pairs': sum(not o['same_embedding_text'] for o in observations),
            'changed_date_pairs': sum(o['date_changed'] for o in observations),
            'baseline_multi_arxiv_works': [r['work_id'] for r in baseline if len(r['arxiv_ids']) > 1],
            'target_multi_arxiv_works': [r['work_id'] for r in target if len(r['arxiv_ids']) > 1],
            'observations': observations}


def read_corpus(cur, run_id):
    cur.execute('SELECT mission_id, query_version_id, status, kind, notes, code_version '
                'FROM analysis_run WHERE run_id = %s', (run_id,))
    row = cur.fetchone()
    if not row or row[2:4] != ('done', 'normalize'):
        raise ValueError('Нужен точный ID завершённой нормализации.')
    provenance = {'run_id': run_id, 'mission_id': row[0], 'query_version_id': row[1],
                  'notes': row[4], 'normalization_code_version': row[5]}
    cur.execute('SELECT w.work_id, w.canonical_title, w.abstract, w.effective_date, '
                "array_agg(DISTINCT i.value ORDER BY i.value) FILTER (WHERE i.kind = 'arxiv'), "
                'array_agg(DISTINCT v.source ORDER BY v.source) '
                'FROM work w LEFT JOIN identifier i ON i.work_id = w.work_id '
                'LEFT JOIN work_version v ON v.work_id = w.work_id '
                'WHERE w.run_id = %s GROUP BY w.work_id ORDER BY w.work_id', (run_id,))
    rows = [{'work_id': i, 'title': title, 'abstract': abstract, 'effective_date': day.isoformat(),
             'arxiv_ids': ids or [], 'sources': sources or []}
            for i, title, abstract, day, ids, sources in cur.fetchall()]
    provenance['canonical_input_sha256'] = hybrid.digest(rows)
    provenance['source_compositions'] = dict(Counter('+'.join(r['sources']) for r in rows))
    return rows, provenance


def analyze(baseline_run_id, target_run_id):
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        old, old_p = read_corpus(cur, baseline_run_id)
        new, new_p = read_corpus(cur, target_run_id)
    result = {'version': VERSION, 'code_version': runs.code_version(),
              'baseline_provenance': old_p, 'target_provenance': new_p,
              'comparison': compare(old, new), 'scientific_labels': None,
              'limitations': ['Совпадение текста проверено точно, без исправления пробелов или замены аннотации.',
                              'Совпадение текста ещё не разрешение копировать вектор: требуется та же модель и проверенный источник вектора.',
                              'Даты — канонические решения алгоритма, не подтверждённое рождение исследования.',
                              'Добавление OpenAlex не является независимым поиском или проверкой исторических авторов.',
                              'Сравнение корпуса не измеряет качество детектора слабых сигналов.']}
    result['report_sha256'] = hybrid.digest(result)
    return result


def main():
    parser = argparse.ArgumentParser(description='Сравнение неизменных нормализованных корпусов')
    parser.add_argument('baseline_run_id', type=int)
    parser.add_argument('target_run_id', type=int)
    parser.add_argument('--export', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.baseline_run_id, args.target_run_id)
    with args.export.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result['comparison'].items()
                      if k not in ('observations', 'ambiguous_pairs')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
