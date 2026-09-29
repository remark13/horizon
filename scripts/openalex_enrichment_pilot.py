"""Budget-limited enrichment probe, never a complete analytical field corpus.

python -m scripts.openalex_enrichment_pilot BASE_PACKAGE --out NEW_DIRECTORY
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from scripts import fetch
from saia.ingest import validate_collection_input


def collect(base_root: Path, output: Path, max_batches: int = 2, batch_size: int = 50,
            full_enrichment: bool = False) -> dict:
    base_root, output = base_root.resolve(), output.resolve()
    if output == base_root or output.is_relative_to(base_root) or base_root.is_relative_to(output):
        raise ValueError('Пилот должен находиться рядом, не внутри исходного пакета.')
    if not isinstance(max_batches, int) or isinstance(max_batches, bool) or max_batches < 1:
        raise ValueError('Лимит пакетов пилота должен быть положительным целым.')
    if not 1 <= batch_size <= 100:
        raise ValueError('Пакет DOI должен содержать 1–100 ID.')
    manifest_bytes = (base_root / 'manifest.json').read_bytes()
    base_manifest = json.loads(manifest_bytes)
    filename = base_manifest['mission_snapshot_file']
    frozen_base = base_root / filename
    if Path(filename).name != filename or not frozen_base.resolve().is_relative_to(base_root):
        raise ValueError('Сохранённая миссия должна находиться внутри исходного пакета.')
    mission_bytes = frozen_base.read_bytes()
    base_mission = json.loads(mission_bytes)
    validate_collection_input(base_root, base_manifest, base_mission, mission_bytes.decode('utf-8'))
    if base_manifest.get('incomplete') or base_manifest.get('source_errors') or 'arxiv' not in base_manifest['sources']:
        raise ValueError('Нужен завершённый arXiv-пакет с проверенными файлами.')
    mission_id = base_mission['mission_id'] + ('-openalex-enriched' if full_enrichment else '-openalex-pilot')
    mission = {**base_mission, 'mission_id': mission_id, 'query_version': mission_id + '/v1',
               'sources': ['arxiv', 'openalex'] if full_enrichment else ['openalex'],
               'collection_stage': 'frozen_arxiv_plus_doi_enrichment' if full_enrichment else 'budget_limited_enrichment_probe_not_field_corpus',
               'query': {**base_mission['query'], 'openalex_enrich_arxiv_ids': {
                   'max_ids': 30000, 'batch_size': batch_size, 'max_batches': max_batches, 'max_pages_per_batch': 10}},
               'base_package': {'mission_id': base_mission['mission_id'],
                                'query_version': base_mission['query_version'],
                                'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                                'mission_sha256': hashlib.sha256(mission_bytes).hexdigest()},
               'sampling': 'all_base_arxiv_ids_no_target_labels' if full_enrichment else 'first_sorted_arxiv_ids_no_target_labels_not_representative'}
    if full_enrichment:
        mission['query']['openalex_enrich_arxiv_ids'].pop('max_batches')
    text = json.dumps(mission, ensure_ascii=False, indent=2)
    output.mkdir(parents=True, exist_ok=True)
    frozen = output / 'mission.json'
    if frozen.exists() and frozen.read_text() != text:
        raise ValueError('Настройки пилота отличаются: используйте новый каталог, старый сохранён.')
    if (output / 'manifest.json').exists():
        raise ValueError('Пилот с manifest не перезаписываем: выберите новый каталог.')
    if not frozen.exists(): frozen.write_text(text)
    manifest = {'mission_id': mission_id, 'query_version': mission['query_version'],
                'connector_version': fetch.CONNECTOR_VERSION, 'mission_snapshot_file': 'mission.json',
                'mission_file_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'fetch_started_utc': fetch.now_utc(), 'sources': {}, 'source_errors': None,
                'input_base_manifest_sha256': mission['base_package']['manifest_sha256'],
                **({'local_audit': base_manifest['local_audit']}
                   if full_enrichment and base_manifest.get('local_audit') is not None else {})}
    if full_enrichment:
        # Copies, not hardlinks: changing any future derived file cannot
        # change the baseline bytes. Existing interrupted copies are verified.
        arxiv_output = output / 'arxiv'
        arxiv_output.mkdir(exist_ok=True)
        for entry in base_manifest['sources']['arxiv']['files']:
            filename = entry['file']
            target = arxiv_output / filename
            if target.exists():
                if fetch.sha256_of(target) != entry['sha256']:
                    raise ValueError('Производная копия arXiv изменена: используйте новый каталог.')
            else:
                shutil.copyfile(base_root / 'arxiv' / filename, target)
        quarantine = base_manifest['sources']['arxiv'].get('record_quarantine')
        if quarantine:
            filename = quarantine['file']
            if Path(filename).name != filename:
                raise ValueError('Некорректный путь журнала карантина в базовом пакете.')
            source = base_root / filename
            target = output / filename
            if not source.is_file() or fetch.sha256_of(source) != quarantine['sha256']:
                raise ValueError('Журнал карантина базового пакета не прошёл проверку.')
            if target.exists():
                if fetch.sha256_of(target) != quarantine['sha256']:
                    raise ValueError('Производная копия журнала карантина изменена.')
            else:
                shutil.copyfile(source, target)
        manifest['sources']['arxiv'] = {**base_manifest['sources']['arxiv'],
            'reused_from_base_manifest_sha256': mission['base_package']['manifest_sha256']}
    error = None
    try:
        files = fetch.fetch_openalex_arxiv_enrichment(mission, base_root / 'arxiv', output / 'openalex', None, False)
    except fetch.FetchError as failed:
        files, error = [], fetch.safe_http_text(failed)
    source = {'api': fetch.OPENALEX_API,
              'access_mode': 'arxiv-doi-enrichment-cursor-audited' if full_enrichment else 'arxiv-doi-enrichment-budget-pilot',
              'independent_discovery': False, 'record_shape': 'full', 'files': files,
              'total_records': sum(f.get('records', 0) for f in files),
              'requested_ids': sum(f.get('requested_ids', 0) for f in files),
              'role': 'metadata_enrichment_not_independent_discovery' if full_enrichment else 'connector_and_metadata_coverage_probe_not_detector_input',
              'field_coverage': 'unknown', 'temporal_comparable': False,
              'historical_affiliation_scope': 'current_bibliography_not_as_of_reconstructed'}
    manifest['sources']['openalex'] = source
    if error: manifest['source_errors'] = {'openalex': error}
    reasons = [f['reason'] for f in files if f.get('incomplete')]
    manifest['incomplete'] = {'openalex': reasons} if reasons else None
    manifest['fetch_finished_utc'] = fetch.now_utc()
    rows = []
    for f in files:
        if f.get('file'): rows.extend(json.loads((output / 'openalex' / f['file']).read_bytes())['results'])
    report = {'mission_id': mission_id, 'requested_ids': source['requested_ids'],
              'returned_records': len(rows), 'unique_openalex_work_ids': len({r['id'] for r in rows}),
              'unique_returned_dois': len({r.get('doi') for r in rows if r.get('doi')}),
              'works_with_author_ids': sum(any((a.get('author') or {}).get('id') for a in (r.get('authorships') or [])) for r in rows),
              'works_with_institutions': sum(any(a.get('institutions') for a in (r.get('authorships') or [])) for r in rows),
              'partial': bool(reasons or error), 'source_errors': manifest['source_errors'],
              'scope': 'all_base_arxiv_dois' if full_enrichment else 'first_sorted_arxiv_dois_budget_probe',
              'limitations': ['OpenAlex DOI enrichment is not independent discovery; base-field coverage is not established.',
                              'Current authorship identities/affiliations are not verified historical independence.',
                              'Missing DOI matches are source coverage gaps, not negative scientific labels.',
                              'No analysis, embeddings, statuses or expert labels were changed.']}
    # Exclusive writes preserve completed pilot reports. Interrupted pages can
    # be reused before a manifest exists; a finished probe needs a new directory.
    with (output / 'coverage-report.json').open('x') as handle: handle.write(json.dumps(report, ensure_ascii=False, indent=2))
    with (output / 'manifest.json').open('x') as handle: handle.write(json.dumps(manifest, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description='Ограниченный пилот OpenAlex, не полный аналитический корпус')
    parser.add_argument('base_package', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--max-batches', type=int, default=2)
    parser.add_argument('--batch-size', type=int, default=50)
    parser.add_argument('--full-enrichment', action='store_true',
                        help='Обогатить все ID и создать рядом новый пакет arXiv+OpenAlex; лимит max-batches не применяется')
    args = parser.parse_args()
    report = collect(args.base_package, args.out, args.max_batches, args.batch_size, args.full_enrichment)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report['source_errors'] else 4 if report['partial'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
