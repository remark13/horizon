"""Read-only audit of frozen OpenAlex enrichment; no scientific labels."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from saia.ingest import collection_content_hash, collection_coverage, validate_collection_input
from saia.research_identity import author_identity_disagreement

POLICY = 'source-metadata-audit-0.4.2'


def summarize(rows: list[dict]) -> dict:
    by_doi = defaultdict(list)
    ids = [r['id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Повтор OpenAlex ID в пакете: сначала проверьте пагинацию.')
    for row in rows:
        if row.get('doi'):
            by_doi[row['doi'].strip().lower()].append(row)
    duplicates = []
    for doi, group in sorted(by_doi.items()):
        if len(group) < 2:
            continue
        dates = {r.get('publication_date') for r in group if r.get('publication_date')}
        duplicates.append({'doi': doi, 'records': len(group),
                           'author_identity_conflict': author_identity_disagreement(group),
                           'publication_date_disagreement': len(dates) > 1,
                           'evidence': [{'openalex_url': r['id'], 'title': r.get('title'),
                                         'publication_date': r.get('publication_date'),
                                         'author_ids': [(a.get('author') or {}).get('id')
                                                        for a in r.get('authorships') or []]}
                                        for r in group]})
    return {'returned_records': len(rows), 'unique_openalex_ids': len(set(ids)),
            'unique_dois': len(by_doi), 'records_without_doi': sum(not r.get('doi') for r in rows),
            'duplicate_doi_groups': len(duplicates),
            'extra_records_for_same_doi': sum(d['records'] - 1 for d in duplicates),
            'author_identity_conflict_groups': sum(d['author_identity_conflict'] for d in duplicates),
            'publication_date_disagreement_groups': sum(d['publication_date_disagreement'] for d in duplicates),
            'duplicate_groups': duplicates}


def audit(root: Path) -> dict:
    root = root.resolve()
    manifest_bytes = (root / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    filename = manifest['mission_snapshot_file']
    mission_path = root / filename
    if Path(filename).name != filename or not mission_path.resolve().is_relative_to(root):
        raise ValueError('Миссия должна находиться внутри пакета.')
    mission_text = mission_path.read_text(encoding='utf-8')
    validate_collection_input(root, manifest, json.loads(mission_text), mission_text)
    block = manifest['sources'].get('openalex')
    if not block:
        raise ValueError('Нет источника OpenAlex.')
    rows = []
    for entry in block['files']:
        if not entry.get('file'):
            continue
        payload = json.loads((root / 'openalex' / entry['file']).read_bytes())
        page = payload.get('results')
        if not isinstance(page, list) or len(page) != entry['records']:
            raise ValueError('Количество записей страницы не совпало с manifest.')
        rows.extend(page)
    if len(rows) != block['total_records']:
        raise ValueError('Суммарное количество записей не совпало с manifest.')
    coverage = collection_coverage(manifest)
    return {'policy_version': POLICY, 'audit_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'mission_id': manifest['mission_id'], 'query_version': manifest['query_version'],
            'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
            'collection_sha256': collection_content_hash(manifest),
            'mission_sha256': manifest['mission_file_sha256'],
            'partial': bool(coverage['incomplete'] or coverage['source_errors']),
            'coverage': coverage, 'observations': summarize(rows),
            'scientific_labels': None,
            'limitations': ['Несколько OpenAlex ID одного DOI не являются независимыми публикациями.',
                            'Расхождение авторских ID требует проверки, но не доказывает отдельные команды.',
                            'Разные даты могут относиться к журналу и препринту: первая дата исследования не подтверждена.',
                            'Покрытие всего направления и историческая независимость организаций не установлены.',
                            'Аудит метаданных не размечает слабые сигналы, шум или рыночный успех.']}


def main() -> None:
    parser = argparse.ArgumentParser(description='Аудит исходного пакета без изменения данных и статусов')
    parser.add_argument('package', type=Path)
    parser.add_argument('--export', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.package)
    with args.export.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report['observations'].items() if k != 'duplicate_groups'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
